"""Overlay HUD desenhado com QPainter puro.

Por que não QWebEngineView aqui (E4 do plano): um QWebEngineView é um Chromium
embutido — algo entre 150 e 300 MB de RAM e um processo de GPU só para mostrar
um HUD, competindo com o pipeline de voz justamente na máquina que já é o
gargalo. Um QWidget com QPainter entrega orbe, legenda e a transição de "TV de
tubo" por um custo perto de zero. O QWebEngine entra quando holograma e painéis
dinâmicos justificarem o preço.

Sobre threads: o orquestrador roda numa thread de trabalho e a interface Qt na
thread principal. Toda comunicação passa por sinais Qt, que são enfileirados
automaticamente na thread certa — chamar métodos do widget direto de outra
thread corromperia o estado interno do Qt.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QElapsedTimer, QPointF, QRectF, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from james.logs import get_logger
from james.ui.states import PULSE_RATE, UiState, caption_for, palette_for

logger = get_logger("james.ui.overlay")

# Duração das transições de entrada/saída, em segundos.
_OPEN_SECONDS = 0.42
_CLOSE_SECONDS = 0.30
# Abaixo disso o painel virou praticamente a linha de brilho central.
_LINE_THRESHOLD = 0.35


def _ease_out(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _ease_in(t: float) -> float:
    return t * t * t


def _pulse(elapsed: float, rate: float) -> float:
    """Pulsação por ondas sobrepostas, entre 0 e 1.

    Um seno simples fica mecânico e previsível. Somando frequências diferentes
    e elevando uma delas a uma potência alta (surtos raros e súbitos), o brilho
    ganha uma respiração que não parece um loop.
    """
    if rate <= 0:
        return 0.5
    base = 0.5 + 0.5 * math.sin(elapsed * rate)
    fast = 0.5 + 0.5 * math.sin(elapsed * rate * 2.7 + 1.3)
    surge = max(0.0, math.sin(elapsed * rate * 0.37)) ** 8
    return min(1.0, 0.5 * base + 0.22 * fast + 0.35 * surge)


class Overlay(QWidget):
    """HUD sem borda, sempre no topo, que não intercepta o mouse."""

    # Emitidos de outras threads; o Qt entrega na thread da interface.
    request_state = Signal(str, bool)   # estado, degradado
    request_caption = Signal(str)
    request_hide = Signal()

    def __init__(
        self,
        size: int = 260,
        position: str = "bottom-right",
        margin: int = 40,
        fps: int = 30,
    ) -> None:
        super().__init__(None)
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

        interval = max(16, int(1000 / max(1, int(fps))))
        self._timer = QTimer(self)
        self._timer.setInterval(interval)
        self._timer.timeout.connect(self._tick)

        self.request_state.connect(self._on_state)
        self.request_caption.connect(self._on_caption)
        self.request_hide.connect(self._on_hide)

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

        first_show = self._state is UiState.HIDDEN
        self._state = state
        self._caption = caption_for(state)
        if first_show:
            self._subtitle = ""

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

    def _on_hide(self) -> None:
        if self._state is UiState.HIDDEN and not self.isVisible():
            return
        self._state = UiState.HIDDEN
        self._target_open = 0.0
        if not self._timer.isActive():
            self._last_tick = self._clock.elapsed() / 1000.0
            self._timer.start()

    # ------------------------------------------------------------ animação

    def _tick(self) -> None:
        now = self._clock.elapsed() / 1000.0
        # Delta real do relógio, não um passo fixo: assumir 60 fps faz o
        # piscar sair em ritmo errado numa máquina mais lenta.
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
        amount = _ease_out(self._open_amount) if opening else _ease_in(self._open_amount)

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

        self._draw_scanlines(painter, rect)
        self._draw_vignette(painter, rect)

    def _draw_panel(self, painter, rect, palette, elapsed, amount) -> None:
        # --- fundo do painel, com cantos angulares ---
        background = QColor(*palette.background)
        background.setAlpha(int(190 * amount))
        painter.setBrush(background)
        painter.setPen(Qt.NoPen)
        painter.drawPath(_angular_path(rect.adjusted(2, 2, -2, -2), 14))

        border = QColor(*palette.glow)
        border.setAlpha(int(150 * amount))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(border, 1.4))
        painter.drawPath(_angular_path(rect.adjusted(2, 2, -2, -2), 14))

        # --- orbe ---
        orb_radius = rect.width() * 0.19
        orb_center = QPointF(rect.center().x(), rect.top() + rect.height() * 0.40)
        intensity = _pulse(elapsed, PULSE_RATE.get(self._state, 1.0))
        flicker = 0.94 + 0.06 * math.sin(self._flicker)

        halo_radius = orb_radius * (1.9 + 0.55 * intensity)
        gradient = QRadialGradient(orb_center, halo_radius)
        core = QColor(*palette.core)
        core.setAlpha(int((215 * (0.55 + 0.45 * intensity)) * amount * flicker))
        mid = QColor(*palette.glow)
        mid.setAlpha(int(110 * amount * intensity))
        edge = QColor(*palette.glow)
        edge.setAlpha(0)
        gradient.setColorAt(0.0, core)
        gradient.setColorAt(0.42, mid)
        gradient.setColorAt(1.0, edge)
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(orb_center, halo_radius, halo_radius)

        # Núcleo sólido, que dá o "ponto de luz" no centro do halo.
        solid = QColor(*palette.core)
        solid.setAlpha(int(235 * amount * flicker))
        painter.setBrush(solid)
        painter.drawEllipse(orb_center, orb_radius * 0.42, orb_radius * 0.42)

        # --- anel de varredura, só quando há trabalho acontecendo ---
        if self._state in (UiState.THINKING, UiState.EXECUTING):
            ring = QColor(*palette.core)
            ring.setAlpha(int(200 * amount))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(ring, 2.0, Qt.SolidLine, Qt.RoundCap))
            box = QRectF(
                orb_center.x() - orb_radius * 1.55,
                orb_center.y() - orb_radius * 1.55,
                orb_radius * 3.1,
                orb_radius * 3.1,
            )
            start_angle = int((-elapsed * 210) % 360) * 16
            painter.drawArc(box, start_angle, 110 * 16)

        # --- textos ---
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

    def _draw_scanlines(self, painter, rect) -> None:
        """Linhas horizontais de tubo. Custo desprezível, efeito grande."""
        line = QColor(0, 0, 0, 46)
        painter.setPen(QPen(line, 1.0))
        y = rect.top()
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += 3.0

    def _draw_vignette(self, painter, rect) -> None:
        gradient = QRadialGradient(rect.center(), max(rect.width(), rect.height()) * 0.62)
        gradient.setColorAt(0.55, QColor(0, 0, 0, 0))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 120))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRect(rect)


def _angular_path(rect: QRectF, cut: float) -> QPainterPath:
    """Retângulo com cantos chanfrados — a silhueta angular do HUD."""
    path = QPainterPath()
    left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
    cut = min(cut, rect.width() / 3.0, rect.height() / 3.0)
    path.moveTo(left + cut, top)
    path.lineTo(right - cut, top)
    path.lineTo(right, top + cut)
    path.lineTo(right, bottom - cut)
    path.lineTo(right - cut, bottom)
    path.lineTo(left + cut, bottom)
    path.lineTo(left, bottom - cut)
    path.lineTo(left, top + cut)
    path.closeSubpath()
    return path
