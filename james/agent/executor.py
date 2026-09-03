"""Execução passo a passo de um plano.

Invariante central: **cada passo passa pelo guard no momento em que roda**, com
os argumentos já resolvidos.

Isso não é detalhe. A alternativa tentadora seria aprovar o plano inteiro de
antemão — o usuário confirma uma vez e a sequência corre. Mas os argumentos de
um passo podem conter `{{resultado_anterior}}`, cujo valor só existe depois que
o passo anterior rodou. Aprovar antes seria aprovar um caminho de arquivo ou
uma URL que ninguém viu ainda.

Por isso, também, a confirmação de Nível 2 acontece por passo. Um plano com
três passos em que só o terceiro é arriscado pergunta uma vez, no terceiro.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from james.agent.plan import Plan, PlanError, Step, resolve_references
from james.logs import audit, audit_text, get_logger
from james.permissions.guard import Decision

logger = get_logger("james.agent.executor")


class StepStatus(Enum):
    OK = "ok"
    BLOQUEADO = "bloqueado"
    CANCELADO = "cancelado"
    FALHOU = "falhou"
    NAO_EXECUTADO = "nao_executado"


@dataclass
class StepOutcome:
    indice: int
    step: Step
    status: StepStatus
    detalhe: str = ""
    fala: str = ""

    @property
    def ok(self) -> bool:
        return self.status is StepStatus.OK

    @property
    def rotulo_curto(self) -> str:
        """Rótulo enxuto para a frase de resumo, sem repetir o plano inteiro."""
        return (self.step.descricao or self.step.tool)[:40]


@dataclass
class PlanResult:
    objetivo: str
    outcomes: list[StepOutcome] = field(default_factory=list)
    escopo: dict[str, Any] = field(default_factory=dict)

    @property
    def concluido(self) -> bool:
        return bool(self.outcomes) and all(o.ok for o in self.outcomes)

    @property
    def executados(self) -> int:
        return sum(1 for o in self.outcomes if o.ok)

    def resumo_falado(self) -> str:
        """Uma ou duas frases sobre o que aconteceu, prontas para o TTS."""
        total = len(self.outcomes)
        if self.concluido:
            falas = [o.fala for o in self.outcomes if o.fala]
            base = f"Pronto, {self.executados} de {total} passos concluídos."
            # A fala do último passo costuma ser a informação que interessa.
            return f"{base} {falas[-1]}" if falas else base

        interrompido = next((o for o in self.outcomes if not o.ok), None)
        if interrompido is None:
            return "Não consegui executar o plano."

        motivos = {
            StepStatus.BLOQUEADO: "não tenho permissão para isso",
            StepStatus.CANCELADO: "o senhor cancelou",
            StepStatus.FALHOU: "deu erro",
            StepStatus.NAO_EXECUTADO: "não cheguei nele",
        }
        motivo = motivos.get(interrompido.status, "não deu certo")
        return (
            f"Parei no passo {interrompido.indice} de {total}, "
            f"{interrompido.rotulo_curto}: {motivo}."
        )


class PlanExecutor:
    def __init__(
        self,
        guard,
        registry,
        confirm: Callable[[str], bool] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.guard = guard
        self.registry = registry
        self.confirm = confirm
        self.on_progress = on_progress

    def run(self, plan: Plan) -> PlanResult:
        resultado = PlanResult(objetivo=plan.objetivo)
        escopo: dict[str, Any] = {}
        total = len(plan.steps)
        audit("plano_iniciado", **audit_text(plan.objetivo, campo="objetivo"), passos=total)

        for indice, step in enumerate(plan.steps, start=1):
            self._anunciar(indice, total, step)
            outcome = self._run_step(indice, step, escopo)
            resultado.outcomes.append(outcome)

            if not outcome.ok:
                # Continuar depois de uma falha faria os passos seguintes
                # rodarem sobre um resultado que não existe.
                for restante_indice, restante in enumerate(
                    plan.steps[indice:], start=indice + 1
                ):
                    resultado.outcomes.append(
                        StepOutcome(
                            indice=restante_indice,
                            step=restante,
                            status=StepStatus.NAO_EXECUTADO,
                            detalhe="passo anterior não concluiu",
                        )
                    )
                break

        resultado.escopo = escopo
        audit(
            "plano_concluido",
            **audit_text(plan.objetivo, campo="objetivo"),
            executados=resultado.executados,
            total=total,
            concluido=resultado.concluido,
        )
        return resultado

    def _anunciar(self, indice: int, total: int, step: Step) -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress(indice, total, step.rotulo)
        except Exception:  # noqa: BLE001 — progresso não pode derrubar o plano
            logger.exception("Erro ao anunciar progresso do plano.")

    def _run_step(self, indice: int, step: Step, escopo: dict[str, Any]) -> StepOutcome:
        try:
            args = resolve_references(step.args, escopo)
        except PlanError as exc:
            logger.warning("Passo %d com referência irresolúvel: %s", indice, exc)
            return _outcome(indice, step, StepStatus.FALHOU, str(exc))

        verdict = self.guard.evaluate(step.tool, args)
        audit(
            "plano_passo_guard",
            passo=indice,
            tool=step.tool,
            decisao=verdict.decision.value,
            motivo=verdict.reason,
        )

        if verdict.decision is Decision.BLOCK:
            return _outcome(indice, step, StepStatus.BLOQUEADO, verdict.reason, verdict.spoken)

        if verdict.decision is Decision.CONFIRM:
            if self.confirm is None:
                # Sem caminho de confirmação, a resposta é não — mesma regra do
                # resto do sistema.
                return _outcome(
                    indice, step, StepStatus.CANCELADO, "sem meio de confirmar"
                )
            if not self.confirm(verdict.spoken):
                return _outcome(indice, step, StepStatus.CANCELADO, "recusado pelo usuário")

        result = self.registry.execute(step.tool, verdict.args)
        if not result.ok:
            return _outcome(
                indice, step, StepStatus.FALHOU, result.speech or "erro na ferramenta"
            )

        if step.salvar_como:
            # Guarda o dado estruturado, não a fala: é o dado que os passos
            # seguintes conseguem usar.
            escopo[step.salvar_como] = result.data

        return _outcome(indice, step, StepStatus.OK, "", result.speech or result.ack)


def _outcome(
    indice: int, step: Step, status: StepStatus, detalhe: str = "", fala: str = ""
) -> StepOutcome:
    return StepOutcome(indice=indice, step=step, status=status, detalhe=detalhe, fala=fala)
