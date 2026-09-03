"""Ferramentas do navegador — o que o modelo pode pedir ao Chrome.

Divisão de risco, e ela não é arbitrária:

  LER  (Nível 1)   abrir aba, listar abas, inspecionar, ver a página
  AGIR (Nível 2)   preencher campo, clicar

Abrir uma aba não muda nada no mundo; clicar pode comprar uma passagem. O
guard pede confirmação para o segundo grupo e não para o primeiro.

Acima dos dois níveis existe uma terceira categoria, que não é "arriscada" e
sim **impossível**: campo de senha, upload de arquivo, campo oculto. Ver
`james/browser/actions.py`. Nenhuma confirmação destrava, porque quem decide
não é o guard nem o modelo — é o código, olhando o que a página diz que o
campo é.
"""

from __future__ import annotations

from james.browser.actions import AcaoRecusada, clicar as _clicar, preencher as _preencher
from james.browser.driver import BrowserUnavailable
from james.browser.inspector import inspecionar as _inspecionar
from james.logs import get_logger
from james.modes.base import ModeError
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.navegador")


def register(registry: ToolRegistry, config, guard, modes) -> None:
    """Registra as ferramentas do navegador. Sem `modes`, nenhuma existe."""
    if modes is None:
        return
    modo = modes.get("navegador") if hasattr(modes, "get") else None
    if modo is None:
        return

    def _driver():
        return modo.exigir_driver()

    def _pagina():
        return _driver().pagina_atual()

    # ------------------------------------------------------------- ler

    def abrir_aba(args: dict) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url:
            return ToolResult.failure("Preciso de um endereço.")
        try:
            pagina = _driver().abrir(url)
        except (ModeError, BrowserUnavailable) as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001 — site fora do ar não derruba o James
            return ToolResult.failure(f"Não consegui abrir: {exc}")
        return ToolResult(
            ok=True, ack="Abri.",
            data={"url": pagina.url, "titulo": pagina.title()},
        )

    def listar_abas(args: dict) -> ToolResult:
        try:
            abas = _driver().abas()
        except (ModeError, BrowserUnavailable) as exc:
            return ToolResult.failure(str(exc))
        if not abas:
            return ToolResult(ok=True, speech="Nenhuma aba aberta.", data={"abas": []})
        return ToolResult(
            ok=True,
            speech=f"{len(abas)} aba{'s' if len(abas) != 1 else ''} abertas.",
            data={"abas": abas},
            # Título de aba é texto de terceiro: passa pelo sanitizador antes
            # de entrar no histórico do modelo.
            external_content=True,
        )

    def inspecionar_pagina(args: dict) -> ToolResult:
        try:
            relatorio = _inspecionar(_pagina())
        except (ModeError, BrowserUnavailable) as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"Não consegui inspecionar: {exc}")
        return ToolResult(
            ok=True,
            speech=relatorio.get("resumo", ""),
            data=relatorio,
            external_content=True,
        )

    # ------------------------------------------------------------- agir

    def preencher_campo(args: dict) -> ToolResult:
        seletor = str(args.get("seletor", "")).strip()
        valor = str(args.get("valor", ""))
        if not seletor:
            return ToolResult.failure("Preciso saber qual campo.")
        try:
            frase = _preencher(_pagina(), seletor, valor)
        except AcaoRecusada as exc:
            # Recusa não é falha: o James fez a coisa certa. A frase explica
            # ao usuário, e o modelo aprende que não adianta insistir.
            return ToolResult(ok=False, speech=str(exc), data={"recusado": True})
        except (ModeError, BrowserUnavailable) as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"Não consegui preencher: {exc}")
        return ToolResult(ok=True, ack=frase, data={"seletor": seletor})

    def clicar_em(args: dict) -> ToolResult:
        seletor = str(args.get("seletor", "")).strip()
        if not seletor:
            return ToolResult.failure("Preciso saber onde clicar.")
        try:
            frase = _clicar(_pagina(), seletor)
        except AcaoRecusada as exc:
            return ToolResult(ok=False, speech=str(exc), data={"recusado": True})
        except (ModeError, BrowserUnavailable) as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"Não consegui clicar: {exc}")
        return ToolResult(ok=True, ack=frase, data={"seletor": seletor})

    # ---------------------------------------------------------- registro

    registry.register(Tool(
        name="abrir_aba",
        description=(
            "Abre um endereço numa aba nova do navegador. Exige o modo "
            "navegador ligado."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Endereço completo.",
                    "audit_mode": "plaintext",
                }
            },
            "required": ["url"],
        },
        handler=abrir_aba,
    ))

    registry.register(Tool(
        name="listar_abas",
        description="Lista as abas abertas no navegador, com título e endereço.",
        parameters={"type": "object", "properties": {}},
        handler=listar_abas,
        fire_and_forget=False,
    ))

    registry.register(Tool(
        name="inspecionar_pagina",
        description=(
            "Analisa a página aberta como um revisor de qualidade: hierarquia "
            "de títulos, imagens sem texto alternativo, campos sem rótulo, "
            "botões sem nome acessível, alvos pequenos demais e formulários. "
            "Use quando pedirem para revisar, auditar ou avaliar uma página."
        ),
        parameters={"type": "object", "properties": {}},
        handler=inspecionar_pagina,
        fire_and_forget=False,
    ))

    registry.register(Tool(
        name="preencher_campo",
        description=(
            "Digita um valor num campo da página aberta. NUNCA funciona em "
            "campo de senha, upload de arquivo ou dado sensível — esses são "
            "recusados pelo sistema, não adianta tentar."
        ),
        parameters={
            "type": "object",
            "properties": {
                "seletor": {
                    "type": "string",
                    "description": "Seletor CSS do campo, vindo de inspecionar_pagina.",
                    "audit_mode": "plaintext",
                },
                "valor": {
                    "type": "string",
                    "description": "O texto a digitar.",
                    # NUNCA plaintext, nem em modo de depuração: aqui passa o
                    # que a pessoa digita num formulário — e-mail, endereço,
                    # nome completo. A anotação vence o modo global de
                    # propósito; trava não tem chave de depuração.
                    "audit_mode": "metadata",
                },
            },
            "required": ["seletor", "valor"],
        },
        handler=preencher_campo,
    ))

    registry.register(Tool(
        name="clicar_em",
        description=(
            "Clica num elemento da página aberta. Use o seletor devolvido por "
            "inspecionar_pagina."
        ),
        parameters={
            "type": "object",
            "properties": {
                "seletor": {
                    "type": "string",
                    "description": "Seletor CSS do elemento.",
                    "audit_mode": "plaintext",
                },
            },
            "required": ["seletor"],
        },
        handler=clicar_em,
    ))
