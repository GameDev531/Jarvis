"""Abrir e fechar programas da whitelist.

O nome do programa nunca chega aqui como o modelo falou: o guard já casou
contra a whitelist e resolveu o executável real. Estas funções executam apenas
o que veio resolvido — se alguém chamar o handler direto com um comando
arbitrário, está contornando a camada de permissão.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from james.logs import get_logger
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.apps")

_CLOSE_TIMEOUT_S = 10


def _spawn_flags() -> int:
    """Desacopla o app do processo do James.

    Sem isto, fechar o James fecharia junto tudo que ele abriu — e um app que
    escreva no console poluiria a saída do assistente.
    """
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )


def _as_argv(command: str) -> list[str]:
    """Transforma o comando da whitelist em argv, sem passar por shell.

    Nada aqui roda com `shell=True`: mesmo vindo da whitelist, um comando com
    `&&` ou `|` não deve virar dois comandos.
    """
    text = str(command).strip()
    if not text:
        raise ValueError("Comando vazio na whitelist.")
    if sys.platform == "win32":
        # No Windows, caminhos com espaço são a regra ("C:\\Program Files\\...").
        # Se o valor configurado for um caminho existente, ele é o argv inteiro.
        if Path(text).exists():
            return [text]
        return shlex.split(text, posix=False)
    return shlex.split(text)


def _image_name(command: str) -> str:
    """Nome do executável para o taskkill (ex: 'chrome.exe')."""
    name = Path(_as_argv(command)[0]).name
    if sys.platform == "win32" and not name.lower().endswith(".exe"):
        name += ".exe"
    return name


def abrir_app(args: dict) -> ToolResult:
    nome = str(args.get("nome", "")).strip()
    comando = args.get("comando")
    if not comando:
        # Chegou sem resolução do guard: recusar é o comportamento correto.
        logger.error("abrir_app chamado sem comando resolvido pelo guard.")
        return ToolResult.failure("Não consegui identificar esse programa.")

    try:
        argv = _as_argv(str(comando))
    except ValueError as exc:
        return ToolResult.failure(f"Configuração inválida para {nome}: {exc}")

    try:
        subprocess.Popen(
            argv,
            creationflags=_spawn_flags(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except FileNotFoundError:
        return ToolResult.failure(
            f"Não encontrei o {nome} instalado no caminho configurado."
        )
    except OSError as exc:
        logger.error("Falha ao abrir %s (%s): %s", nome, argv, exc)
        return ToolResult.failure(f"Não consegui abrir o {nome}.")

    return ToolResult(ok=True, ack=f"Abrindo o {nome}.", data={"aberto": nome})


def fechar_app(args: dict) -> ToolResult:
    nome = str(args.get("nome", "")).strip()
    comando = args.get("comando")
    if not comando:
        logger.error("fechar_app chamado sem comando resolvido pelo guard.")
        return ToolResult.failure("Não consegui identificar esse programa.")

    try:
        image = _image_name(str(comando))
    except ValueError as exc:
        return ToolResult.failure(f"Configuração inválida para {nome}: {exc}")

    if sys.platform == "win32":
        # Sem /F de propósito: o taskkill "educado" manda WM_CLOSE, então o app
        # ainda consegue perguntar se você quer salvar. Forçar aqui destruiria
        # exatamente o trabalho que a confirmação de risco quis proteger.
        argv = ["taskkill", "/IM", image]
    else:
        argv = ["pkill", "-TERM", "-f", image]

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            timeout=_CLOSE_TIMEOUT_S,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if sys.platform == "win32"
            else 0,
        )
    except subprocess.TimeoutExpired:
        return ToolResult.failure(f"O {nome} não respondeu ao pedido de fechar.")
    except (OSError, FileNotFoundError) as exc:
        logger.error("Falha ao fechar %s: %s", nome, exc)
        return ToolResult.failure(f"Não consegui fechar o {nome}.")

    if result.returncode != 0:
        # taskkill devolve 128 quando não há processo com aquele nome.
        return ToolResult(
            ok=True, ack=f"O {nome} não estava aberto.", data={"fechado": False}
        )

    return ToolResult(ok=True, ack=f"Fechando o {nome}.", data={"fechado": True})


def register(registry: ToolRegistry, config, guard) -> None:
    registry.register(
        Tool(
            name="abrir_app",
            description=(
                "Abre um programa instalado no computador. Use apenas para "
                "programas; para sites use abrir_pagina."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "nome": {
                        "type": "string",
                        "description": "Nome do programa como o usuário o chamou.",
                    }
                },
                "required": ["nome"],
            },
            handler=abrir_app,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="fechar_app",
            description=(
                "Fecha um programa aberto. Pode descartar trabalho não salvo, "
                "por isso exige confirmação do usuário."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "nome": {"type": "string", "description": "Nome do programa a fechar."}
                },
                "required": ["nome"],
            },
            handler=fechar_app,
            fire_and_forget=True,
        )
    )
