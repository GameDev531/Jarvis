"""Agentes especialistas: recorte de catálogo e permissões inalteradas."""

import pytest

from james.agent.team import SubAgente, carregar_perfis, Perfil
from james.config import Config
from james.llm.base import LLMResponse, NoProviderAvailable
from james.llm.history import ToolCall
from james.permissions.guard import Decision, GuardVerdict
from james.tools.registry import Tool, ToolRegistry, ToolResult


class FakeLLM:
    """Devolve respostas roteirizadas e registra o catálogo que enxergou."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.tools = []
        self.catalogos_vistos: list[list[str]] = []

    def reason(self, conversa, text=None, instruction=None, on_text=None):
        self.catalogos_vistos.append([s.name for s in (self.tools or [])])
        if not self.respostas:
            return LLMResponse(text="acabou", provider="fake", model="fake")
        return self.respostas.pop(0)


class FakeGuard:
    def __init__(self, decisoes=None):
        self.decisoes = decisoes or {}
        self.avaliadas: list[str] = []

    def evaluate(self, tool, args=None):
        self.avaliadas.append(tool)
        return GuardVerdict(
            tool=tool,
            decision=self.decisoes.get(tool, Decision.ALLOW),
            reason="teste",
            spoken=f"Confirma {tool}?",
            args=dict(args or {}),
        )


@pytest.fixture
def registry():
    r = ToolRegistry()
    for nome in ("buscar_na_web", "ler_pagina", "abrir_app", "mover_arquivo"):
        r.register(
            Tool(
                name=nome,
                description=f"faz {nome}",
                handler=lambda args, n=nome: ToolResult(ok=True, data={"tool": n}),
            )
        )
    return r


PERFIL = Perfil(
    nome="pesquisador",
    descricao="Investiga assuntos",
    ferramentas=("buscar_na_web", "ler_pagina"),
)


# ------------------------------------------------------------- perfis

def test_perfil_valido_e_carregado(registry):
    config = Config({"agentes": {"perfis": {
        "pesquisador": {"descricao": "investiga", "ferramentas": ["buscar_na_web"]}
    }}})
    perfis = carregar_perfis(config, registry.names)
    assert perfis["pesquisador"].ferramentas == ("buscar_na_web",)


def test_ferramenta_inexistente_e_descartada(registry):
    config = Config({"agentes": {"perfis": {
        "x": {"descricao": "d", "ferramentas": ["buscar_na_web", "nao_existe"]}
    }}})
    assert carregar_perfis(config, registry.names)["x"].ferramentas == ("buscar_na_web",)


def test_perfil_sem_ferramenta_valida_e_ignorado(registry):
    config = Config({"agentes": {"perfis": {"x": {"ferramentas": ["nao_existe"]}}}})
    assert carregar_perfis(config, registry.names) == {}


def test_perfil_nao_pode_delegar_nem_orquestrar(registry):
    """Senão um agente chamaria outro agente, sem fim."""
    registry.register(Tool(name="delegar", description="d", handler=lambda a: ToolResult()))
    config = Config({"agentes": {"perfis": {
        "x": {"ferramentas": ["delegar", "executar_sequencia", "buscar_na_web"]}
    }}})
    assert carregar_perfis(config, registry.names)["x"].ferramentas == ("buscar_na_web",)


def test_sem_perfis_configurados(registry):
    assert carregar_perfis(Config({}), registry.names) == {}


# ------------------------------------------------------- recorte real

def test_agente_so_enxerga_as_ferramentas_do_perfil(registry):
    """É o ponto todo do módulo: menos opções, escolha melhor."""
    llm = FakeLLM([LLMResponse(text="pronto", provider="f", model="f")])
    SubAgente(PERFIL, llm, FakeGuard(), registry).run("investigue algo")

    assert llm.catalogos_vistos[0] == ["buscar_na_web", "ler_pagina"]
    assert "abrir_app" not in llm.catalogos_vistos[0]


def test_catalogo_original_e_restaurado(registry):
    """O agente principal não pode ficar com o recorte do especialista."""
    llm = FakeLLM([LLMResponse(text="pronto", provider="f", model="f")])
    llm.tools = ["catalogo-completo"]
    SubAgente(PERFIL, llm, FakeGuard(), registry).run("tarefa")
    assert llm.tools == ["catalogo-completo"]


def test_ferramenta_fora_do_recorte_e_recusada(registry):
    """Mesmo que o modelo invente o nome, ele não alcança o que não é dele."""
    guard = FakeGuard()
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(name="mover_arquivo")], provider="f", model="f"),
        LLMResponse(text="terminei", provider="f", model="f"),
    ])
    resultado = SubAgente(PERFIL, llm, guard, registry).run("tarefa")

    assert "mover_arquivo" not in guard.avaliadas
    assert "mover_arquivo" not in resultado.ferramentas_usadas


# ------------------------------------------------------------ execução

def test_ferramenta_do_recorte_executa(registry):
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(name="buscar_na_web")], provider="f", model="f"),
        LLMResponse(text="encontrei o seguinte", provider="f", model="f"),
    ])
    resultado = SubAgente(PERFIL, llm, FakeGuard(), registry).run("tarefa")

    assert resultado.ferramentas_usadas == ["buscar_na_web"]
    assert resultado.concluido is True
    assert resultado.resumo == "encontrei o seguinte"


def test_guard_continua_valendo_para_o_especialista(registry):
    """Delegar muda quem pede, nunca o que é permitido."""
    guard = FakeGuard({"buscar_na_web": Decision.BLOCK})
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(name="buscar_na_web")], provider="f", model="f"),
        LLMResponse(text="não consegui", provider="f", model="f"),
    ])
    resultado = SubAgente(PERFIL, llm, guard, registry).run("tarefa")
    assert resultado.ferramentas_usadas == []


def test_confirmacao_de_nivel_2_e_pedida(registry):
    guard = FakeGuard({"ler_pagina": Decision.CONFIRM})
    perguntas = []
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(name="ler_pagina")], provider="f", model="f"),
        LLMResponse(text="feito", provider="f", model="f"),
    ])
    SubAgente(
        PERFIL, llm, guard, registry, confirm=lambda t: perguntas.append(t) or True
    ).run("tarefa")
    assert len(perguntas) == 1


def test_sem_meio_de_confirmar_o_passo_e_negado(registry):
    guard = FakeGuard({"ler_pagina": Decision.CONFIRM})
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(name="ler_pagina")], provider="f", model="f"),
        LLMResponse(text="feito", provider="f", model="f"),
    ])
    resultado = SubAgente(PERFIL, llm, guard, registry, confirm=None).run("tarefa")
    assert resultado.ferramentas_usadas == []


def test_teto_de_iteracoes(registry):
    """Um agente que só chama ferramenta e nunca conclui precisa parar."""
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(name="buscar_na_web")], provider="f", model="f")
        for _ in range(20)
    ])
    resultado = SubAgente(PERFIL, llm, FakeGuard(), registry).run("tarefa")
    assert resultado.concluido is False
    assert "iterações" in resultado.erro


def test_perfil_sem_ferramenta_disponivel(registry):
    vazio = Perfil(nome="x", descricao="d", ferramentas=("inexistente",))
    resultado = SubAgente(vazio, FakeLLM([]), FakeGuard(), registry).run("tarefa")
    assert resultado.erro


def test_sem_modelo_disponivel(registry):
    class SemModelo(FakeLLM):
        def reason(self, *a, **k):
            raise NoProviderAvailable("cota esgotada")

    resultado = SubAgente(PERFIL, SemModelo([]), FakeGuard(), registry).run("tarefa")
    assert resultado.concluido is False
    assert "sem modelo" in resultado.erro


def test_progresso_e_anunciado(registry):
    eventos = []
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(name="buscar_na_web")], provider="f", model="f"),
        LLMResponse(text="ok", provider="f", model="f"),
    ])
    SubAgente(
        PERFIL, llm, FakeGuard(), registry,
        on_progress=lambda p, e: eventos.append((p, e)),
    ).run("tarefa")
    assert ("pesquisador", "começando") in eventos
    assert ("pesquisador", "buscar_na_web") in eventos


def test_erro_no_progresso_nao_derruba(registry):
    def quebra(*_):
        raise RuntimeError("proposital")

    llm = FakeLLM([LLMResponse(text="ok", provider="f", model="f")])
    resultado = SubAgente(PERFIL, llm, FakeGuard(), registry, on_progress=quebra).run("t")
    assert resultado.concluido is True
