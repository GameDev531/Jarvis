"""A política de rede dentro de um Chromium de verdade.

Os testes de unidade provam que `avaliar()` classifica certo. Isso não prova
que a trava PEGA: uma política perfeita instalada no lugar errado deixa passar
tudo, e a suíte fica verde do mesmo jeito.

O que só dá para provar aqui:

  - que um SUBRECURSO é interceptado. Uma `page.goto()` dispara dezenas de
    requisições que o James nunca pediu — imagem, script, fetch, iframe.
    Validar só a URL digitada protege uma das dezenas.
  - que um REDIRECIONAMENTO é reavaliado a cada salto. Uma URL pública que
    redireciona para `127.0.0.1` passa por qualquer validação feita só antes
    do `goto`.

Um servidor HTTP local serve de alvo — ele é loopback, que é justamente o que
a política precisa recusar.

    pytest -m browser
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from james.browser.network_policy import instalar

pytestmark = pytest.mark.browser

pytest.importorskip("playwright.sync_api", reason="playwright não instalado")


class _Alvo(BaseHTTPRequestHandler):
    """Um servidor que não deveria ser alcançado a partir de uma página."""

    def do_GET(self):                                     # noqa: N802
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:%d/segredo" % self.server.server_port)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"SEGREDO_INTERNO")

    def log_message(self, *a):                            # silencia o servidor
        pass


@pytest.fixture(scope="module")
def servidor():
    httpd = HTTPServer(("127.0.0.1", 0), _Alvo)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture
def pagina(navegador):
    pg = navegador.new_page()
    yield pg
    pg.close()


def com_politica(pagina):
    bloqueios = []
    instalar(pagina, ao_bloquear=lambda motivo, tipo: bloqueios.append((motivo, tipo)))
    return bloqueios


# ------------------------------------------------------ o servidor é alcançável


def test_sem_a_politica_o_servidor_responde(pagina, servidor):
    """A contraprova. Sem ela, os testes abaixo poderiam estar passando porque
    o servidor simplesmente não funciona."""
    pagina.goto(servidor + "/segredo")
    assert "SEGREDO_INTERNO" in pagina.content()


# -------------------------------------------------------------- a trava pega


def test_a_navegacao_para_loopback_e_bloqueada(pagina, servidor):
    bloqueios = com_politica(pagina)
    with pytest.raises(Exception):
        pagina.goto(servidor + "/segredo", timeout=5000)
    assert bloqueios, "a política não chegou a ser consultada"
    assert "loopback" in bloqueios[0][0]


def test_um_subrecurso_para_dentro_e_bloqueado(pagina, servidor):
    """O caso que a validação de URL antes do goto NÃO pega.

    A página é pública e inofensiva; quem vai buscar o endereço interno é uma
    `<img>` dela. Sem interceptação, o navegador vai lá e ninguém fica sabendo.
    """
    bloqueios = com_politica(pagina)
    pagina.set_content(
        f'<html><body><img src="{servidor}/segredo" alt="x"></body></html>'
    )
    pagina.wait_for_timeout(400)

    assert bloqueios, "o subrecurso não passou pela política"
    assert any("loopback" in m for m, _ in bloqueios)
    assert any(t == "image" for _, t in bloqueios)


def test_fetch_da_pagina_para_dentro_e_bloqueado(pagina, servidor):
    """O caminho mais direto de exfiltração: a própria página pede."""
    bloqueios = com_politica(pagina)
    pagina.set_content("<html><body>ok</body></html>")

    resultado = pagina.evaluate(
        """async (url) => {
            try {
                const r = await fetch(url);
                return await r.text();
            } catch (e) {
                return 'BLOQUEADO';
            }
        }""",
        servidor + "/segredo",
    )
    assert resultado == "BLOQUEADO"
    assert "SEGREDO_INTERNO" not in str(resultado)
    assert bloqueios


def test_redirecionamento_de_publico_para_interno_e_bloqueado(pagina, servidor):
    """Uma URL pública que redireciona para dentro passa por qualquer
    validação feita só antes do `goto` — o salto acontece depois."""
    bloqueios = com_politica(pagina)
    pagina.set_content("<html><body>ok</body></html>")

    resultado = pagina.evaluate(
        """async (url) => {
            try { const r = await fetch(url); return await r.text(); }
            catch (e) { return 'BLOQUEADO'; }
        }""",
        servidor + "/redirect",
    )
    assert "SEGREDO_INTERNO" not in str(resultado)


def test_pagina_publica_normal_continua_carregando(pagina):
    """Uma trava que bloqueia tudo é fácil e inútil."""
    com_politica(pagina)
    pagina.set_content("<html><body><h1>tudo certo</h1></body></html>")
    assert "tudo certo" in pagina.content()


def test_data_url_continua_funcionando(pagina):
    """Imagem embutida é o pão de cada dia de uma página; barrar `data:`
    quebraria metade da web sem proteger nada."""
    com_politica(pagina)
    pagina.set_content(
        '<html><body><img id="i" src="data:image/gif;base64,'
        'R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="></body></html>'
    )
    pagina.wait_for_timeout(200)
    assert pagina.evaluate("() => document.getElementById('i').complete") is True
