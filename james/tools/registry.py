"""Registro de tools e o modelo de resposta falada.

Modelo de economia de requisição (fire-and-forget, seção 20):

  Uma rodada de tool calling custa 2 requisições — o modelo pede a tool, o
  código executa, e o modelo precisa ser chamado DE NOVO para saber o resultado
  e formular a fala. Isso é estrutural: ele não pode saber o resultado antes de
  a tool rodar.

  Mas quando o resultado é previsível ("abrir o Chrome" ou "que horas são"), o
  segundo ciclo não agrega nada: o próprio código sabe o que dizer. Nesses
  casos o James fala a frase pronta e não volta à API — 1 requisição.

  O que a tool devolve para ser falado:
    `speech` — informação que o modelo NÃO tem (a hora, o status do sistema).
               Sempre é dita.
    `ack`    — confirmação genérica, usada só quando ninguém mais falou nada.

  Tools de resultado imprevisível (ler página, analisar tela) marcam
  `fire_and_forget=False` e aí sim pagam o segundo ciclo — é exatamente onde o
  modelo agrega valor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from james.llm.base import ToolSchema
from james.logs import audit, get_logger

logger = get_logger("james.tools")


@dataclass
class ToolResult:
    ok: bool = True
    speech: str = ""          # informação que só a tool conhece
    ack: str = ""             # confirmação genérica, se ninguém falar nada
    data: Any = None          # o que volta ao modelo, quando volta
    external_content: bool = False   # se True, passa pelo sanitizador antes do histórico

    @classmethod
    def failure(cls, message: str) -> "ToolResult":
        return cls(ok=False, speech=message, data={"erro": message})


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    handler: Callable[[dict[str, Any]], ToolResult] = None  # type: ignore[assignment]
    fire_and_forget: bool = True

    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name, description=self.description, parameters=self.parameters
        )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # Dependências injetadas depois da construção: as ferramentas de visão
        # precisam do cliente de LLM e da captura de tela, que só existem
        # quando o orquestrador e a interface Qt já subiram.
        self.llm = None
        self.screen_grabber = None

    def attach_llm(self, client) -> None:
        self.llm = client

    def attach_screen_grabber(self, grabber) -> None:
        self.screen_grabber = grabber

    def register(self, tool: Tool) -> None:
        if tool.handler is None:
            raise ValueError(f"Tool '{tool.name}' registrada sem handler.")
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' registrada duas vezes.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def schemas(self) -> list[ToolSchema]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Executa uma tool JÁ APROVADA pelo guard.

        Os argumentos devem ser os do veredito do guard, não os crus do modelo —
        é o guard quem resolve o executável e a URL final. Chamar isto com os
        argumentos crus contornaria a validação.
        """
        tool = self._tools.get(name)
        if tool is None:
            # Só acontece com bug de fiação interna: o guard já barra tool
            # desconhecida antes de chegar aqui.
            logger.error("Tentativa de executar tool não registrada: %s", name)
            return ToolResult.failure("Essa capacidade não existe.")

        try:
            result = tool.handler(dict(args))
        except Exception as exc:  # noqa: BLE001 — uma tool não pode derrubar o James
            logger.exception("Tool '%s' levantou exceção.", name)
            audit("tool_erro", tool=name, erro=repr(exc))
            return ToolResult.failure("Não consegui completar essa ação.")

        audit("tool_executada", tool=name, args=args, ok=result.ok)
        return result
