"""O recorte de catálogo dentro do turno, com os métodos reais do orquestrador.

Não instancia o `Orchestrator` — ele carrega Qt, áudio e microfone, e nada
disso diz respeito a quais schemas vão na requisição. Os métodos são chamados
desligados da classe, contra um objeto mínimo: o código exercitado é o que roda
em produção, sem o peso de subir o assistente inteiro.

O que este arquivo protege é a parte que os testes de `packs.py` não alcançam:
lá se prova que a ESCOLHA está certa; aqui, que a escolha chega mesmo ao
`llm.tools` — e, principalmente, que a saída de emergência não é decorativa.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from james.config import Config
from james.llm.history import ToolCall
from james.memory.curated_store import MemoryStore
from james.memory.fact_store import FactStore
from james.permissions.guard import Guard
from james.runtime.orchestrator import Orchestrator
from james.tools import build_registry
from james.tools.packs import CORE


@pytest.fixture
def orq():
    """Um objeto com só o que os métodos de pack tocam."""
    raiz = Path(tempfile.mkdtemp())
    config = Config({})
    registry = build_registry(
        config,
        Guard(config),
        memory=MemoryStore(raiz),
        facts=FactStore(raiz / "f.db"),
    )
    falso = SimpleNamespace(
        registry=registry,
        llm=SimpleNamespace(tools=registry.schemas()),
        modes=SimpleNamespace(ativos=lambda: []),
        _packs_forcados=set(),
        _packs_do_turno=None,
    )
    # Os métodos chamam uns aos outros por `self`, então o dublê precisa
    # respondê-los. São os reais, só amarrados a este objeto.
    falso._aplicar_packs = lambda s: Orchestrator._aplicar_packs(falso, s)
    return falso


def nomes(orq) -> set[str]:
    return {s.name for s in orq.llm.tools}


def selecionar(orq, texto: str) -> None:
    Orchestrator._selecionar_packs(orq, texto)


# ------------------------------------------------------------------ o recorte


def test_o_recorte_chega_ao_llm(orq):
    completo = len(orq.llm.tools)
    selecionar(orq, "que horas são")
    assert len(orq.llm.tools) < completo
    assert "que_horas_sao" in nomes(orq)
    assert "criar_planilha" not in nomes(orq)


def test_a_saida_de_emergencia_esta_sempre_la(orq):
    """Se ela sumisse do CORE, um recorte errado viraria beco sem saída."""
    for frase in ("que horas são", "obrigado", "analisa a PETR4"):
        selecionar(orq, frase)
        assert "mais_ferramentas" in nomes(orq), f"sumiu em '{frase}'"


def test_o_turno_de_financas_traz_financas_e_nao_traz_o_resto(orq):
    selecionar(orq, "analisa a PETR4")
    assert "analisar_acao" in nomes(orq)
    assert "criar_apresentacao" not in nomes(orq)
    assert "organizar_arquivos" not in nomes(orq)


def test_o_modo_ligado_entra_no_recorte(orq):
    orq.modes = SimpleNamespace(ativos=lambda: ["visao"])
    selecionar(orq, "e aí, tudo certo")
    assert "ver_tela" in nomes(orq)


# --------------------------------------------------------- a saída de emergência


def test_pedir_um_pack_realmente_entrega_as_ferramentas(orq):
    """Sem isto a chamada seria decorativa.

    A segunda volta iria com o mesmo catálogo curto que já tinha faltado, o
    modelo pediria de novo — ou desistiria e diria ao usuário que não consegue,
    que é o pior jeito de falhar: parece burrice, não peça faltando.
    """
    selecionar(orq, "que horas são")
    assert "analisar_acao" not in nomes(orq)

    Orchestrator._carregar_pack_pedido(
        orq, ToolCall(call_id="1", name="mais_ferramentas", args={"pack": "financas"})
    )
    assert "analisar_acao" in nomes(orq)


def test_pedir_um_pack_nao_derruba_os_que_o_turno_ja_tinha(orq):
    """A soma é com o que já havia — recalcular perderia o resto.

    Recalcular a partir de texto vazio jogaria fora os packs que a frase
    original justificou: o modelo perderia ferramentas ao pedir ferramentas,
    e o segundo pedido apagaria o primeiro.
    """
    selecionar(orq, "pesquisa sobre baterias de estado sólido")
    assert "buscar_na_web" in nomes(orq)

    Orchestrator._carregar_pack_pedido(
        orq, ToolCall(call_id="1", name="mais_ferramentas", args={"pack": "escritorio"})
    )
    assert "criar_planilha" in nomes(orq)
    assert "buscar_na_web" in nomes(orq), "perdeu o pack da própria frase"

    Orchestrator._carregar_pack_pedido(
        orq, ToolCall(call_id="2", name="mais_ferramentas", args={"pack": "financas"})
    )
    assert {"criar_planilha", "buscar_na_web", "analisar_acao"} <= nomes(orq)


def test_pack_inventado_nao_muda_nada_nem_estoura(orq):
    selecionar(orq, "que horas são")
    antes = nomes(orq)
    Orchestrator._carregar_pack_pedido(
        orq, ToolCall(call_id="1", name="mais_ferramentas", args={"pack": "teleporte"})
    )
    assert nomes(orq) == antes


def test_sem_argumento_nenhum_nao_estoura(orq):
    selecionar(orq, "que horas são")
    Orchestrator._carregar_pack_pedido(
        orq, ToolCall(call_id="1", name="mais_ferramentas", args=None)
    )
    assert "mais_ferramentas" in nomes(orq)


def test_o_pack_pedido_nao_vaza_para_o_turno_seguinte(orq):
    """Precisar de finanças uma vez não é motivo para carregar finanças sempre.

    O `_packs_forcados` é limpo no `finally` do turno; aqui se prova o outro
    lado: se ele NÃO for limpo, o pack continua — ou seja, a limpeza importa.
    """
    selecionar(orq, "que horas são")
    Orchestrator._carregar_pack_pedido(
        orq, ToolCall(call_id="1", name="mais_ferramentas", args={"pack": "financas"})
    )
    assert "analisar_acao" in nomes(orq)

    orq._packs_forcados.clear()          # o que o `finally` do turno faz
    selecionar(orq, "que horas são")
    assert "analisar_acao" not in nomes(orq)


def test_antes_de_qualquer_selecao_o_catalogo_e_inteiro(orq):
    """O estado inicial não pode ser um recorte: um turno que falhe antes da
    seleção precisa cair no comportamento antigo, não num catálogo vazio."""
    assert "criar_planilha" in nomes(orq)
    assert "analisar_acao" in nomes(orq)
