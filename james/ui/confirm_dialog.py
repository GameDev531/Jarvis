"""Confirmação por diálogo — a alternativa à confirmação por voz.

Quando entra em cena:
  - o whisper.cpp não está disponível (sem ele, a confirmação por voz é
    impossível e, sem esta janela, toda ação de Nível 2 seria recusada);
  - o usuário configurou `permissions.confirm.mode: dialog`;
  - há PIN configurado, caso em que a digitação é a prova.

Assim como a captura de tela, o diálogo precisa abrir na thread da interface,
enquanto o pipeline de voz espera numa thread de trabalho. O pedido atravessa
por sinal Qt e a thread de trabalho bloqueia até haver resposta.

A regra de ouro continua: qualquer coisa que não seja um "sim" explícito nega.
Fechar a janela, timeout, PIN errado — tudo leva a não executar.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QObject, Signal, Slot

from james.logs import get_logger

logger = get_logger("james.ui.confirm")

_DEFAULT_TIMEOUT_S = 30.0


class ConfirmDialogBridge(QObject):
    """Abre o diálogo na thread da interface e devolve a resposta."""

    _requested = Signal(str, bool)

    def __init__(self, parent=None, app_name: str = "James") -> None:
        super().__init__(parent)
        self.app_name = app_name
        self._result = False
        self._pin_typed = ""
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._requested.connect(self._show)

    def ask(
        self, question: str, require_pin: bool = False, timeout: float = _DEFAULT_TIMEOUT_S
    ) -> tuple[bool, str]:
        """Pergunta e espera. Devolve (aprovado, pin_digitado)."""
        with self._lock:
            self._done.clear()
            self._result = False
            self._pin_typed = ""
            self._requested.emit(question, require_pin)

            if not self._done.wait(timeout):
                # Sem resposta a tempo é negação, igual ao timeout por voz.
                logger.info("Confirmação por diálogo expirou em %.0fs.", timeout)
                return False, ""
            return self._result, self._pin_typed

    @Slot(str, bool)
    def _show(self, question: str, require_pin: bool) -> None:
        try:
            from PySide6.QtWidgets import (
                QDialog,
                QDialogButtonBox,
                QLabel,
                QLineEdit,
                QVBoxLayout,
            )

            dialog = QDialog(None)
            dialog.setWindowTitle(f"{self.app_name} — confirmação")
            dialog.setModal(True)
            # Este é o único momento em que o James pede atenção direta: uma
            # ação irreversível não pode ser confirmada sem o usuário ver.
            dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)

            layout = QVBoxLayout(dialog)
            label = QLabel(question, dialog)
            label.setWordWrap(True)
            label.setMinimumWidth(360)
            layout.addWidget(label)

            pin_field = None
            if require_pin:
                pin_field = QLineEdit(dialog)
                pin_field.setEchoMode(QLineEdit.Password)
                pin_field.setPlaceholderText("PIN")
                pin_field.setMaxLength(32)
                layout.addWidget(pin_field)

            buttons = QDialogButtonBox(dialog)
            confirmar = buttons.addButton("Confirmar", QDialogButtonBox.AcceptRole)
            cancelar = buttons.addButton("Cancelar", QDialogButtonBox.RejectRole)
            # O foco começa em "Cancelar": um Enter distraído não pode aprovar
            # uma ação irreversível.
            confirmar.setAutoDefault(False)
            confirmar.setDefault(False)
            cancelar.setAutoDefault(True)
            cancelar.setDefault(True)
            cancelar.setFocus()
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)

            accepted = dialog.exec() == QDialog.Accepted
            self._result = bool(accepted)
            self._pin_typed = pin_field.text() if (pin_field and accepted) else ""
        except Exception as exc:  # noqa: BLE001 — falhar aqui significa negar
            logger.error("Não consegui abrir o diálogo de confirmação: %s", exc)
            self._result = False
            self._pin_typed = ""
        finally:
            self._done.set()
