"""Confirmação de ações de risco — casamento determinístico, sem LLM (C2).

Por que isto não pode passar pelo modelo: se o LLM é quem julga "o usuário
confirmou?", uma prompt injection vinda de uma página web pode forjar a
confirmação e destravar uma ação de Nível 2. A decisão fica em Python, contra
listas fixas vindas do config.yaml.

Política, nesta ordem:
  1. qualquer palavra de negação presente  -> NÃO  (negação sempre vence)
  2. senão, qualquer palavra de aprovação  -> SIM
  3. senão                                  -> AMBÍGUO
E o chamador trata AMBÍGUO e timeout como negação.
"""

from __future__ import annotations

import re
from enum import Enum

from james.config import normalize_text


class Confirmation(Enum):
    YES = "yes"
    NO = "no"
    AMBIGUOUS = "ambiguous"


def _build_pattern(phrases: list[str]) -> re.Pattern[str] | None:
    """Regex de frases inteiras, com fronteira de palavra.

    A fronteira é o que impede "sim" de casar dentro de "simplesmente" e
    "nao" dentro de "naotoriedade". Frases com espaço aceitam espaçamento
    variável, porque a transcrição nem sempre é consistente.
    """
    cleaned: list[str] = []
    for phrase in phrases or []:
        normalized = normalize_text(str(phrase))
        if normalized:
            cleaned.append(normalized)
    if not cleaned:
        return None
    # Frases mais longas primeiro: "melhor nao" antes de "nao".
    cleaned.sort(key=len, reverse=True)
    alternatives = [r"\s+".join(re.escape(word) for word in phrase.split()) for phrase in cleaned]
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")\b")


class ConfirmationMatcher:
    """Casador reutilizável — compila as listas do config uma única vez."""

    def __init__(self, yes_words: list[str], no_words: list[str]) -> None:
        self._yes = _build_pattern(yes_words)
        self._no = _build_pattern(no_words)

    def classify(self, transcript: str | None) -> Confirmation:
        if transcript is None:
            # Sem áudio / timeout / falha de transcrição: nunca assume aprovação.
            return Confirmation.NO
        text = normalize_text(transcript)
        if not text:
            return Confirmation.AMBIGUOUS
        if self._no is not None and self._no.search(text):
            return Confirmation.NO
        if self._yes is not None and self._yes.search(text):
            return Confirmation.YES
        return Confirmation.AMBIGUOUS


def classify_confirmation(
    transcript: str | None,
    yes_words: list[str],
    no_words: list[str],
) -> Confirmation:
    """Versão de uso único. Para chamadas repetidas, use ConfirmationMatcher."""
    return ConfirmationMatcher(yes_words, no_words).classify(transcript)
