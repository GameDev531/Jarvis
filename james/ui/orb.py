"""O orbe pulsante, como widget reutilizável.

Só anima quando está visível e num estado ativo: um widget parado não gasta
quadro nenhum, o que importa numa máquina onde o pipeline de voz é quem deve
ficar com a CPU.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from james.ui.painting import draw_orb, draw_scan_ring, pulse
from james.ui.states import PULSE_RATE, UiState, palette_for


class OrbWidget(QWidget):
    def __init__(self, parent=None, fps: int = 30, height: int = 150) -> None:
        super().__init__(parent)
        self.setMinimumHeight(height)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._state = UiState.HIDDEN
        self._degraded = False
        self._flicker = 0.0

        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_tick = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(max(16, int(1000 / max(1, fps))))
        self._timer.timeout.connect(self._tick)

    def set_state(self, state: UiState, degraded: bool = False) -> None:
        self._state = state
        self._degraded = degraded
        # Estado sem movimento não precisa de quadros: o temporizador para.
        if state is UiState.HIDDEN:
            self._timer.stop()
        elif not self._timer.isActive():
            self._last_tick = self._clock.elapsed() / 1000.0
            self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        now = self._clock.elapsed() / 1000.0
        # Delta real do relógio: assumir 60 quadros por segundo faria o piscar
        # sair em ritmo errado numa máquina mais lenta.
        delta = max(0.0, min(0.25, now - self._last_tick))
        self._last_tick = now
        self._flicker = (self._flicker + delta * 11.0) % (math.pi * 2)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — assinatura do Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())

        palette = palette_for(self._state, self._degraded)
        painter.fillRect(rect, QColor(*palette.background))

        if self._state is UiState.HIDDEN:
            return

        elapsed = self._clock.elapsed() / 1000.0
        intensity = pulse(elapsed, PULSE_RATE.get(self._state, 1.0))
        flicker = 0.94 + 0.06 * math.sin(self._flicker)
        center = QPointF(rect.center().x(), rect.center().y())
        radius = min(rect.width(), rect.height()) * 0.20

        draw_orb(painter, center, radius, palette, intensity, flicker=flicker)

        if self._state in (UiState.THINKING, UiState.EXECUTING):
            draw_scan_ring(painter, center, radius, palette, elapsed)
