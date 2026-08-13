"""HUD flutuante — modo alternativo da interface (`interface.mode: hud`).

É o painel pequeno, sem borda, sempre no topo e transparente a cliques, com a
transição de "TV de tubo" na entrada e na saída.

Custa mais que a janela comum: exige composição de tela ativa e faz o
compositor redesenhar o que está por baixo a cada quadro. Numa máquina modesta
isso pesa no sistema inteiro, não só no James — por isso o padrão é
`interface.mode: window`. Use este modo se a sua máquina aguenta e você quer a
estética.

Sobre threads: o orquestrador roda numa thread de trabalho e a interface Qt na
thread principal. Toda comunicação passa por sinais Qt, que são enfileirados
automaticamente na thread certa — chamar métodos do widget direto de outra
thread corromperia o estado interno do Qt.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from james.logs import get_logger
from james.ui.painting import (
    angular_path,
    draw_orb,
    draw_scan_ring,
    draw_scanlines,
    draw_vignette,
    ease_in,
    ease_out,
    pulse,
)
from james.ui.states import PULSE_RATE, UiState, caption_for, palette_for

logger = get_logger("james.ui.overlay")

# Duração das transições de entrada/saída, em segundos.
_OPEN_SECONDS = 0.42
_CLOSE_SECONDS = 0.30
# Abaixo disso o painel virou praticamente a linha de brilho central.
_LINE_THRESHOLD = 0.35


class Overlay(QWidget):
    """HUD sem borda, sempre no topo, que não intercepta o mouse."""

    request_state = Signal(str, bool)
    request_caption = Signal(str)
    request_hide = Signal()
    request_transcript = Signal(str, str)
    request_quota = Signal(dict)

    cancel_requested = Signal()
    toggle_listening = Signal()

    def __init__(
        self,
        app_name: str = "James",
        size: int = 260,
        position: str = "bottom-right",
        margin: int = 40,
        fps: int = 30,
        **_ignored,
    ) -> None:
        super().__init__(None)
        self.app_name = app_name
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                        # fora da barra de tarefas e do Alt+Tab
            | Qt.WindowTransparentForInput   # cliques atravessam para o app de baixo
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._panel_size = max(140, int(size))
        self._position = position
        self._margin = max(0, int(margin))
        self.resize(self._panel_size, int(self._panel_size * 0.82))

        self._state = UiState.HIDDEN
        self._degraded = False
        self._caption = ""
        self._subtitle = ""

        self._open_amount = 0.0
        self._target_open = 0.0
        self._flicker = 0.0

        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_tick = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(max(16, int(1000 / max(1, int(fps)))))
        self._timer.timeout.connect(self._tick)

        self.request_state.connect(self._on_state)
        self.request_caption.connect(self._on_caption)
        self.request_hide.connect(self._on_hide)
        self.request_transcript.connect(self._on_transcript)
        self.request_quota.connect(lambda _: None)   # o HUD não exibe cota

        self._place()

    # ------------------------------------------------------------ posição

    def _place(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width, height = self.width(), self.height()
        margin = self._margin

        positions = {
            "bottom-right": (area.right() - width - margin, area.bottom() - height - margin),
            "bottom-center": (area.center().x() - width // 2, area.bottom() - height - margin),
            "top-right": (area.right() - width - margin, area.top() + margin),
            "center": (area.center().x() - width // 2, area.center().y() - height // 2),
        }
        x, y = positions.get(self._position, positions["bottom-right"])
        self.move(int(x), int(y))

    # -------------------------------------------------------------- slots

    def _on_state(self, state_value: str, degraded: bool) -> None:
        try:
            state = UiState(state_value)
        except ValueError:
            logger.warning("Estado de interface desconhecido: %r", state_value)
            return

        self._degraded = bool(degraded)
        if state is UiState.HIDDEN:
            self._on_hide()
            return

        if self._state is UiState.HIDDEN:
            self._subtitle = ""
        self._state = state
        self._caption = caption_for(state)

        self._target_open = 1.0
        if not self.isVisible():
            self._place()
            self.show()
        if not self._timer.isActive():
            self._last_tick = self._clock.elapsed() / 1000.0
            self._timer.start()
        self.update()

    def _on_caption(self, text: str) -> None:
        """Linha secundária: um trecho curto do que o James está dizendo."""
        cleaned = " ".join(str(text or "").split())
        self._subtitle = cleaned if len(cleaned) <= 90 else cleaned[:87] + "..."
        self.update()

    def _on_transcript(self, who: str, text: str) -> None:
        # Sem espaço para histórico: o HUD mostra apenas a última fala.
        if who in ("james", "sistema"):
            self._on_caption(text)

    def _on_hide(self) -> None:
        if self._state is UiState.HIDDEN and not self.isVisible():
            return
        self._state = UiState.HIDDEN
        self._target_open = 0.0
        if not self._timer.isActive():
            self._last_tick = self._clock.elapsed() / 1000.0
            self._timer.start()

    def set_listening(self, listening: bool) -> None:
        """Presente para casar a interface com a da janela comum."""

    # ------------------------------------------------------------ animação

    def _tick(self) -> None:
        now = self._clock.elapsed() / 1000.0
        # Delta real do relógio, não um passo fixo: assumir 60 quadros por
        # segundo faz o piscar sair em ritmo errado numa máquina mais lenta.
        delta = max(0.0, min(0.25, now - self._last_tick))
        self._last_tick = now

        if self._target_open > self._open_amount:
            self._open_amount = min(1.0, self._open_amount + delta / _OPEN_SECONDS)
        elif self._target_open < self._open_amount:
            self._open_amount = max(0.0, self._open_amount - delta / _CLOSE_SECONDS)

        self._flicker = (self._flicker + delta * 11.0) % (math.pi * 2)

        if self._open_amount <= 0.0 and self._target_open <= 0.0:
            self._timer.stop()
            self.hide()
            return

        self.update()

    # ------------------------------------------------------------- pintura

    def paintEvent(self, event) -> None:  # noqa: N802 — assinatura do Qt
        if self._open_amount <= 0.001:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        rect = QRectF(self.rect())
        palette = palette_for(self._state, self._degraded)
        elapsed = self._clock.elapsed() / 1000.0
        opening = self._target_open > 0.0
        amount = ease_out(self._open_amount) if opening else ease_in(self._open_amount)

        if amount > 0.02:
            painter.save()
            center = rect.center()
            painter.translate(center)
            # Colapso vertical: o painel esmaga em direção a uma linha, como um
            # tubo de imagem sendo desligado.
            painter.scale(1.0, max(0.02, amount))
            painter.translate(-center)
            self._draw_panel(painter, rect, palette, elapsed, amount)
            painter.restore()

        if self._open_amount < _LINE_THRESHOLD:
            self._draw_collapse_line(painter, rect, palette)

        draw_scanlines(painter, rect)
        draw_vignette(painter, rect)

    def _draw_panel(self, painter, rect, palette, elapsed, amount) -> None:
        background = QColor(*palette.background)
        background.setAlpha(int(190 * amount))
        painter.setBrush(background)
        painter.setPen(Qt.NoPen)
        painter.drawPath(angular_path(rect.adjusted(2, 2, -2, -2), 14))

        border = QColor(*palette.glow)
        border.setAlpha(int(150 * amount))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(border, 1.4))
        painter.drawPath(angular_path(rect.adjusted(2, 2, -2, -2), 14))

        orb_radius = rect.width() * 0.19
        orb_center = QPointF(rect.center().x(), rect.top() + rect.height() * 0.40)
        intensity = pulse(elapsed, PULSE_RATE.get(self._state, 1.0))
        flicker = 0.94 + 0.06 * math.sin(self._flicker)

        draw_orb(painter, orb_center, orb_radius, palette, intensity, amount, flicker)

        if self._state in (UiState.THINKING, UiState.EXECUTING):
            draw_scan_ring(painter, orb_center, orb_radius, palette, elapsed, amount)

        text_color = QColor(*palette.text)
        text_color.setAlpha(int(235 * amount))
        painter.setPen(text_color)

        caption_font = QFont(painter.font())
        caption_font.setPointSizeF(max(8.0, rect.width() * 0.052))
        caption_font.setLetterSpacing(QFont.PercentageSpacing, 118)
        caption_font.setBold(True)
        painter.setFont(caption_font)
        caption_rect = QRectF(
            rect.left(), rect.top() + rect.height() * 0.63, rect.width(), rect.height() * 0.14
        )
        painter.drawText(caption_rect, Qt.AlignCenter, self._caption.upper())

        if self._subtitle:
            subtitle_color = QColor(*palette.text)
            subtitle_color.setAlpha(int(165 * amount))
            painter.setPen(subtitle_color)
            subtitle_font = QFont(painter.font())
            subtitle_font.setPointSizeF(max(7.0, rect.width() * 0.040))
            subtitle_font.setBold(False)
            painter.setFont(subtitle_font)
            subtitle_rect = QRectF(
                rect.left() + rect.width() * 0.08,
                rect.top() + rect.height() * 0.77,
                rect.width() * 0.84,
                rect.height() * 0.19,
            )
            painter.drawText(
                subtitle_rect, int(Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap), self._subtitle
            )

    def _draw_collapse_line(self, painter, rect, palette) -> None:
        """A linha brilhante do colapso, que some num ponto central."""
        progress = self._open_amount / _LINE_THRESHOLD  # 0 = ponto, 1 = linha cheia
        half_width = (rect.width() * 0.46) * max(0.02, progress)
        alpha = int(255 * (1.0 - abs(progress - 0.5) * 1.2))
        if alpha <= 0:
            return

        color = QColor(*palette.core)
        color.setAlpha(max(0, min(255, alpha)))
        center_y = rect.center().y()
        painter.setPen(QPen(color, 2.2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(
            QPointF(rect.center().x() - half_width, center_y),
            QPointF(rect.center().x() + half_width, center_y),
        )

        glow = QRadialGradient(QPointF(rect.center().x(), center_y), rect.width() * 0.22)
        hot = QColor(*palette.core)
        hot.setAlpha(max(0, min(255, int(alpha * 0.65))))
        cold = QColor(*palette.core)
        cold.setAlpha(0)
        glow.setColorAt(0.0, hot)
        glow.setColorAt(1.0, cold)
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(
            QPointF(rect.center().x(), center_y), rect.width() * 0.22, rect.height() * 0.09
        )
