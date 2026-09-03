"""A rede do navegador é uma superfície própria. `safe_http` não a cobre.

## Por que não dá para reaproveitar o `safe_http`

O `safe_http` protege as requisições que o *Python* faz: ele resolve o nome,
olha o IP, decide, e só então conecta. Nada disso acontece quando o Chromium
navega — quem resolve o DNS e abre o soquete é o navegador, num processo
separado, e o Python nunca vê o IP.

Pior: uma única `page.goto()` dispara dezenas de requisições que o James nunca
pediu — imagens, scripts, `fetch` do próprio site, iframes. Validar só a URL
que o modelo digitou protege exatamente uma das dezenas. Uma página pública
pode buscar `http://127.0.0.1:8080/admin` num `<img>` e o navegador vai lá.

## Onde a trava mora

Em `page.route("**/*")`, que o Chromium consulta antes de CADA requisição —
navegação, subrecurso e cada salto de um redirecionamento. É o único ponto que
vê tudo.

## O que isto pega, e o que não pega

Pega: esquema proibido, IP literal privado/loopback/link-local, endereço de
metadados de nuvem, nome que RESOLVE para IP privado, e a mesma conferência em
cada salto de redirecionamento e em cada subrecurso.

**Não pega DNS rebinding**, e é honesto dizer: entre a nossa resolução e a do
navegador existe uma janela, e um servidor hostil pode devolver IP público para
nós e privado para ele. Fechar isso exigiria um proxy que fizesse a conexão —
está anotado como trabalho futuro. O que existe aqui já barra o caso comum, que
é uma página buscando um endereço interno de propósito.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from james.logs import audit, get_logger

logger = get_logger("james.browser.rede")

# `data:` e `blob:` ficam de fora da conferência de host (não têm host) mas
# entram na lista porque uma página normal os usa o tempo todo para imagem.
ESQUEMAS_PERMITIDOS = frozenset({"http", "https", "data", "blob", "about"})

# Nunca, por nenhuma configuração. `file:` lê o disco; `javascript:` executa no
# contexto da página, o que transformaria uma navegação em execução de código.
ESQUEMAS_PROIBIDOS = frozenset({
    "file", "javascript", "chrome", "chrome-extension", "devtools", "view-source",
})

# Endereços de metadados das nuvens. Sem autenticação, devolvem credenciais.
METADADOS = frozenset({
    "169.254.169.254",      # AWS, Azure, GCP, DigitalOcean
    "metadata.google.internal",
    "100.100.100.100",      # Alibaba
})

_HOSTS_LOCAIS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


class RedeBloqueada(RuntimeError):
    """A requisição não pode sair. Não é falha de rede: é recusa."""


@dataclass(frozen=True)
class Veredito:
    permitido: bool
    motivo: str = ""

    def __bool__(self) -> bool:
        return self.permitido


PERMITIDO = Veredito(True)


def _ip_interno(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """O motivo, se este IP for interno. Vazio se puder sair."""
    if str(ip) in METADADOS:
        return "endereço de metadados da nuvem"
    if ip.is_loopback:
        return "loopback"
    if ip.is_private:
        return "rede interna"
    if ip.is_link_local:
        return "link-local"
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return "endereço reservado"

    # IPv6 embrulhando IPv4 (`::ffff:127.0.0.1`) burla toda checagem acima:
    # como IPv6 ele não é loopback nem privado, e o navegador conecta em 127.
    mapeado = getattr(ip, "ipv4_mapped", None)
    if mapeado is not None:
        return _ip_interno(mapeado)
    return ""


def _resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    return list({info[4][0] for info in infos})


def avaliar(url: str, *, resolver_dns: bool = True) -> Veredito:
    """Esta URL pode ser buscada pelo navegador?

    `resolver_dns=False` existe para o teste e para quem já resolveu antes —
    não para desligar a trava: as conferências de esquema e de IP literal
    continuam valendo.
    """
    if not url:
        return Veredito(False, "URL vazia")

    try:
        partes = urlparse(url)
    except ValueError:
        return Veredito(False, "URL malformada")

    esquema = (partes.scheme or "").lower()
    if esquema in ESQUEMAS_PROIBIDOS:
        return Veredito(False, f"esquema '{esquema}' não é permitido")
    if esquema and esquema not in ESQUEMAS_PERMITIDOS:
        return Veredito(False, f"esquema '{esquema}' desconhecido")
    if esquema in ("data", "blob", "about"):
        return PERMITIDO

    host = (partes.hostname or "").strip().lower().rstrip(".")
    if not host:
        return Veredito(False, "URL sem host")

    if host in _HOSTS_LOCAIS or host.endswith(".localhost"):
        return Veredito(False, "loopback")
    if host in METADADOS:
        return Veredito(False, "endereço de metadados da nuvem")

    # IP literal: decide na hora, sem DNS.
    try:
        return _veredito_de_ip(ipaddress.ip_address(host))
    except ValueError:
        pass

    if not resolver_dns:
        return PERMITIDO

    enderecos = _resolver(host)
    if not enderecos:
        # Não resolver é problema de rede, não motivo de bloqueio: dizer
        # "endereço interno" para um site fora do ar seria uma mentira, e a
        # pessoa iria procurar um problema de segurança que não existe.
        return PERMITIDO

    for bruto in enderecos:
        try:
            ip = ipaddress.ip_address(bruto)
        except ValueError:
            continue
        motivo = _ip_interno(ip)
        if motivo:
            # Um nome público apontando para IP privado é o formato clássico
            # do ataque — e o único jeito de ver isso é resolvendo.
            return Veredito(False, f"{host} resolve para {ip} ({motivo})")
    return PERMITIDO


def _veredito_de_ip(ip) -> Veredito:
    motivo = _ip_interno(ip)
    return Veredito(False, motivo) if motivo else PERMITIDO


def exigir(url: str, *, resolver_dns: bool = True) -> None:
    veredito = avaliar(url, resolver_dns=resolver_dns)
    if not veredito:
        raise RedeBloqueada(
            f"Não acesso esse endereço: {veredito.motivo}."
        )


def instalar(pagina, *, ao_bloquear=None) -> None:
    """Liga a política nesta página, para TODA requisição que ela fizer.

    Vale para navegação, subrecurso e cada salto de redirecionamento — é o
    único ponto onde os três passam.
    """
    bloqueadas = {"n": 0}

    def _rota(rota, requisicao):
        url = requisicao.url
        veredito = avaliar(url)
        if veredito:
            try:
                rota.continue_()
            except Exception:  # noqa: BLE001 — página fechou no meio
                pass
            return

        bloqueadas["n"] += 1
        # Na trilha vai o HOST e o motivo, nunca a URL inteira: caminho e
        # query carregam token de sessão e id de pedido.
        audit(
            "navegador_rede_bloqueada",
            host=(urlparse(url).hostname or "")[:80],
            motivo=veredito.motivo,
            tipo=requisicao.resource_type,
        )
        logger.info("Bloqueado no navegador: %s (%s)", veredito.motivo, requisicao.resource_type)
        if ao_bloquear is not None:
            ao_bloquear(veredito.motivo, requisicao.resource_type)
        try:
            rota.abort("blockedbyclient")
        except Exception:  # noqa: BLE001
            pass

    pagina.route("**/*", _rota)
