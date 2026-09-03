"""Ferramentas sobre as próprias ferramentas.

Só existe uma, e ela é a saída de emergência do roteador de packs.

O roteador escolhe por palavra-chave, então ele erra. Errar para mais custa
tokens; errar para MENOS custa a tarefa — o modelo simplesmente não tem como
fazer o que foi pedido, e o que chega até você é uma recusa sem explicação
("não consigo fazer isso agora"), que é o pior jeito de falhar: parece que o
James ficou burro, não que faltou uma peça.

Com `mais_ferramentas`, o modelo diz o que faltou. Custa uma volta a mais, só
quando o roteador erra, e cada erro fica registrado na trilha com o pack que
foi pedido — ou seja, a lista de gatilhos ganha uma fonte de correção que não
depende de alguém adivinhar.
"""

from __future__ import annotations

from james.logs import get_logger
from james.tools.packs import PACKS, packs_disponiveis
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger(__name__)


def register(registry: ToolRegistry, config, guard) -> None:
    def mais_ferramentas(args: dict) -> ToolResult:
        pedido = str(args.get("pack", "")).strip().lower()

        # O catálogo real, não o teórico: um pack cujas ferramentas não foram
        # registradas nesta execução não deve ser oferecido nem aceito.
        disponiveis = packs_disponiveis(registry.names)

        if pedido not in disponiveis:
            return ToolResult.failure(
                f"Não tenho um conjunto chamado '{pedido}'. "
                f"Os que existem são: {', '.join(disponiveis)}."
            )

        presentes = [f for f in PACKS[pedido] if f in set(registry.names)]
        logger.info("Modelo pediu o pack '%s'.", pedido)
        return ToolResult(
            ok=True,
            data={"pack": pedido, "ferramentas": presentes},
        )

    registry.register(
        Tool(
            name="mais_ferramentas",
            description=(
                "Carrega um conjunto de ferramentas que não está disponível "
                "agora. Use quando o usuário pedir algo que você sabe fazer mas "
                "não encontra a ferramenta na lista atual — em vez de dizer que "
                "não consegue. Depois de carregar, faça a ação pedida."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pack": {
                        "type": "string",
                        "description": (
                            "O conjunto necessário: memoria (lembrar e fatos), "
                            "web (pesquisa e páginas), arquivos, escritorio "
                            "(slides e planilhas), financas, visao (tela e "
                            "câmera), agente (sequências), habilidades, "
                            "navegador."
                        ),
                        "audit_mode": "plaintext",
                    },
                },
                "required": ["pack"],
            },
            handler=mais_ferramentas,
            fire_and_forget=False,
        )
    )
