"""Roteador local: casar errado é caro, não casar é barato."""

import pytest

from james.llm.router import LocalRouter

APPS = {"chrome": "chrome.exe", "bloco de notas": "notepad.exe", "spotify": "spotify.exe"}


@pytest.fixture
def router():
    return LocalRouter(APPS)


@pytest.mark.parametrize(
    "fala",
    ["que horas são", "que horas sao", "que hora é", "me diz que horas são", "as horas"],
)
def test_pergunta_de_hora(router, fala):
    match = router.match(fala)
    assert match is not None and match.tool == "que_horas_sao"


@pytest.mark.parametrize(
    "fala,acao",
    [
        ("aumenta o volume", "aumentar"),
        ("sobe o som", "aumentar"),
        ("diminui o volume", "diminuir"),
        ("abaixa o som", "diminuir"),
        ("muta", "mutar"),
        ("tira o som", "mutar"),
    ],
)
def test_comandos_de_volume(router, fala, acao):
    match = router.match(fala)
    assert match is not None
    assert match.tool == "ajustar_volume" and match.args["acao"] == acao


@pytest.mark.parametrize(
    "fala,nome",
    [
        ("abre o chrome", "chrome"),
        ("abrir chrome", "chrome"),
        ("abra o bloco de notas", "bloco de notas"),
        ("inicia o spotify", "spotify"),
    ],
)
def test_abrir_app_da_whitelist(router, fala, nome):
    match = router.match(fala)
    assert match is not None
    assert match.tool == "abrir_app" and match.args["nome"] == nome


@pytest.mark.parametrize(
    "fala",
    [
        "abre a janela pra ventilar",   # 'abre' sem app conhecido
        "abre o photoshop",             # app real mas fora da whitelist
        "abra a porta",
    ],
)
def test_abrir_algo_que_nao_e_app_conhecido_vai_pro_llm(router, fala):
    """Na dúvida, não casa: quem resolve ambiguidade é o modelo."""
    assert router.match(fala) is None


def test_busca_preserva_acentuacao_original(router):
    match = router.match("pesquisa sobre São Paulo")
    assert match is not None
    assert match.tool == "pesquisar_web"
    assert match.args["query"] == "São Paulo"


@pytest.mark.parametrize(
    "fala",
    [
        "abre o chrome e pesquisa sobre marte",
        "pesquisa marte e depois abre o spotify",
        "aumenta o volume e abre o chrome",
    ],
)
def test_comando_composto_vai_pro_llm(router, fala):
    """Encadear ações é trabalho do modelo, não do roteador."""
    assert router.match(fala) is None


@pytest.mark.parametrize(
    "fala",
    [
        "me conta uma piada",
        "o que você acha do projeto",
        "como foi seu dia",
        "explica o que é um buraco negro",
    ],
)
def test_conversa_normal_nao_casa(router, fala):
    assert router.match(fala) is None


@pytest.mark.parametrize("fala", [None, "", "   ", ".", "???"])
def test_entrada_vazia_ou_lixo(router, fala):
    assert router.match(fala) is None


def test_pontuacao_final_nao_impede_o_casamento(router):
    assert router.match("que horas são?") is not None
    assert router.match("abre o chrome.") is not None


def test_roteador_desligado_nunca_casa():
    desligado = LocalRouter(APPS, enabled=False)
    assert desligado.match("que horas são") is None


def test_busca_curta_demais_nao_casa(router):
    """'pesquisa a' não é um comando de busca utilizável."""
    assert router.match("pesquisa a") is None


def test_whitelist_vazia_nao_casa_abrir_app():
    vazio = LocalRouter({})
    assert vazio.match("abre o chrome") is None
