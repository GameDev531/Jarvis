"""Divisão de texto em sentenças para síntese em streaming.

Por que isto existe: sintetizar a resposta inteira antes de tocar qualquer coisa
faz o usuário esperar pela frase mais longa. Dividindo, o James começa a falar
a primeira sentença enquanto sintetiza a segunda — o que o ouvinte percebe é o
tempo até a PRIMEIRA palavra, não o total.

Os casos que quebram um `text.split(".")` ingênuo em português:
  "Sr. Stark chegou."      -> uma sentença, não duas
  "Custa R$ 1.500,00 hoje" -> ponto de milhar não termina frase
  "Versão 3.5 instalada."  -> ponto decimal não termina frase
  "Espere... já vi."       -> reticências são um único terminador
"""

from __future__ import annotations

import re

# Abreviações comuns em pt-BR: o ponto faz parte da palavra.
_ABBREVIATIONS = frozenset(
    {
        "sr", "sra", "srta", "dr", "dra", "prof", "profa", "eng", "adm",
        "av", "r", "pç", "ap", "apto", "bl", "ed",
        "etc", "ex", "obs", "ref", "cf", "pag", "pág", "fl", "num", "núm", "no", "nº",
        "jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez",
        "seg", "ter", "qua", "qui", "sex", "sáb", "dom",
        "a", "b", "c", "d", "e", "i", "p",  # iniciais e itens de lista: "p. ex."
    }
)

_TERMINATORS = ".!?…"
_SPLIT_FALLBACK = re.compile(r"[;:,]\s")
_WORD_BEFORE = re.compile(r"([\wÀ-ÿ]+)\.?$", re.UNICODE)

DEFAULT_MIN_CHARS = 12
DEFAULT_MAX_CHARS = 240


def _is_abbreviation(text: str, dot_index: int) -> bool:
    match = _WORD_BEFORE.search(text[:dot_index])
    if not match:
        return False
    return match.group(1).lower() in _ABBREVIATIONS


def _is_inside_number(text: str, dot_index: int) -> bool:
    """`1.500` e `3.5`: dígito dos dois lados do ponto."""
    before = text[dot_index - 1] if dot_index > 0 else ""
    after = text[dot_index + 1] if dot_index + 1 < len(text) else ""
    return before.isdigit() and after.isdigit()


def _scan_terminators(text: str) -> list[int]:
    """Posições (exclusivas) onde cada sentença COMPLETA termina."""
    cuts: list[int] = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if char not in _TERMINATORS:
            index += 1
            continue

        # Consome a sequência inteira: "..." e "?!" são um terminador só.
        end = index
        while end + 1 < length and text[end + 1] in _TERMINATORS:
            end += 1

        if char == "." and end == index:
            if _is_inside_number(text, index) or _is_abbreviation(text, index):
                index += 1
                continue

        following = text[end + 1 : end + 2]
        # Terminador seguido de espaço (ou fim do texto) encerra a sentença.
        # Sem isso, "google.com" viraria duas.
        if following and not following.isspace():
            index = end + 1
            continue

        cuts.append(end + 1)
        index = end + 1

    return cuts


def _raw_split(text: str) -> list[str]:
    """Quebra nos terminadores reais de sentença."""
    sentences: list[str] = []
    start = 0
    for cut in _scan_terminators(text):
        piece = text[start:cut].strip()
        if piece:
            sentences.append(piece)
        start = cut
    remainder = text[start:].strip()
    if remainder:
        sentences.append(remainder)
    return sentences


def _break_long(sentence: str, max_chars: int) -> list[str]:
    """Divide uma sentença longa demais em pontos naturais de respiro."""
    if len(sentence) <= max_chars:
        return [sentence]

    pieces: list[str] = []
    remaining = sentence
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        # Melhor corte: última pontuação fraca dentro da janela.
        candidates = [match.end() for match in _SPLIT_FALLBACK.finditer(window)]
        cut = candidates[-1] if candidates else window.rfind(" ")
        if cut <= 0:
            cut = max_chars  # palavra única gigante: corte duro
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return [piece for piece in pieces if piece]


def split_sentences(
    text: str,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
    merge_tail: bool = True,
) -> list[str]:
    """Divide o texto em blocos prontos para o TTS.

    Blocos muito curtos são fundidos com o seguinte (fragmento solto sai com
    prosódia estranha) e blocos longos demais são quebrados (senão a primeira
    fala demora).

    `merge_tail=False` é usado no streaming: ali o último bloco costuma ser uma
    frase ainda incompleta, que precisa continuar separada para o chamador
    segurá-la até o resto do texto chegar.
    """
    if not text or not text.strip():
        return []
    if max_chars < min_chars:
        raise ValueError("max_chars precisa ser >= min_chars")

    normalized = " ".join(text.split())
    sentences = _raw_split(normalized)
    if not sentences:
        return []

    merged: list[str] = []
    for sentence in sentences:
        if merged and len(merged[-1]) < min_chars:
            merged[-1] = f"{merged[-1]} {sentence}".strip()
        else:
            merged.append(sentence)

    # Um último fragmento curto demais gruda no anterior em vez de virar bloco.
    if merge_tail and len(merged) > 1 and len(merged[-1]) < min_chars:
        tail = merged.pop()
        merged[-1] = f"{merged[-1]} {tail}".strip()

    result: list[str] = []
    for sentence in merged:
        result.extend(_break_long(sentence, max_chars))
    return result


def split_stream(
    buffer: str,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[list[str], str]:
    """Separa o que já dá para falar do que ainda está chegando.

    Devolve `(sentenças completas, resto)`. O resto é um pedaço EXATO do buffer
    original, com os espaços preservados — normalizá-lo aqui apagaria o espaço
    que separa a última sentença da próxima, e "…completa. " + "Terceira…"
    viraria "…completa.Terceira", que o divisor então trata como uma frase só
    (mesma regra que impede "google.com" de virar duas).
    """
    if not buffer:
        return [], ""

    cuts = _scan_terminators(buffer)
    if not cuts:
        return [], buffer

    head, remainder = buffer[: cuts[-1]], buffer[cuts[-1] :]

    # Um trecho curto demais espera companhia, em vez de virar um bloco de
    # síntese com prosódia estranha — desde que ainda haja texto por vir.
    if remainder.strip() and len(head.strip()) < min_chars:
        return [], buffer

    return split_sentences(head, min_chars, max_chars), remainder
