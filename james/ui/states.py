"""Estados visíveis do James e a tradução deles para a interface.

A tabela de legendas implementa a regra de "esconder o mecanismo" (seção 12) no
lado da UI: nenhuma etapa interna aparece como é — "chamando pesquisar_web"
vira "Executando". A mesma regra existe no system_prompt para o que é falado.

A paleta muda de ciano para âmbar quando o serviço está degradado, para
sinalizar limitação sem precisar avisar por voz toda hora (A5).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UiState(Enum):
    HIDDEN = "hidden"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CONFIRMING = "confirming"
    EXECUTING = "executing"
    ERROR = "error"


_CAPTIONS = {
    UiState.HIDDEN: "",
    UiState.LISTENING: "Ouvindo",
    UiState.THINKING: "Processando",
    UiState.SPEAKING: "Respondendo",
    UiState.CONFIRMING: "Aguardando confirmação",
    UiState.EXECUTING: "Executando",
    UiState.ERROR: "Capacidade limitada",
}


@dataclass(frozen=True)
class Palette:
    """Cores em RGB. O overlay aplica alfa conforme a animação."""

    core: tuple[int, int, int]
    glow: tuple[int, int, int]
    text: tuple[int, int, int]
    background: tuple[int, int, int]


_NORMAL = Palette(
    core=(140, 235, 255),
    glow=(0, 170, 225),
    text=(190, 240, 255),
    background=(4, 12, 20),
)
_DEGRADED = Palette(
    core=(255, 205, 130),
    glow=(225, 145, 20),
    text=(255, 225, 180),
    background=(20, 12, 4),
)
_ALERT = Palette(
    core=(255, 165, 165),
    glow=(220, 70, 70),
    text=(255, 210, 210),
    background=(20, 6, 6),
)


def caption_for(state: UiState) -> str:
    return _CAPTIONS.get(state, "")


def palette_for(state: UiState, degraded: bool = False) -> Palette:
    if state is UiState.ERROR:
        return _ALERT
    return _DEGRADED if degraded else _NORMAL


# Intensidade da pulsação por estado: parado quando ouve, agitado quando pensa.
PULSE_RATE = {
    UiState.LISTENING: 0.9,
    UiState.THINKING: 2.4,
    UiState.SPEAKING: 1.6,
    UiState.CONFIRMING: 1.2,
    UiState.EXECUTING: 2.0,
    UiState.ERROR: 0.5,
    UiState.HIDDEN: 0.0,
}
