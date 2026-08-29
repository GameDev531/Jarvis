"""Projeção holográfica — "Jarvis, mostra um cérebro".

A ferramenta é fina de propósito: ela só **publica o pedido** no barramento de
estado. Quem resolve o assunto em geometria é o navegador, que já tem o
Three.js carregado, a cascata de resolução e a GPU. Mandar o Python decidir
geometria seria mover trabalho para o processo errado.

Duas propriedades que valem registrar:

**Não gasta cota.** É `fire_and_forget`: o resultado é previsível ("Projetando
o cérebro"), então o James fala a frase pronta e não volta à API. Diferente da
visão, onde o segundo ciclo agrega de verdade, aqui não haveria o que agregar —
o modelo não vê a tela.

**Não falha por assunto desconhecido.** A cascata do lado do navegador tem um
nível final genérico: pedir algo que ninguém previu devolve uma forma abstrata,
não um erro. Então esta ferramenta aceita qualquer texto — validar aqui
rejeitaria pedidos que a interface atenderia bem.

O modo holograma precisa estar ligado. Se não estiver, a resposta diz isso em
vez de o pedido sumir em silêncio — falhar calado é pior que falhar falando.
"""

from __future__ import annotations

from james.logs import audit, get_logger
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.holograma")

# Um assunto é uma coisa, não um parágrafo. O teto evita que o modelo mande a
# frase inteira do usuário como "assunto" e a janela fique com um título absurdo.
MAX_ASSUNTO = 60


def register(registry: ToolRegistry, config, guard, bus, modes=None) -> None:
    if bus is None:
        return

    def projetar_holograma(args: dict) -> ToolResult:
        assunto = str(args.get("assunto", "")).strip()
        if not assunto:
            return ToolResult.failure("O que devo projetar, senhor?")
        if len(assunto) > MAX_ASSUNTO:
            assunto = assunto[:MAX_ASSUNTO].rstrip()

        # Sem a interface aberta não há onde projetar. Dizer isso é mais útil
        # que aceitar o pedido e não mostrar nada.
        if modes is not None:
            holo = modes.get("holograma")
            if holo is not None and not holo.ligado:
                return ToolResult(
                    ok=False,
                    speech=(
                        "A interface holográfica está desligada, senhor. "
                        "Peça para ativar o holograma primeiro."
                    ),
                    data={"erro": "modo_desligado"},
                )

        titulo = str(args.get("titulo", "")).strip()[:MAX_ASSUNTO]
        bus.publish(holograma={"assunto": assunto, "titulo": titulo or assunto})
        audit("holograma", assunto=assunto)
        logger.info("Projetando holograma: %s", assunto)

        return ToolResult(
            ok=True,
            speech=f"Projetando {assunto}.",
            data={"assunto": assunto},
        )

    def fechar_hologramas(args: dict) -> ToolResult:
        bus.publish(holograma_fechar=True)
        return ToolResult(ok=True, speech="Projeções encerradas.")

    registry.register(
        Tool(
            name="projetar_holograma",
            description=(
                "Projeta um objeto em 3D na interface holográfica. Use quando o "
                "usuário pedir para ver, mostrar, projetar ou visualizar alguma "
                "coisa — um cérebro, o planeta, uma molécula, um foguete. "
                "Aceita qualquer assunto: o que não estiver no catálogo vira uma "
                "forma abstrata em vez de erro."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "assunto": {
                        "type": "string",
                        "description": (
                            "O objeto a projetar, em uma ou duas palavras "
                            "('cérebro', 'dna', 'foguete'). Sem artigo."
                        ),
                    },
                    "titulo": {
                        "type": "string",
                        "description": "Título da janela, se diferente do assunto.",
                    },
                },
                "required": ["assunto"],
            },
            handler=projetar_holograma,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="fechar_hologramas",
            description="Fecha todas as janelas de projeção abertas na interface.",
            parameters={"type": "object", "properties": {}},
            handler=fechar_hologramas,
            fire_and_forget=True,
        )
    )
