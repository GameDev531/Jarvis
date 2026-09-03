"""O Ultron com as mãos no Chrome — e as travas que não abrem.

Só o que roda sem navegador nenhum. A parte que abre um Chromium de verdade
está em tests/integration/test_navegador_real.py — antes ela morava aqui, com
um `importorskip` no topo do módulo, e isso PULAVA O ARQUIVO INTEIRO numa
máquina sem playwright: as travas determinísticas abaixo, que são justamente
as que não podem falhar, simplesmente não rodavam e ninguém via.

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
