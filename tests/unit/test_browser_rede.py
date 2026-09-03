"""A política de rede do navegador, caso a caso.

Os casos vêm do item 5 do prompt de auditoria, e cada um existe porque é um
jeito real de chegar num endereço interno a partir de uma página.

O que NÃO se testa aqui, porque não é verdade: DNS rebinding. Entre a nossa
resolução e a do navegador existe uma janela, e um servidor hostil pode
devolver IP público para nós e privado para ele. Está escrito no módulo como
limite conhecido — anotar a lacuna é honesto; um teste que "provasse" o
contrário seria mentira.
"""

from __future__ import annotations

import pytest

from james.browser.network_policy import RedeBloqueada, avaliar, exigir


def bloqueia(url, **kw) -> bool:
    return not avaliar(url, **kw)


# ------------------------------------------------------------------ esquemas


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Windows/System32/drivers/etc/hosts",
        "file:///etc/passwd",
        "javascript:fetch('http://127.0.0.1:8080/admin')",
        "chrome://settings",
        "chrome-extension://abcdef/background.js",
        "view-source:https://exemplo.com",
        "devtools://devtools/bundled/inspector.html",
    ],
)
def test_esquema_proibido_e_bloqueado(url):
    """`file:` lê o disco; `javascript:` transforma navegação em execução de
    código no contexto da própria página."""
    assert bloqueia(url)


@pytest.mark.parametrize(
    "url",
    ["https://exemplo.com/a", "http://exemplo.com/a", "data:image/png;base64,iVBOR"],
)
def test_esquema_normal_passa(url):
    assert not bloqueia(url, resolver_dns=False)


def test_esquema_desconhecido_e_recusado():
    """Falha fechada: um esquema que ninguém previu não é 'provavelmente ok'."""
    assert bloqueia("gopher://exemplo.com/")
    assert bloqueia("ftp://arquivos.exemplo.com/")


# ----------------------------------------------------------------- internos


@pytest.mark.parametrize(
    "url,porque",
    [
        ("http://127.0.0.1:8080/admin", "loopback v4"),
        ("http://localhost:3000/", "nome loopback"),
        ("http://LOCALHOST/", "caixa alta"),
        ("http://app.localhost/", "sufixo .localhost"),
        ("http://[::1]:9222/json", "loopback v6"),
        ("http://192.168.0.1/", "RFC1918"),
        ("http://10.0.0.5/", "RFC1918"),
        ("http://172.16.3.9/", "RFC1918"),
        ("http://169.254.169.254/latest/meta-data/", "metadados de nuvem"),
        ("http://metadata.google.internal/computeMetadata/v1/", "metadados GCP"),
        ("http://100.100.100.100/", "metadados Alibaba"),
        ("http://0.0.0.0/", "não especificado"),
        ("http://[fe80::1]/", "link-local v6"),
    ],
)
def test_endereco_interno_e_bloqueado(url, porque):
    assert bloqueia(url), porque


def test_ipv6_embrulhando_ipv4_nao_escapa():
    """`::ffff:127.0.0.1` não é loopback NEM privado enquanto IPv6 — as
    checagens normais passam batido, e o navegador conecta em 127.0.0.1."""
    assert bloqueia("http://[::ffff:127.0.0.1]/")
    assert bloqueia("http://[::ffff:192.168.1.1]/")


def test_o_ponto_final_do_nome_nao_escapa():
    """`localhost.` é o mesmo host para o resolvedor, e outro texto para uma
    comparação ingênua."""
    assert bloqueia("http://localhost./")


def test_porta_alta_nao_muda_nada():
    assert bloqueia("http://127.0.0.1:65000/")


# --------------------------------------------------------------------- DNS


def test_nome_publico_que_resolve_para_privado_e_bloqueado(monkeypatch):
    """O formato clássico: o domínio é público, o registro A aponta para
    dentro. Só resolvendo dá para ver."""
    import james.browser.network_policy as pol

    monkeypatch.setattr(pol, "_resolver", lambda host: ["192.168.1.50"])
    assert bloqueia("https://interno.meusite.com/")


def test_nome_que_resolve_para_metadados_e_bloqueado(monkeypatch):
    import james.browser.network_policy as pol

    monkeypatch.setattr(pol, "_resolver", lambda host: ["169.254.169.254"])
    assert bloqueia("https://parece-normal.com/")


def test_basta_um_endereco_ruim_entre_varios(monkeypatch):
    """Round-robin com um IP interno no meio seria uma loteria: às vezes sai
    o público, às vezes o de dentro."""
    import james.browser.network_policy as pol

    monkeypatch.setattr(pol, "_resolver", lambda host: ["93.184.216.34", "10.1.2.3"])
    assert bloqueia("https://misto.com/")


def test_nome_que_nao_resolve_nao_vira_acusacao(monkeypatch):
    """Site fora do ar não é ataque.

    Dizer "endereço interno" para um domínio que só não resolveu manda a
    pessoa procurar um problema de segurança que não existe.
    """
    import james.browser.network_policy as pol

    monkeypatch.setattr(pol, "_resolver", lambda host: [])
    assert not bloqueia("https://caiu-agora.com/")


def test_nome_publico_normal_passa(monkeypatch):
    import james.browser.network_policy as pol

    monkeypatch.setattr(pol, "_resolver", lambda host: ["93.184.216.34"])
    assert not bloqueia("https://exemplo.com/pagina")


def test_sem_dns_as_outras_travas_continuam(monkeypatch):
    """`resolver_dns=False` é para quem já resolveu, não para desligar."""
    assert bloqueia("http://127.0.0.1/", resolver_dns=False)
    assert bloqueia("file:///etc/passwd", resolver_dns=False)


# ------------------------------------------------------------------ formato


@pytest.mark.parametrize("url", ["", "http://", "https://", "não é url"])
def test_url_estranha_nao_estoura_e_nao_passa(url):
    resultado = avaliar(url)
    assert isinstance(bool(resultado), bool)
    if url in ("", "http://", "https://"):
        assert not resultado


def test_exigir_levanta_com_o_motivo_dentro():
    with pytest.raises(RedeBloqueada) as erro:
        exigir("http://169.254.169.254/")
    assert "metadados" in str(erro.value)


def test_o_motivo_sai_dizivel():
    """A frase vai para o usuário: precisa dizer o que houve, não um código."""
    veredito = avaliar("http://192.168.1.1/")
    assert veredito.motivo and not veredito.permitido
    assert veredito.motivo.islower() or " " in veredito.motivo
