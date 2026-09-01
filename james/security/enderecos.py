"""Um endereço pode ser alcançado? A resposta vale para o guard E para o fetch.

Este arquivo existe porque a validação estava em dois lugares — e num deles
não estava.

O guard barrava `http://127.0.0.1` corretamente. Só que ele testava o host com
`ipaddress.ip_address()`, que entende IP LITERAL e mais nada: num nome de
domínio a função levanta `ValueError`, e o código lia isso como "não é IP
proibido, pode passar". Então:

    http://127.0.0.1:8080          barrado
    http://meusite.com  ->  127.0.0.1     PASSAVA
    página pública que redireciona para 127.0.0.1     PASSAVA

O segundo caso não precisa nem de má-fé de terceiro: qualquer um registra um
domínio apontando para 127.0.0.1. O terceiro era pior, porque o `httpx` seguia
o redirecionamento sozinho, com `follow_redirects=True`, muito depois de o
guard ter aprovado a primeira URL.

O que estava exposto: a interface holográfica em 127.0.0.1, o roteador da casa,
impressoras, câmeras, NAS, e o serviço de metadados de nuvem em 169.254.169.254
— tudo alcançável por uma frase falada e uma página maliciosa.

A regra agora é uma só, aqui, usada nos dois lugares. Duas cópias de uma regra
de segurança viram uma cópia desatualizada.
"""

from __future__ import annotations

import ipaddress
import socket

from james.logs import get_logger

logger = get_logger("james.security.enderecos")


class EnderecoBloqueado(RuntimeError):
    """O endereço aponta para algo que não deveria ser alcançado."""


class NaoResolveu(EnderecoBloqueado):
    """O nome não resolveu. Continua barrando — mas por outro motivo.

    Subclasse de propósito: quem só quer saber "posso ir?" continua tratando
    igual, e a resposta segue sendo não. Não dá para garantir que um nome é
    externo sem saber para onde ele aponta, então falhar fechado é o certo.

    O que muda é a FRASE. Dizer "recusado por segurança" quando o Wi-Fi caiu
    é gritar lobo: quem ouve isso três vezes por engano ignora na quarta, que
    é justamente quando havia lobo.
    """


def motivo_ip_bloqueado(host: str) -> str | None:
    """Por que este IP LITERAL não pode ser alcançado. `None` = pode.

    Devolve `None` também para nome de domínio — quem resolve o nome é
    `validar_host`. Separar os dois é o que evita a confusão original entre
    "não é IP proibido" e "não é IP".
    """
    try:
        endereco = ipaddress.ip_address(host)
    except ValueError:
        return None

    if endereco.is_loopback:
        return f"'{host}' é loopback"
    if endereco.is_private:
        return f"'{host}' é endereço de rede privada"
    if endereco.is_link_local:
        # Inclui 169.254.169.254, o serviço de metadados de nuvem.
        return f"'{host}' é link-local (metadados de nuvem)"
    if endereco.is_reserved or endereco.is_unspecified or endereco.is_multicast:
        return f"'{host}' é endereço reservado"
    return None


def resolver(host: str) -> list[str]:
    """Todos os IPs para os quais este nome aponta.

    Todos, e não o primeiro: um nome pode devolver vários registros, e barrar
    só o primeiro deixaria a porta aberta para o segundo.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise NaoResolveu(f"não consegui resolver '{host}' ({exc})") from exc
    return sorted({info[4][0] for info in infos})


def validar_host(host: str, *, resolver_dns: bool = True) -> None:
    """Levanta `EnderecoBloqueado` se o host não puder ser alcançado.

    Confere o host como IP literal e, se for um nome, resolve e confere CADA
    endereço que ele devolve.
    """
    host = (host or "").strip().strip("[]")
    if not host:
        raise EnderecoBloqueado("Endereço sem host.")

    motivo = motivo_ip_bloqueado(host)
    if motivo:
        raise EnderecoBloqueado(motivo)

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass          # é um nome; segue para a resolução
    else:
        return        # era IP literal e já passou

    if not resolver_dns:
        return

    for endereco in resolver(host):
        motivo = motivo_ip_bloqueado(endereco)
        if motivo:
            raise EnderecoBloqueado(f"'{host}' aponta para {motivo}")
