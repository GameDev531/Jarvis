"""Higiene de conteúdo externo antes de entrar no histórico do LLM (A3).

O guard protege as AÇÕES; este módulo protege o CONTEXTO. Todo resultado de
tool que carrega texto de fora (página web, tela, busca) passa por aqui.

Isto é defesa em profundidade, não substituto do guard: mesmo que uma injection
convença o modelo, o guard determinístico continua sendo quem decide se a ação
roda. Ver james/permissions/guard.py.
"""

from __future__ import annotations

import re
import unicodedata

# Caracteres invisíveis usados para contrabandear instruções: zero-width,
# joiners, BOM e os controles de direção do ataque "Trojan Source".
_INVISIBLE = re.compile(
    "["
    "­"           # soft hyphen
    "​-‏"    # zero-width space/joiners + marcas LTR/RTL
    "‪-‮"    # overrides de direção (Trojan Source)
    "⁠-⁤"    # word joiner e invisíveis matemáticos
    "⁦-⁩"    # isolates de direção
    "﻿"           # BOM
    "]"
)

_DEFAULT_TAG = "resultado_externo"
_DEFAULT_MAX_CHARS = 4000


def strip_dangerous_chars(text: str) -> str:
    """Remove controles e invisíveis, preservando quebra de linha e tabulação."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE.sub("", text)
    cleaned = []
    for char in text:
        if char in "\n\t":
            cleaned.append(char)
            continue
        # Cc = controles, Cf = formatação invisível, Cs = surrogates órfãos
        if unicodedata.category(char) in ("Cc", "Cf", "Cs"):
            continue
        cleaned.append(char)
    return "".join(cleaned)


def _neutralize_tag(text: str, tag: str) -> str:
    """Impede que o conteúdo feche a própria tag e "escape" do envelope."""
    pattern = re.compile(rf"</?\s*{re.escape(tag)}\b[^>]*>?", re.IGNORECASE)
    return pattern.sub("[tag-removida]", text)


def _escape_attribute(value: str) -> str:
    """Deixa a origem segura para viver dentro de aspas duplas no envelope."""
    value = strip_dangerous_chars(str(value))
    value = value.replace("&", "&amp;").replace('"', "&quot;")
    value = value.replace("<", "&lt;").replace(">", "&gt;")
    value = " ".join(value.split())
    if len(value) > 200:
        value = value[:197] + "..."
    return value


def sanitize_external(
    content: object,
    origin: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
    tag: str = _DEFAULT_TAG,
) -> str:
    """Envelopa conteúdo externo como DADO, nunca como instrução.

    - remove invisíveis/controles usados para esconder instruções;
    - neutraliza qualquer tentativa de fechar a tag do envelope;
    - trunca (protege contexto e cota de tokens), avisando que truncou;
    - devolve `<tag origem="...">...</tag>`.

    O system prompt instrui o modelo a tratar tudo dentro da tag como dado.
    """
    if max_chars <= 0:
        raise ValueError("max_chars deve ser positivo")

    text = "" if content is None else str(content)
    text = strip_dangerous_chars(text)
    text = _neutralize_tag(text, tag)
    text = text.strip()

    if not text:
        text = "(vazio)"

    original_length = len(text)
    if original_length > max_chars:
        text = text[:max_chars].rstrip()
        text += f"\n[...truncado: {original_length - max_chars} caracteres omitidos]"

    return f'<{tag} origem="{_escape_attribute(origin)}">\n{text}\n</{tag}>'
