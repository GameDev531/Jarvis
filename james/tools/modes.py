"""Ferramentas de modo: ligar e desligar capacidades por voz.

"Jarvis, ativa a webcam" chega aqui. O modelo entende a intenção e escolhe o
modo; o gerente decide se dá, e o guard decide se pode.

Uma assimetria de propósito entre as duas ferramentas:

- `ativar_modo` é fire-and-forget=False para os modos sensíveis? Não — é
  fire-and-forget em ambos os casos, porque o resultado é uma frase que o
  próprio código já sabe escrever ("Webcam ligada"). Não há nada que um
  segundo ciclo com o modelo acrescentaria, e ele custaria uma requisição.
- `desativar_modo` nunca falha por permissão. Ligar pode exigir confirmação;
  desligar é sempre imediato. Um freio que às vezes não funciona não é freio.

Se alguém disser "desativa a webcam" com a webcam já desligada, a resposta é
uma frase tranquila, não um erro — o usuário queria a câmera fechada, e ela
está.
"""

from __future__ import annotations

from james.logs import get_logger
from james.modes.base import ModeError
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.modos")


def register(registry: ToolRegistry, config, guard, manager) -> None:
    if manager is None:
        return

    def _nomes() -> str:
        nomes = manager.nomes
        return ", ".join(nomes) if nomes else "nenhum"

    def ativar_modo(args: dict) -> ToolResult:
        nome = str(args.get("modo", "")).strip().lower()
        if not nome:
            return ToolResult.failure("Qual modo eu ligo?")

        try:
            mensagem = manager.ligar(nome)
        except ModeError as exc:
            logger.info("Não liguei o modo '%s': %s", nome, exc)
            return ToolResult.failure(str(exc))

        return ToolResult(ok=True, speech=mensagem, data={"modo": nome, "estado": "ligado"})

    def desativar_modo(args: dict) -> ToolResult:
        nome = str(args.get("modo", "")).strip().lower()
        if not nome:
            # Sem nome, desligar tudo é a leitura certa: quem pede para
            # desligar quer o hardware livre, não uma pergunta de volta.
            encerrados = manager.desligar_todos()
            if not encerrados:
                return ToolResult(ok=True, speech="Não há nenhum modo ligado.")
            return ToolResult(
                ok=True,
                speech=f"Desliguei: {', '.join(encerrados)}.",
                data={"desligados": encerrados},
            )

        try:
            mensagem = manager.desligar(nome)
        except ModeError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult(ok=True, speech=mensagem, data={"modo": nome, "estado": "desligado"})

    def listar_modos(args: dict) -> ToolResult:
        infos = manager.listar()
        if not infos:
            return ToolResult(ok=True, speech="Não tenho nenhum modo disponível.")

        ligados = [i.nome for i in infos if i.estado == "ligado"]
        if ligados:
            fala = f"Modos disponíveis: {_nomes()}. Ligados agora: {', '.join(ligados)}."
        else:
            fala = f"Modos disponíveis: {_nomes()}. Nenhum está ligado."
        return ToolResult(ok=True, speech=fala, data=[i.to_dict() for i in infos])

    disponiveis = list(manager.nomes)
    descricao_modos = ", ".join(disponiveis) if disponiveis else "nenhum"

    # O `enum` só entra quando há modos: um enum vazio faz o schema recusar
    # qualquer valor, e alguns provedores rejeitam a ferramenta inteira.
    campo_modo: dict = {
        "type": "string",
        "description": "Nome do modo a ligar.",
        "audit_mode": "plaintext",
    }
    if disponiveis:
        campo_modo["enum"] = disponiveis

    registry.register(
        Tool(
            name="ativar_modo",
            description=(
                "Liga um modo do James, que passa a usar um recurso contínuo "
                "(câmera, por exemplo) até ser desligado. "
                f"Modos existentes: {descricao_modos}."
            ),
            parameters={
                "type": "object",
                "properties": {"modo": campo_modo},
                "required": ["modo"],
            },
            handler=ativar_modo,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="desativar_modo",
            description=(
                "Desliga um modo e libera o recurso que ele ocupava. "
                "Sem o nome do modo, desliga todos."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "modo": {
                        "type": "string",
                        "description": "Nome do modo a desligar. Vazio desliga todos.",
                        "audit_mode": "plaintext",
                    }
                },
            },
            handler=desativar_modo,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="listar_modos",
            description="Diz quais modos existem e quais estão ligados agora.",
            parameters={"type": "object", "properties": {}},
            handler=listar_modos,
            fire_and_forget=True,
        )
    )
