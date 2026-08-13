"""Captura de tela feita na thread certa.

`QScreen.grabWindow` só pode ser chamado da thread da interface. O pipeline de
voz roda numa thread de trabalho, então a chamada precisa atravessar: o pedido
vira um sinal Qt (que o Qt enfileira na thread dona do objeto), e a thread de
trabalho espera o resultado. Chamar direto de outra thread trava ou devolve
imagem corrompida, dependendo da plataforma.

O objeto precisa ser criado na thread da interface para que o sinal seja
entregue lá.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal, Slot

from james.logs import get_logger

logger = get_logger("james.ui.capture")

_GRAB_TIMEOUT_S = 8.0
# Reduzir antes de enviar: a tela cheia em PNG passa de 2 MB, o que é upload
# desnecessário e tokens desnecessários. Nesta largura o texto continua legível
# para o modelo.
_MAX_WIDTH = 1280


class ScreenGrabber(QObject):
    _requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._result: bytes | None = None
        self._error: str | None = None
        self._done = threading.Event()
        self._lock = threading.Lock()
        # Conexão automática: emitido de outra thread, executado na thread dona.
        self._requested.connect(self._grab_on_gui_thread)

    def grab_png(self, timeout: float = _GRAB_TIMEOUT_S) -> bytes:
        """Captura a tela inteira como PNG. Levanta RuntimeError se falhar."""
        with self._lock:
            self._done.clear()
            self._result = None
            self._error = None
            self._requested.emit()

            if not self._done.wait(timeout):
                raise RuntimeError(
                    f"A captura de tela não respondeu em {timeout:.0f}s."
                )
            if self._error:
                raise RuntimeError(self._error)
            if not self._result:
                raise RuntimeError("A captura de tela veio vazia.")
            return self._result

    @Slot()
    def _grab_on_gui_thread(self) -> None:
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
            if screen is None:
                self._error = "Nenhuma tela disponível para capturar."
                return

            pixmap = screen.grabWindow(0)
            if pixmap.isNull():
                self._error = "O sistema não permitiu a captura de tela."
                return

            if pixmap.width() > _MAX_WIDTH:
                pixmap = pixmap.scaledToWidth(
                    _MAX_WIDTH, Qt.SmoothTransformation
                )

            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.WriteOnly)
            saved = pixmap.save(buffer, "PNG")
            buffer.close()

            if not saved:
                self._error = "Não consegui codificar a captura de tela."
                return
            self._result = bytes(data)
            logger.debug("Tela capturada: %d KB", len(self._result) // 1024)
        except Exception as exc:  # noqa: BLE001 — o erro precisa voltar, não subir
            self._error = f"Falha ao capturar a tela: {exc}"
        finally:
            self._done.set()
