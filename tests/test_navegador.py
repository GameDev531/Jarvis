"""O Ultron com as mãos no Chrome — e as travas que não abrem.

Preencher formulário é a ação mais perigosa que um assistente de voz faz
sozinho, e o motivo não é o óbvio. O risco não é digitar errado: é digitar no
CAMPO ERRADO. `input[type=password]` e `input[type=text]` são irmãos no DOM, e
a distância entre preencher um formulário e vazar uma credencial para o
histórico do modelo é um atributo.

Por isso a recusa é determinística e mora em `james/browser/actions.py`, não no
prompt. Um modelo convencido a "só desta vez" não passa daqui, porque não é ele
quem decide.
"""

from __future__ import annotations

import pytest

from james.browser.actions import AcaoRecusada, conferir_campo
from james.config import Config
from james.permissions.guard import Decision, Guard


# ------------------------------------------------------- o que nunca passa


@pytest.mark.parametrize("tipo", ["password", "file", "hidden"])
def test_tipo_proibido_e_recusado(tipo):
    with pytest.raises(AcaoRecusada):
        conferir_campo({"tipo": tipo, "nome": "qualquer", "seletor": "#x"})


@pytest.mark.parametrize("nome", [
    "senha", "senha_atual", "novaSenha", "user-password", "passwd",
    "numero_cartao", "cc-number", "creditCard", "cvv", "cvc",
    "cpf", "cnpj", "otp", "api_key", "secret_token",
])
def test_nome_sensivel_e_recusado_mesmo_em_campo_de_texto(nome):
    """Um site pode marcar o campo de senha como `type=text` — por descuido ou
    de propósito. O tipo sozinho não basta."""
    with pytest.raises(AcaoRecusada):
        conferir_campo({"tipo": "text", "nome": nome, "seletor": "#x"})


@pytest.mark.parametrize("nome", ["email", "nome_completo", "assunto", "cidade", "busca"])
def test_campo_comum_passa(nome):
    """A trava não pode ser tão larga que o recurso deixe de existir."""
    conferir_campo({"tipo": "text", "nome": nome, "seletor": "#x"})


@pytest.mark.parametrize("nome", ["api_key", "api-key", "api key", "cc_number", "ccnumber"])
def test_separador_nao_abre_buraco(nome):
    """O primeiro `_PADRAO_SENSIVEL` usava "-?" e deixava passar `api_key`.

    O campo é o mesmo campo; muda o sublinhado. Buraco assim não aparece em
    teste feliz — aparece no dia em que o site usa a outra convenção.
    """
    with pytest.raises(AcaoRecusada):
        conferir_campo({"tipo": "text", "nome": nome})


def test_erra_para_o_lado_de_recusar():
    """`tokenizer` cai na trava por causa de "token", e está certo assim.

    Numa heurística sobre nome de campo, os dois erros não custam igual: negar
    um campo inofensivo custa "digite o senhor mesmo"; deixar passar um campo
    de credencial custa a credencial. Só `pin` ganhou borda de palavra, porque
    ali o falso positivo era comum demais ("pinturas", "pincel").
    """
    with pytest.raises(AcaoRecusada):
        conferir_campo({"tipo": "text", "nome": "tokenizer"})
    conferir_campo({"tipo": "text", "nome": "pinturas"})       # não levanta


def test_o_autocomplete_tambem_denuncia():
    """`autocomplete="cc-number"` é o que o navegador usa para preencher
    cartão. Se está lá, o campo é de cartão, chame-se ele como se chamar."""
    with pytest.raises(AcaoRecusada):
        conferir_campo({"tipo": "text", "nome": "n1", "autocomplete": "cc-number"})


def test_o_rotulo_visivel_tambem_conta():
    with pytest.raises(AcaoRecusada):
        conferir_campo({"tipo": "text", "nome": "f1", "rotulo": "Digite sua senha"})


# ------------------------------------------------------------- o guard


@pytest.fixture
def guard():
    return Guard(Config({}))


@pytest.mark.parametrize("tool", ["listar_abas", "inspecionar_pagina"])
def test_ler_e_nivel_1(guard, tool):
    """Inspecionar não muda nada. Pedir confirmação para ler treina a pessoa a
    dizer sim sem ler — e aí a confirmação que importa também passa batido."""
    assert guard.evaluate(tool, {}).decision is Decision.ALLOW


@pytest.mark.parametrize("tool", ["preencher_campo", "clicar_em"])
def test_agir_e_nivel_2(guard, tool):
    """Um clique pode confirmar uma compra. O guard não sabe qual botão é qual
    — a página é de terceiro e o rótulo pode mentir."""
    veredito = guard.evaluate(tool, {"seletor": "#enviar", "valor": "x"})
    assert veredito.decision is Decision.CONFIRM
    assert "#enviar" in veredito.spoken


def test_abrir_aba_usa_a_mesma_validacao_de_url(guard):
    """Uma segunda porta para a mesma casa é a que alguém esquece de trancar."""
    assert guard.evaluate("abrir_aba", {"url": "file:///etc/passwd"}).decision is Decision.BLOCK
    assert guard.evaluate("abrir_aba", {"url": "http://127.0.0.1:8080"}).decision is Decision.BLOCK
    assert guard.evaluate("abrir_aba", {"url": "https://exemplo.com"}).decision is Decision.ALLOW


# --------------------------------------------------------------- o modo


def test_modo_navegador_e_sensivel():
    """Anexado ao seu Chrome, ele enxerga as abas abertas — onde estão o
    e-mail, o banco e o trabalho. Mais íntimo que a webcam, não menos."""
    from james.modes.browser import BrowserMode

    assert BrowserMode.sensivel is True
    assert "navegador" in BrowserMode.recursos


def test_ferramenta_sem_modo_ligado_explica_o_que_fazer():
    from james.modes.base import ModeError
    from james.modes.browser import BrowserMode

    with pytest.raises(ModeError, match="desligado"):
        BrowserMode().exigir_driver()


# ------------------------------------------- inspetor, contra página real

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
