"""O inspetor e as travas contra uma página de VERDADE, num Chromium de verdade.

Estes testes moravam no fim de `tests/test_navegador.py`, atrás de um
`importorskip` no topo do módulo. O efeito colateral era caro: numa máquina sem
playwright, o `skip` levava junto as travas determinísticas do mesmo arquivo —
as que recusam campo de senha e upload — e a suíte ficava verde sem tê-las
rodado.

Separados, cada grupo diz do que precisa:

    pytest -m browser                     estes (exige `playwright install chromium`)
    pytest -m "not browser"               a CI unitária

O que se prova aqui e não dá para provar com dublê: que o `input[type=password]`
de um DOM real é reconhecido como senha, e que a recusa acontece contra a
página, não contra um dicionário que o teste montou.
"""

from __future__ import annotations

import pytest

from james.browser.actions import AcaoRecusada

pytestmark = pytest.mark.browser

pw = pytest.importorskip("playwright.sync_api", reason="playwright não instalado")

PAGINA_RUIM = """
<!doctype html><html><head><title>T</title></head><body>
<h2>sem h1</h2><h5>pulo</h5>
<img src="a.png"><a href="#">clique aqui</a><button></button>
<form><input type="text" name="email"><input type="password" name="senha"></form>
</body></html>
"""


@pytest.fixture(scope="module")
def pagina():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            navegador = p.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        except Exception:                       # noqa: BLE001
            navegador = p.chromium.launch()
        pg = navegador.new_page()
        pg.set_content(PAGINA_RUIM)
        yield pg
        navegador.close()


def test_inspetor_acha_os_defeitos_reais(pagina):
    from james.browser.inspector import inspecionar

    r = inspecionar(pagina)
    msgs = " | ".join(a["mensagem"] for a in r["achados"])
    assert "não tem <h1>" in msgs
    assert "sem atributo alt" in msgs
    assert "sem nome acessível" in msgs
    assert "texto vago" in msgs
    assert "pula de h2 para h5" in msgs


def test_o_grave_vem_primeiro(pagina):
    """Quem ouve o relatório em voz alta cansa antes do fim da lista."""
    from james.browser.inspector import inspecionar

    ordem = [a["gravidade"] for a in inspecionar(pagina)["achados"]]
    assert ordem.index("alto") < len(ordem)
    assert "baixo" not in ordem[: ordem.count("alto")]


def test_o_inventario_de_formulario_permite_o_passo_seguinte(pagina):
    """Achar o problema não serve se não dá para apontar o elemento depois."""
    from james.browser.inspector import inspecionar

    forms = inspecionar(pagina)["formularios"]
    assert forms and all(c["seletor"] for c in forms[0]["campos"])


def test_preencher_de_verdade_respeita_a_trava(pagina):
    """O teste que fecha o círculo: página real, campo real, recusa real."""
    from james.browser.actions import preencher

    assert "Preenchi" in preencher(pagina, "input[name=email]", "a@b.com")
    with pytest.raises(AcaoRecusada):
        preencher(pagina, "input[name=senha]", "segredo")
