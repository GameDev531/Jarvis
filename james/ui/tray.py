"""Ícone de bandeja — o único ponto de controle visível do James.

O ícone é desenhado em código em vez de carregado de arquivo: evita um asset
binário no repositório e garante que o James sempre tenha ícone, mesmo numa
instalação incompleta.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from james.logs import get_logger
from james.ui.states import UiState, caption_for, palette_for

logger = get_logger("james.ui.tray")


def build_icon(degraded: bool = False, size: int = 64) -> QIcon:
    """Desenha o orbe do James como ícone."""
    palette = palette_for(UiState.LISTENING, degraded)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    center = QPointF(size / 2, size / 2)

    gradient = QRadialGradient(center, size * 0.48)
    core = QColor(*palette.core)
    mid = QColor(*palette.glow)
    mid.setAlpha(160)
    edge = QColor(*palette.glow)
    edge.setAlpha(0)
    gradient.setColorAt(0.0, core)
    gradient.setColorAt(0.5, mid)
    gradient.setColorAt(1.0, edge)

    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(QRectF(0, 0, size, size))

    painter.setBrush(QColor(*palette.core))
    painter.drawEllipse(center, size * 0.16, size * 0.16)
    painter.end()

    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    toggle_listening = Signal()
    quit_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, app_name: str = "James", parent=None) -> None:
        super().__init__(parent)
        self.app_name = app_name
        self._listening = True
        self._degraded = False

        self.setIcon(build_icon())
        self._menu = QMenu()

        self._status_action = QAction(f"{app_name} — iniciando", self._menu)
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)
        self._menu.addSeparator()

        self._toggle_action = QAction("Pausar escuta", self._menu)
        self._toggle_action.triggered.connect(self.toggle_listening.emit)
        self._menu.addAction(self._toggle_action)

        cancel_action = QAction("Cancelar ação atual", self._menu)
        cancel_action.triggered.connect(self.cancel_requested.emit)
        self._menu.addAction(cancel_action)

        self._menu.addSeparator()
        quit_action = QAction("Sair", self._menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(quit_action)

        self.setContextMenu(self._menu)
        self.setToolTip(app_name)

    def set_state(self, state: UiState, degraded: bool = False) -> None:
        caption = caption_for(state) or ("Escutando" if self._listening else "Pausado")
        suffix = " (capacidade limitada)" if degraded else ""
        self._status_action.setText(f"{self.app_name} — {caption}{suffix}")
        self.setToolTip(f"{self.app_name} — {caption}{suffix}")
        if degraded != self._degraded:
            self._degraded = degraded
            self.setIcon(build_icon(degraded))

    def set_listening(self, listening: bool) -> None:
        self._listening = bool(listening)
        self._toggle_action.setText("Pausar escuta" if listening else "Retomar escuta")

    def notify(self, title: str, message: str) -> None:
        if not self.supportsMessages():
            logger.info("%s: %s", title, message)
            return
        self.showMessage(title, message, build_icon(self._degraded), 4000)
