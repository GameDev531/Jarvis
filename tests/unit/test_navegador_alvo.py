"""As ferramentas do navegador exigem alvo explícito — pelo caminho real.

Os testes de `sessao.py` provam que a resolução recusa alvo implícito. Isso não
prova que a FERRAMENTA recusa: uma ferramenta que passasse `tab_id=None` sem
querer, ou que caísse num `except Exception` genérico, transformaria a recusa
em "não consegui" e o modelo tentaria de novo com outro seletor.

Aqui as ferramentas são executadas pelo `ToolRegistry` de verdade, com um
driver dublê no lugar do Chromium — o que se mede é o contrato, não o
navegador.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from james.browser.sessao import BrowserSession
from james.browser.snapshot import (
    CODIGO_PAGINA_MUDOU,
    ElementRef,
    Snapshot,
    Snapshots,
    agora,
)
from james.config import Config
from james.permissions.guard import Guard
from james.tools import navegador as tools_navegador
from james.tools.registry import ToolRegistry


class PaginaFalsa:
    def __init__(self, url="https://loja.com/carrinho"):
        self._url = url
        self.cliques = []
        self.preenchimentos = []
        self.fechada = False

    def is_closed(self):
        return self.fechada

    @property
    def url(self):
        return self._url

    def title(self):
        return "Carrinho"

    def click(self, seletor):
        self.cliques.append(seletor)

    def fill(self, seletor, valor):
        self.preenchimentos.append((seletor, valor))

    def query_selector(self, seletor):
        return object()

    def close(self):
        self.fechada = True

    def evaluate(self, script, arg=None):
        """Responde às três consultas de `snapshot.py`, sempre concordando.

        O dublê CONCORDA de propósito: assim, qualquer recusa que aconteça nos
        testes veio da lógica de alvo, não de uma página fingindo ter mudado.
        """
        if "__james_snapshot" in script and "===" in script:
            return True
        if "querySelectorAll(sel)" in script:
            return {
                "quantos": 1, "papel": "button", "nome": "Comprar",
                "tipo": "", "nome_campo": "", "id_campo": "comprar",
                "autocomplete": "", "rotulo": "", "placeholder": "",
                "visivel": True, "ativo": True,
            }
        return {"url": self._url, "titulo": "Carrinho", "elementos": []}


class DriverFalso:
    def __init__(self, *paginas):
        self.sessao = BrowserSession()
        self.snapshots = Snapshots()
        self.paginas = list(paginas)
        self.sessao.sincronizar(self.paginas)

    def sincronizar(self):
        return self.sessao.sincronizar(self.paginas)

    def abas(self):
        self.sincronizar()
        return self.sessao.listar()

    def aba_para_ler(self, tab_id):
        self.sincronizar()
        return self.sessao.para_leitura(tab_id)

    def aba_para_agir(self, tab_id):
        self.sincronizar()
        return self.sessao.exigir(tab_id)

    def fechar_aba(self, tab_id):
        aba = self.sessao.exigir(tab_id)
        aba.page.close()
        self.sincronizar()
        return f"Fechei a aba {tab_id}."


def snapshot_para(aba, element_id="e1", nome="Comprar", seletor="#comprar"):
    # A origem sai da própria aba. Fixá-la aqui fazia o snapshot do banco
    # nascer dizendo "loja.com" — e a conferência de origem, corretamente,
    # recusava. Um helper de teste que mente sobre o cenário acusa o código.
    from james.browser.snapshot import origem as origem_de

    return Snapshot(
        snapshot_id="snap1",
        tab_id=aba.tab_id,
        origem=origem_de(aba.url),
        marca="m",
        criado_em=agora(),
        elementos={
            element_id: ElementRef(
                element_id=element_id, seletor=seletor, papel="button", nome=nome
            )
        },
        url=aba.url,
        titulo="Carrinho",
    )


@pytest.fixture
def cenario():
    """Duas abas — o caso em que `pages[-1]` errava."""
    banco = PaginaFalsa("https://banco.com/transferencia")
    loja = PaginaFalsa("https://loja.com/carrinho")
    driver = DriverFalso(banco, loja)

    modo = SimpleNamespace(exigir_driver=lambda: driver)
    modos = SimpleNamespace(get=lambda nome: modo if nome == "navegador" else None)

    config = Config({})
    registry = ToolRegistry()
    tools_navegador.register(registry, config, Guard(config), modos)
    return SimpleNamespace(registry=registry, driver=driver, banco=banco, loja=loja)


def ids(cenario) -> dict:
    """tab_id de cada página, pela ordem em que foram sincronizadas."""
    return {a.page: a.tab_id for a in cenario.driver.sessao.abas}


# ------------------------------------------------------------ agir sem alvo


def test_clicar_sem_tab_id_e_recusado(cenario):
    r = cenario.registry.execute("clicar_em", {"snapshot_id": "x", "element_id": "e1"})
    assert r.ok is False
    assert r.data.get("recusado") is True
    assert cenario.banco.cliques == [] and cenario.loja.cliques == []


def test_preencher_sem_tab_id_e_recusado(cenario):
    r = cenario.registry.execute(
        "preencher_campo", {"snapshot_id": "x", "element_id": "e1", "valor": "oi"}
    )
    assert r.ok is False
    assert cenario.banco.preenchimentos == [] and cenario.loja.preenchimentos == []


def test_clicar_sem_snapshot_e_recusado(cenario):
    mapa = ids(cenario)
    r = cenario.registry.execute(
        "clicar_em", {"tab_id": mapa[cenario.loja], "element_id": "e1"}
    )
    assert r.ok is False
    assert "inspecione" in r.speech.lower()


def test_clicar_com_element_id_inventado_e_recusado(cenario):
    """O hábito que este desenho existe para acabar: o modelo escrever um
    seletor que pareceu razoável, sem nunca ter visto o elemento."""
    mapa = ids(cenario)
    aba = cenario.driver.sessao.exigir(mapa[cenario.loja])
    cenario.driver.snapshots.guardar(snapshot_para(aba))

    r = cenario.registry.execute("clicar_em", {
        "tab_id": aba.tab_id, "snapshot_id": "snap1", "element_id": "e99",
    })
    assert r.ok is False
    assert cenario.loja.cliques == []


# --------------------------------------------------------- agir com alvo


def test_clicar_com_alvo_completo_funciona(cenario):
    """A contraprova. Sem ela, os testes acima passariam com tudo quebrado."""
    mapa = ids(cenario)
    aba = cenario.driver.sessao.exigir(mapa[cenario.loja])
    cenario.driver.snapshots.guardar(snapshot_para(aba))

    r = cenario.registry.execute("clicar_em", {
        "tab_id": aba.tab_id, "snapshot_id": "snap1", "element_id": "e1",
    })
    assert r.ok is True
    assert cenario.loja.cliques == ["#comprar"]
    assert cenario.banco.cliques == []


def test_o_clique_vai_na_aba_pedida_e_nao_na_ultima(cenario):
    """O defeito original, em uma linha.

    `pages[-1]` é a loja. Pedindo a aba do banco, o clique tem que ir no banco
    — e o teste falharia contra o código antigo justamente por isso.
    """
    mapa = ids(cenario)
    aba = cenario.driver.sessao.exigir(mapa[cenario.banco])
    cenario.driver.snapshots.guardar(snapshot_para(aba))

    r = cenario.registry.execute("clicar_em", {
        "tab_id": aba.tab_id, "snapshot_id": "snap1", "element_id": "e1",
    })
    assert r.ok is True
    assert cenario.banco.cliques == ["#comprar"]
    assert cenario.loja.cliques == []


def test_snapshot_de_uma_aba_nao_serve_para_outra(cenario):
    mapa = ids(cenario)
    loja = cenario.driver.sessao.exigir(mapa[cenario.loja])
    cenario.driver.snapshots.guardar(snapshot_para(loja))

    r = cenario.registry.execute("clicar_em", {
        "tab_id": mapa[cenario.banco], "snapshot_id": "snap1", "element_id": "e1",
    })
    assert r.ok is False
    assert r.data.get("codigo") == CODIGO_PAGINA_MUDOU
    assert cenario.banco.cliques == []


def test_depois_de_clicar_a_leitura_e_descartada(cenario):
    """Clicar muda a página com frequência; a leitura que autorizou este
    clique não autoriza o próximo."""
    mapa = ids(cenario)
    aba = cenario.driver.sessao.exigir(mapa[cenario.loja])
    cenario.driver.snapshots.guardar(snapshot_para(aba))

    args = {"tab_id": aba.tab_id, "snapshot_id": "snap1", "element_id": "e1"}
    assert cenario.registry.execute("clicar_em", dict(args)).ok is True

    segundo = cenario.registry.execute("clicar_em", dict(args))
    assert segundo.ok is False
    assert cenario.loja.cliques == ["#comprar"]        # clicou uma vez só


def test_a_recusa_vira_codigo_que_o_modelo_entende(cenario):
    """`recusado` + `codigo` dizem ao modelo o que fazer: inspecionar de novo,
    em vez de tentar outro seletor."""
    r = cenario.registry.execute("clicar_em", {
        "tab_id": "1", "snapshot_id": "nao-existe", "element_id": "e1",
    })
    assert r.data.get("codigo") == CODIGO_PAGINA_MUDOU


# ------------------------------------------------------------------- ler


def test_listar_abas_da_um_numero_a_cada_uma(cenario):
    r = cenario.registry.execute("listar_abas", {})
    assert r.ok is True
    numeros = {a["tab_id"] for a in r.data["abas"]}
    assert len(numeros) == 2


def test_listar_abas_nao_leva_a_url_inteira(cenario):
    cenario.banco._url = "https://banco.com/conta?token=SEGREDO&conta=99887"
    r = cenario.registry.execute("listar_abas", {})
    assert "SEGREDO" not in str(r.data)
    assert "99887" not in str(r.data)
    assert any(a["dominio"] == "banco.com" for a in r.data["abas"])


def test_inspecionar_com_duas_abas_exige_o_numero(cenario):
    """Ler tem padrão só quando não há ambiguidade para resolver."""
    r = cenario.registry.execute("inspecionar_pagina", {})
    assert r.ok is False
    assert "qual" in r.speech.lower() or "abas" in r.speech.lower()


# ---------------------------------------------------------------- fechar


def test_fechar_exige_o_numero(cenario):
    r = cenario.registry.execute("fechar_aba", {})
    assert r.ok is False
    assert not cenario.loja.fechada and not cenario.banco.fechada


def test_fechar_a_aba_certa(cenario):
    mapa = ids(cenario)
    r = cenario.registry.execute("fechar_aba", {"tab_id": mapa[cenario.banco]})
    assert r.ok is True
    assert cenario.banco.fechada and not cenario.loja.fechada
