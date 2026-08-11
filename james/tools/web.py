"""Navegação: busca e abertura de páginas.

A URL final vem sempre do guard. Em `pesquisar_web` ela é montada aqui a partir
de um domínio fixo, então o texto da query não consegue redirecionar o destino;
em `abrir_pagina` o guard já validou esquema, host e padrões sensíveis.
"""

from __future__ import annotations

import urllib.parse
import webbrowser

from james.logs import get_logger
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.web")

_SEARCH_ENDPOINT = "https://www.google.com/search"


def _open(url: str) -> bool:
    try:
        # new=2 pede uma aba nova em vez de sequestrar a janela atual.
        return bool(webbrowser.open(url, new=2))
    except Exception as exc:  # noqa: BLE001 — webbrowser levanta tipos variados
        logger.error("Falha ao abrir o navegador para %s: %s", url, exc)
        return False


def pesquisar_web(args: dict) -> ToolResult:
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult.failure("Preciso saber o que pesquisar.")

    url = f"{_SEARCH_ENDPOINT}?q={urllib.parse.quote_plus(query)}"
    if not _open(url):
        return ToolResult.failure("Não consegui abrir o navegador.")

    resumo = query if len(query) <= 60 else query[:57] + "..."
    return ToolResult(ok=True, ack=f"Pesquisando por {resumo}.", data={"url": url})


def abrir_pagina(args: dict) -> ToolResult:
    url = str(args.get("url", "")).strip()
    host = str(args.get("host", "")).strip()
    if not url:
        return ToolResult.failure("Preciso do endereço para abrir.")

    if not _open(url):
        return ToolResult.failure("Não consegui abrir o navegador.")

    destino = host or "a página"
    return ToolResult(ok=True, ack=f"Abrindo {destino}.", data={"url": url})


def register(registry: ToolRegistry, config, guard) -> None:
    registry.register(
        Tool(
            name="pesquisar_web",
            description=(
                "Abre uma busca no navegador. Use quando o usuário pedir para "
                "pesquisar, procurar ou buscar algo na internet."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "O que pesquisar."}
                },
                "required": ["query"],
            },
            handler=pesquisar_web,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="abrir_pagina",
            description="Abre um endereço específico no navegador padrão.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Endereço completo do site."}
                },
                "required": ["url"],
            },
            handler=abrir_pagina,
            fire_and_forget=True,
        )
    )
