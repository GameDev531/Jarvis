"""A ferramenta `delegar` — entrega uma tarefa a um agente especialista.

Delegar não afrouxa nada: cada ferramenta que o especialista chama passa pelo
mesmo guard, com as mesmas regras e a mesma confirmação de Nível 2. O que muda
é *quem pede*, nunca *o que é permitido*.

Quando vale delegar, e quando não:

- **Vale** quando a tarefa exige várias ferramentas de um mesmo domínio e
  produz material bruto que não interessa ao histórico principal — investigar
  um assunto, organizar uma pasta grande, montar um relatório.
- **Não vale** para uma ação isolada. Delegar "abra o Chrome" custa uma
  requisição a mais e não melhora nada.
"""

from __future__ import annotations

from james.agent.team import SubAgente, carregar_perfis
from james.logs import get_logger
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.delegation")


def register(registry: ToolRegistry, config, guard) -> None:
    perfis = carregar_perfis(config, registry.names)
    if not perfis:
        logger.info("Nenhum perfil de agente configurado; delegação desabilitada.")
        return

    catalogo = "\n".join(
        f"  - {p.nome}: {p.descricao} (ferramentas: {', '.join(p.ferramentas)})"
        for p in perfis.values()
    )

    def delegar(args: dict) -> ToolResult:
        nome = str(args.get("perfil", "")).strip().lower()
        tarefa = str(args.get("tarefa", "")).strip()

        perfil = perfis.get(nome)
        if perfil is None:
            disponiveis = ", ".join(sorted(perfis))
            return ToolResult.failure(
                f"Não tenho um especialista '{nome}'. Disponíveis: {disponiveis}."
            )
        if not tarefa:
            return ToolResult.failure("Preciso saber qual é a tarefa.")

        if registry.llm is None:
            return ToolResult.failure("Não consigo acionar um especialista agora.")

        agente = SubAgente(
            perfil=perfil,
            llm=registry.llm,
            guard=guard,
            registry=registry,
            confirm=registry.confirm_callback,
            on_progress=registry.agent_progress_callback,
        )
        resultado = agente.run(tarefa)

        if resultado.erro and not resultado.resumo:
            return ToolResult(
                ok=False,
                speech=f"O especialista não concluiu: {resultado.erro}.",
                data={"perfil": perfil.nome, "erro": resultado.erro},
            )

        return ToolResult(
            ok=resultado.concluido,
            data={
                "perfil": perfil.nome,
                "tarefa": tarefa,
                "resumo": resultado.resumo,
                "ferramentas_usadas": resultado.ferramentas_usadas,
                "observacao": resultado.erro or "",
            },
        )

    registry.register(
        Tool(
            name="delegar",
            description=(
                "Entrega uma tarefa a um agente especialista, que trabalha com um "
                "conjunto reduzido de ferramentas e devolve um resumo. Use para "
                "tarefas de várias etapas dentro de um domínio; para uma ação "
                "isolada, chame a ferramenta diretamente.\n\n"
                f"Especialistas disponíveis:\n{catalogo}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "perfil": {
                        "type": "string",
                        "enum": sorted(perfis),
                        "description": "Qual especialista aciona.",
                    },
                    "tarefa": {
                        "type": "string",
                        "description": (
                            "A tarefa completa e autossuficiente. O especialista não "
                            "vê a conversa nem pode fazer perguntas."
                        ),
                    },
                },
                "required": ["perfil", "tarefa"],
            },
            handler=delegar,
            fire_and_forget=False,
        )
    )
