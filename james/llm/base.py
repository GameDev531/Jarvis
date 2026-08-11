"""Contratos compartilhados entre os provedores de LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from james.llm.history import ToolCall

# Sinais de estouro de cota nas mensagens de erro dos provedores.
_QUOTA_MARKERS = re.compile(
    r"\b(429|resource[_ ]exhausted|rate[_ ]?limit|quota|too many requests)\b",
    re.IGNORECASE,
)


class ProviderError(RuntimeError):
    """Falha ao obter resposta deste provedor."""


class QuotaExceeded(ProviderError):
    """Cota/limite atingido — tentar outro provedor pode funcionar."""


class NoProviderAvailable(RuntimeError):
    """Todos os provedores falharam: hora do modo degradado."""


def looks_like_quota_error(exc: BaseException) -> bool:
    """Reconhece estouro de cota sem depender do tipo de exceção do SDK.

    Cada SDK usa uma hierarquia diferente e ela muda entre versões; o código
    numérico e a mensagem são o que se mantém estável.
    """
    for attribute in ("code", "status_code"):
        value = getattr(exc, attribute, None)
        if value == 429 or str(value) == "429":
            return True
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    return bool(_QUOTA_MARKERS.search(str(exc)))


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""
    model: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class ToolSchema:
    """Declaração de uma tool para o LLM.

    `parameters` segue o subconjunto de JSON Schema aceito pelos provedores:
    type/properties/required/enum/description.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})

    def to_gemini(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


TextCallback = Callable[[str], None]
