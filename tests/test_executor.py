"""Execução do plano: o guard avalia cada passo na hora de rodar."""


from james.agent.executor import PlanExecutor, StepStatus
from james.agent.plan import Plan, Step
from james.permissions.guard import Decision, GuardVerdict
from james.tools.registry import Tool, ToolRegistry, ToolResult


class FakeGuard:
    """Guard controlável: decide por nome de ferramenta."""

    def __init__(self, decisoes=None):
        self.decisoes = decisoes or {}
        self.avaliacoes: list[tuple[str, dict]] = []

    def evaluate(self, tool, args=None):
        args = dict(args or {})
        self.avaliacoes.append((tool, args))
        decisao = self.decisoes.get(tool, Decision.ALLOW)
        return GuardVerdict(
            tool=tool,
            decision=decisao,
            reason=f"regra de teste para {tool}",
            spoken=f"Confirma {tool}?",
            args=args,
        )


def build_registry(resultados=None):
    resultados = resultados or {}
    registry = ToolRegistry()

    def make(nome):
        def handler(args):
            if nome in resultados:
                return resultados[nome](args)
            return ToolResult(ok=True, ack=f"{nome} ok", data={"eco": args})

        return handler

    for nome in ("busca", "planilha", "falha", "abrir"):
        registry.register(
            Tool(name=nome, description=nome, handler=make(nome), fire_and_forget=True)
        )
    return registry


def plano(*steps, objetivo="objetivo de teste"):
    return Plan(objetivo=objetivo, steps=list(steps))


# ----------------------------------------------------------------- básico

def test_todos_os_passos_executam_em_ordem():
    guard = FakeGuard()
    registry = build_registry()
    resultado = PlanExecutor(guard, registry).run(
        plano(Step(tool="busca"), Step(tool="planilha"))
    )

    assert resultado.concluido is True
    assert resultado.executados == 2
    assert [t for t, _ in guard.avaliacoes] == ["busca", "planilha"]


def test_resultado_de_um_passo_alimenta_o_seguinte():
    """É este encadeamento que a automação sequencial existe para fazer."""
    guard = FakeGuard()
    registry = build_registry(
        {"busca": lambda args: ToolResult(ok=True, data={"url": "https://x.com"})}
    )
    executor = PlanExecutor(guard, registry)
    executor.run(
        plano(
            Step(tool="busca", salvar_como="achado"),
            Step(tool="planilha", args={"titulo": "{{achado.url}}"}),
        )
    )

    _, args_planilha = guard.avaliacoes[1]
    assert args_planilha["titulo"] == "https://x.com"


def test_guard_recebe_os_argumentos_ja_resolvidos():
    """Avaliar antes da substituição validaria uma URL que ninguém viu."""
    guard = FakeGuard()
    registry = build_registry(
        {"busca": lambda args: ToolResult(ok=True, data={"caminho": "/permitido/x.txt"})}
    )
    PlanExecutor(guard, registry).run(
        plano(
            Step(tool="busca", salvar_como="r"),
            Step(tool="abrir", args={"alvo": "{{r.caminho}}"}),
        )
    )
    assert "{{" not in str(guard.avaliacoes[1][1])


# -------------------------------------------------------------- bloqueio

def test_passo_bloqueado_interrompe_o_plano():
    guard = FakeGuard({"planilha": Decision.BLOCK})
    resultado = PlanExecutor(guard, build_registry()).run(
        plano(Step(tool="busca"), Step(tool="planilha"), Step(tool="abrir"))
    )

    assert resultado.concluido is False
    assert resultado.outcomes[1].status is StepStatus.BLOQUEADO
    # Continuar rodaria os seguintes sobre um resultado que não existe.
    assert resultado.outcomes[2].status is StepStatus.NAO_EXECUTADO


def test_falha_de_ferramenta_interrompe_o_plano():
    registry = build_registry({"falha": lambda args: ToolResult.failure("deu ruim")})
    resultado = PlanExecutor(FakeGuard(), registry).run(
        plano(Step(tool="busca"), Step(tool="falha"), Step(tool="abrir"))
    )
    assert resultado.outcomes[1].status is StepStatus.FALHOU
    assert resultado.outcomes[2].status is StepStatus.NAO_EXECUTADO


def test_referencia_irresoluvel_em_runtime_falha_o_passo():
    """O passo anterior rodou mas não devolveu o campo esperado."""
    registry = build_registry({"busca": lambda args: ToolResult(ok=True, data={"outro": 1})})
    resultado = PlanExecutor(FakeGuard(), registry).run(
        plano(
            Step(tool="busca", salvar_como="r"),
            Step(tool="planilha", args={"t": "{{r.inexistente}}"}),
        )
    )
    assert resultado.outcomes[1].status is StepStatus.FALHOU


# ---------------------------------------------------------- confirmação

def test_confirmacao_e_pedida_apenas_no_passo_de_risco():
    """Plano de três passos com um arriscado pergunta uma vez, não três."""
    guard = FakeGuard({"planilha": Decision.CONFIRM})
    perguntas: list[str] = []

    PlanExecutor(
        guard, build_registry(), confirm=lambda texto: perguntas.append(texto) or True
    ).run(plano(Step(tool="busca"), Step(tool="planilha"), Step(tool="abrir")))

    assert len(perguntas) == 1
    assert "planilha" in perguntas[0]


def test_recusa_na_confirmacao_cancela_o_plano():
    guard = FakeGuard({"planilha": Decision.CONFIRM})
    resultado = PlanExecutor(guard, build_registry(), confirm=lambda texto: False).run(
        plano(Step(tool="busca"), Step(tool="planilha"))
    )
    assert resultado.outcomes[1].status is StepStatus.CANCELADO


def test_sem_meio_de_confirmar_o_passo_e_negado():
    """Mesma regra do resto do sistema: sem confirmação possível, é não."""
    guard = FakeGuard({"planilha": Decision.CONFIRM})
    resultado = PlanExecutor(guard, build_registry(), confirm=None).run(
        plano(Step(tool="planilha"))
    )
    assert resultado.outcomes[0].status is StepStatus.CANCELADO


# ------------------------------------------------------------- progresso

def test_progresso_e_anunciado_por_passo():
    eventos: list[tuple] = []
    PlanExecutor(
        FakeGuard(),
        build_registry(),
        on_progress=lambda i, total, rotulo: eventos.append((i, total, rotulo)),
    ).run(plano(Step(tool="busca", descricao="buscar"), Step(tool="planilha")))

    assert eventos == [(1, 2, "buscar"), (2, 2, "planilha")]


def test_erro_no_progresso_nao_derruba_o_plano():
    def quebra(*_):
        raise RuntimeError("erro proposital")

    resultado = PlanExecutor(FakeGuard(), build_registry(), on_progress=quebra).run(
        plano(Step(tool="busca"))
    )
    assert resultado.concluido is True


# ---------------------------------------------------------------- resumo

def test_resumo_de_plano_concluido():
    resultado = PlanExecutor(FakeGuard(), build_registry()).run(
        plano(Step(tool="busca"), Step(tool="planilha"))
    )
    assert "2 de 2" in resultado.resumo_falado()


def test_resumo_diz_onde_parou():
    guard = FakeGuard({"planilha": Decision.BLOCK})
    resultado = PlanExecutor(guard, build_registry()).run(
        plano(Step(tool="busca"), Step(tool="planilha", descricao="montar a tabela"))
    )
    resumo = resultado.resumo_falado()
    assert "passo 2" in resumo and "montar a tabela" in resumo


def test_plano_de_um_passo_so():
    resultado = PlanExecutor(FakeGuard(), build_registry()).run(plano(Step(tool="busca")))
    assert resultado.concluido is True
