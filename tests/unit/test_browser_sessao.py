"""Identidade de aba: quem é "a página" quando o James vai clicar.

Antes, toda ação mutável ia para `contexto.pages[-1]`. O defeito não é sutil,
mas o modo como ele falhava era: **funcionava quase sempre.** Com uma aba só,
a última é a certa. O erro só aparecia com várias abas abertas — ou seja,
exatamente quando alguém está trabalhando de verdade, e exatamente quando um
clique no lugar errado custa caro.

Um teste com uma aba só teria passado com o código velho. Por isso quase tudo
aqui monta mais de uma.
"""

from __future__ import annotations

import pytest

from james.browser.sessao import (
    AbaDesconhecida,
    AlvoAusente,
    BrowserSession,
    dominio,
)


class PaginaFalsa:
    """O suficiente para a sessão: fecha, tem URL e título."""

    def __init__(self, url="https://exemplo.com/", titulo="Exemplo"):
        self._url = url
        self._titulo = titulo
        self._fechada = False

    def is_closed(self):
        return self._fechada

    def fechar(self):
        self._fechada = True

    @property
    def url(self):
        if self._fechada:
            raise RuntimeError("página fechada")
        return self._url

    def title(self):
        if self._fechada:
            raise RuntimeError("página fechada")
        return self._titulo


@pytest.fixture
def sessao():
    return BrowserSession()


# --------------------------------------------------------------- identidade


def test_cada_aba_ganha_um_id_proprio(sessao):
    a, b = PaginaFalsa(), PaginaFalsa()
    sessao.sincronizar([a, b])
    ids = {aba.tab_id for aba in sessao.abas}
    assert len(ids) == 2


def test_a_mesma_pagina_nao_ganha_id_novo(sessao):
    """Sincronizar é chamado a toda listagem; se ele renumerasse, o id que o
    modelo acabou de receber apontaria para outra coisa no turno seguinte."""
    a = PaginaFalsa()
    sessao.sincronizar([a])
    primeiro = sessao.abas[0].tab_id
    sessao.sincronizar([a])
    sessao.sincronizar([a])
    assert sessao.abas[0].tab_id == primeiro


def test_id_de_aba_fechada_nunca_e_reaproveitado(sessao):
    """A regra que impede o pior caso.

    Se a aba 2 fecha e a próxima também for 2, uma referência que o modelo
    guardou aponta para uma página completamente diferente — o mesmo erro de
    alvo que este módulo existe para acabar, só que mais difícil de ver.
    """
    a, b = PaginaFalsa(), PaginaFalsa()
    sessao.sincronizar([a, b])
    ids_antigos = {aba.tab_id for aba in sessao.abas}

    b.fechar()
    sessao.sincronizar([a, b])

    c = PaginaFalsa()
    sessao.sincronizar([a, c])
    novo = next(aba.tab_id for aba in sessao.abas if aba.page is c)
    assert novo not in ids_antigos


def test_aba_fechada_some_da_listagem(sessao):
    a, b = PaginaFalsa(), PaginaFalsa()
    sessao.sincronizar([a, b])
    b.fechar()
    sessao.sincronizar([a, b])
    assert len(sessao.abas) == 1


def test_a_aba_que_o_usuario_abriu_tambem_ganha_id(sessao):
    """Se o registro só soubesse das abas que o James abriu, a listagem
    mostraria metade do navegador — e a metade invisível é onde você trabalha."""
    minha = PaginaFalsa(url="https://banco.com/")
    sessao.sincronizar([minha])
    assert sessao.abas[0].tab_id


# ------------------------------------------------------------- a resolução


def test_agir_sem_dizer_a_aba_e_recusado(sessao):
    """O coração da correção. Se isto algum dia devolver um palpite,
    `pages[-1]` voltou com outro nome."""
    sessao.sincronizar([PaginaFalsa(), PaginaFalsa()])
    with pytest.raises(AlvoAusente):
        sessao.exigir(None)
    with pytest.raises(AlvoAusente):
        sessao.exigir("")


def test_agir_exige_a_aba_mesmo_havendo_so_uma(sessao):
    """Ler pode ter padrão; agir, não — nem no caso fácil.

    Deixar passar com uma aba criaria um hábito que quebra em silêncio no dia
    em que aparecer a segunda.
    """
    sessao.sincronizar([PaginaFalsa()])
    with pytest.raises(AlvoAusente):
        sessao.exigir(None)


def test_id_inexistente_diz_quais_existem(sessao):
    sessao.sincronizar([PaginaFalsa(), PaginaFalsa()])
    with pytest.raises(AbaDesconhecida) as erro:
        sessao.exigir("99")
    assert "1" in str(erro.value) and "2" in str(erro.value)


def test_aba_fechada_nao_resolve_para_a_mais_proxima(sessao):
    """Adivinhar aqui seria reintroduzir o bug com cara de conveniência."""
    a, b = PaginaFalsa(), PaginaFalsa()
    sessao.sincronizar([a, b])
    alvo = sessao.abas[1].tab_id
    b.fechar()

    with pytest.raises(AbaDesconhecida):
        sessao.exigir(alvo)


def test_ler_aceita_padrao_quando_ha_uma_aba_so(sessao):
    """A assimetria é o ponto: ler a aba errada gasta uma leitura; clicar na
    aba errada compra uma passagem."""
    a = PaginaFalsa()
    sessao.sincronizar([a])
    assert sessao.para_leitura(None).page is a


def test_ler_com_varias_abas_volta_a_exigir_o_numero(sessao):
    sessao.sincronizar([PaginaFalsa(), PaginaFalsa()])
    with pytest.raises(AlvoAusente):
        sessao.para_leitura(None)


def test_ler_sem_aba_nenhuma_diz_isso(sessao):
    with pytest.raises(AbaDesconhecida):
        sessao.para_leitura(None)


def test_a_aba_selecionada_vira_o_padrao_de_leitura(sessao):
    a, b = PaginaFalsa(), PaginaFalsa()
    sessao.sincronizar([a, b])
    sessao.selecionar(sessao.abas[1].tab_id)
    assert sessao.para_leitura(None).page is b


def test_selecionar_aba_que_fecha_volta_a_exigir_escolha(sessao):
    a, b = PaginaFalsa(), PaginaFalsa()
    sessao.sincronizar([a, b])
    sessao.selecionar(sessao.abas[1].tab_id)
    b.fechar()
    sessao.sincronizar([a, b])
    assert sessao.para_leitura(None).page is a      # sobrou uma


# ------------------------------------------------------------- o que sai


def test_a_listagem_nao_leva_a_url_inteira(sessao):
    """URL carrega token de sessão, id de pedido, termo de busca — e a
    listagem vai inteira para o histórico do modelo."""
    sessao.sincronizar([
        PaginaFalsa(url="https://banco.com/conta?token=SEGREDO123&id=4451")
    ])
    item = sessao.listar()[0]
    assert item["dominio"] == "banco.com"
    assert "SEGREDO123" not in str(item)
    assert "4451" not in str(item)


def test_a_listagem_marca_a_selecionada(sessao):
    sessao.sincronizar([PaginaFalsa(), PaginaFalsa()])
    escolhida = sessao.abas[1].tab_id
    sessao.selecionar(escolhida)
    marcadas = [i["tab_id"] for i in sessao.listar() if i["selecionada"]]
    assert marcadas == [escolhida]


def test_a_listagem_sai_em_ordem_numerica(sessao):
    """Ordem de texto poria a aba 10 antes da 2, e a pessoa lê em voz alta."""
    sessao.sincronizar([PaginaFalsa() for _ in range(11)])
    numeros = [int(i["tab_id"]) for i in sessao.listar()]
    assert numeros == sorted(numeros)


def test_uma_aba_morta_nao_derruba_a_listagem_das_outras(sessao):
    """Uma aba pode morrer no meio da listagem; a informação das outras
    continua boa e é ela que a pessoa pediu."""
    a, b = PaginaFalsa(), PaginaFalsa()
    sessao.sincronizar([a, b])
    b._fechada = True                    # morre sem passar por sincronizar
    itens = sessao.listar()
    assert len(itens) == 1
    assert itens[0]["dominio"] == "exemplo.com"


def test_dominio_de_url_quebrada_nao_estoura():
    assert dominio("nao é uma url") == ""
    assert dominio("") == ""
