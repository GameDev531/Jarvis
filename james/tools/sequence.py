"""Automação sequencial — a ferramenta que executa um plano de vários passos.

O modelo emite o plano inteiro como argumento desta ferramenta, o que custa uma
única requisição. A execução acontece em Python, e cada passo passa pelo guard
no momento em que roda.

Esta ferramenta é uma exceção deliberada ao modelo de permissão: ela mesma é
Nível 1, porque não faz nada por conta própria — ela apenas coordena. Toda a
autoridade continua nos passos, individualmente avaliados. Aprovar a sequência
não aprova nada do que está dentro dela.
"""

from __future__ import annotations

from james.agent.executor import PlanExecutor
from james.agent.plan import MAX_STEPS, PlanError, parse_plan
from james.logs import get_logger
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.sequence")


def register(registry: ToolRegistry, config, guard) -> None:
    def executar_sequencia(args: dict) -> ToolResult:
        objetivo = str(args.get("objetivo", "")).strip()
        passos = args.get("passos")

        # As ferramentas conhecidas são as do registro MENOS esta, para um plano
        # não conseguir chamar a si mesmo.
        disponiveis = tuple(nome for nome in registry.names if nome != "executar_sequencia")

        try:
            plano = parse_plan(objetivo, passos, disponiveis)
        except PlanError as exc:
            logger.info("Plano recusado: %s", exc)
            return ToolResult(ok=False, speech=str(exc), data={"erro": str(exc)})

        executor = PlanExecutor(
            guard=guard,
            registry=registry,
            confirm=registry.confirm_callback,
            on_progress=registry.progress_callback,
        )
        resultado = executor.run(plano)

        return ToolResult(
            ok=resultado.concluido,
            speech=resultado.resumo_falado(),
            data={
                "objetivo": resultado.objetivo,
                "concluido": resultado.concluido,
                "executados": resultado.executados,
                "total": len(resultado.outcomes),
                "passos": [
                    {
                        "indice": o.indice,
                        "ferramenta": o.step.tool,
                        "descricao": o.step.descricao,
                        "status": o.status.value,
                        "detalhe": o.detalhe,
                    }
                    for o in resultado.outcomes
                ],
            },
        )

    registry.register(
        Tool(
            name="executar_sequencia",
            description=(
                "Executa vários passos em ordem, onde um passo pode usar o resultado "
                "do anterior. Use quando o pedido exigir encadeamento — por exemplo "
                "'pesquise X e monte uma planilha com isso'. Para uma ação isolada, "
                "chame a ferramenta diretamente em vez desta.\n\n"
                "Para encadear: dê um nome ao resultado de um passo em 'salvar_como' "
                "e referencie esse nome nos argumentos de um passo posterior usando "
                "{{nome}} ou {{nome.campo}}. Só é possível referenciar passos "
                "anteriores, nunca posteriores.\n\n"
                f"Máximo de {MAX_STEPS} passos."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "objetivo": {
                        "type": "string",
                        "description": "O que o usuário quer, em uma frase.",
                    },
                    "passos": {
                        "type": "array",
                        "description": "Passos em ordem de execução.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ferramenta": {
                                    "type": "string",
                                    "description": "Nome exato de outra ferramenta.",
                                },
                                "argumentos": {
                                    "type": "object",
                                    "description": (
                                        "Argumentos da ferramenta. Pode conter "
                                        "referências {{nome}} a resultados anteriores."
                                    ),
                                },
                                "salvar_como": {
                                    "type": "string",
                                    "description": (
                                        "Nome para guardar o resultado deste passo, "
                                        "se algum passo seguinte for usá-lo."
                                    ),
                                },
                                "descricao": {
                                    "type": "string",
                                    "description": "O que este passo faz, em poucas palavras.",
                                },
                            },
                            "required": ["ferramenta"],
                        },
                    },
                },
                "required": ["objetivo", "passos"],
            },
            handler=executar_sequencia,
            # O resumo já sai pronto daqui; voltar à API para narrá-lo não
            # acrescentaria informação que o código não tenha.
            fire_and_forget=True,
        )
    )
