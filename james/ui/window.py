"""Janela comum do James — o modo padrão da interface.

Por que uma janela normal em vez de um painel sobreposto: o HUD sem borda,
sempre no topo e translúcido exige composição de tela ativa e obriga o
compositor a redesenhar o que está por baixo a cada quadro. Numa máquina
modesta isso aparece como travamento do sistema inteiro, não só do James.

Uma janela comum não tem nada disso: você move, minimiza e fecha como qualquer
programa, ela não cobre nada, e o custo de desenho é o de um widget parado
quando nada está acontecendo.

Ela também nunca rouba o foco do teclado. Se o James for chamado enquanto você
digita, a janela se atualiza sem tirar o cursor de onde está.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from james.logs import get_logger
from james.ui.orb import OrbWidget
from james.ui.states import UiState, caption_for, palette_for
from james.ui.tray import build_icon

logger = get_logger("james.ui.window")

_MAX_TRANSCRIPT_BLOCKS = 200

_STYLE = """
QWidget#raiz { background: #070d14; }
QLabel#estado {
    color: #bef0ff; font-size: 15px; font-weight: 600; letter-spacing: 1px;
}
QLabel#cota { color: #5f7d8c; font-size: 11px; }
QTextEdit#transcricao {
    background: #050a10; color: #c8dbe6; border: 1px solid #123243;
    border-radius: 6px; padding: 8px; font-size: 12px;
}
QPushButton {
    background: #0d2231; color: #bef0ff; border: 1px solid #1d4a63;
    border-radius: 5px; padding: 7px 14px; font-size: 12px;
}
QPushButton:hover { background: #133247; }
QPushButton:pressed { background: #0a1a26; }
"""


class MainWindow(QWidget):
    """Janela de estado do James. Mesma interface de sinais do HUD."""

    # Emitidos de outras threads; o Qt entrega na thread da interface.
    request_state = Signal(str, bool)      # estado, degradado
    request_caption = Signal(str)
    request_hide = Signal()
    request_transcript = Signal(str, str)  # quem ("voce"/"james"/"sistema"), texto
    request_quota = Signal(dict)

    # Emitidos pela janela para o orquestrador.
    cancel_requested = Signal()
    toggle_listening = Signal()

    def __init__(
        self,
        app_name: str = "James",
        width: int = 460,
        height: int = 520,
        fps: int = 30,
        show_transcript: bool = True,
        start_minimized: bool = True,
        raise_on_wake: bool = True,
    ) -> None:
        super().__init__(None)
        self.app_name = app_name
        self.raise_on_wake = raise_on_wake
        self._state = UiState.HIDDEN
        self._degraded = False
        self._listening = True

        self.setObjectName("raiz")
        self.setWindowTitle(app_name)
        self.setWindowIcon(build_icon())
        self.setStyleSheet(_STYLE)
        self.resize(width, height)
        self.setMinimumSize(360, 300)
        # Aparece sem tirar o foco do teclado de onde o usuário estiver.
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.orb = OrbWidget(self, fps=fps, height=150)
        layout.addWidget(self.orb)

        self.status_label = QLabel("Em repouso", self)
        self.status_label.setObjectName("estado")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.caption_label = QLabel("", self)
        self.caption_label.setAlignment(Qt.AlignCenter)
        self.caption_label.setWordWrap(True)
        self.caption_label.setStyleSheet("color: #7fb6cc; font-size: 12px;")
        self.caption_label.setMinimumHeight(34)
        layout.addWidget(self.caption_label)

        self.transcript = QTextEdit(self)
        self.transcript.setObjectName("transcricao")
        self.transcript.setReadOnly(True)
        self.transcript.setVisible(show_transcript)
        # Sem isto, um dia inteiro de conversa vira um documento gigante que
        # come memória e trava a rolagem.
        self.transcript.document().setMaximumBlockCount(_MAX_TRANSCRIPT_BLOCKS)
        layout.addWidget(self.transcript, stretch=1)

        self.quota_label = QLabel("", self)
        self.quota_label.setObjectName("cota")
        self.quota_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.quota_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.pause_button = QPushButton("Pausar escuta", self)
        self.pause_button.clicked.connect(self.toggle_listening.emit)
        buttons.addWidget(self.pause_button)

        cancel_button = QPushButton("Cancelar ação", self)
        cancel_button.clicked.connect(self.cancel_requested.emit)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        self.request_state.connect(self._on_state)
        self.request_caption.connect(self._on_caption)
        self.request_hide.connect(self._on_idle)
        self.request_transcript.connect(self._on_transcript)
        self.request_quota.connect(self._on_quota)

        if start_minimized:
            self.showMinimized()
        else:
            self.show()

    # -------------------------------------------------------------- slots

    def _on_state(self, state_value: str, degraded: bool) -> None:
        try:
            state = UiState(state_value)
        except ValueError:
            logger.warning("Estado de interface desconhecido: %r", state_value)
            return

        self._state = state
        self._degraded = bool(degraded)
        self.orb.set_state(state, self._degraded)

        caption = caption_for(state) or "Em repouso"
        if self._degraded:
            caption += " · capacidade limitada"
        self.status_label.setText(caption)

        palette = palette_for(state, self._degraded)
        self.status_label.setStyleSheet(
            f"color: rgb({palette.text[0]},{palette.text[1]},{palette.text[2]}); "
            "font-size: 15px; font-weight: 600; letter-spacing: 1px;"
        )

        if self.raise_on_wake and self.isMinimized():
            # showNormal sozinho restaura sem ativar, porque a janela carrega
            # WA_ShowWithoutActivating: o cursor do usuário fica onde estava.
            self.showNormal()

    def _on_caption(self, text: str) -> None:
        self.caption_label.setText(" ".join(str(text or "").split()))

    def _on_idle(self) -> None:
        self._state = UiState.HIDDEN
        self.orb.set_state(UiState.HIDDEN, self._degraded)
        self.status_label.setText("Em repouso")
        self.caption_label.setText("")

    def _on_transcript(self, who: str, text: str) -> None:
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return
        colors = {"voce": "#8fd4ff", "james": "#a8f0c8", "sistema": "#d8a860"}
        labels = {"voce": "Você", "james": self.app_name, "sistema": "Sistema"}
        color = colors.get(who, "#c8dbe6")
        label = labels.get(who, who)

        self.transcript.append(
            f'<span style="color:{color}"><b>{label}:</b> '
            f'<span style="color:#c8dbe6">{_escape(cleaned)}</span></span>'
        )
        self.transcript.moveCursor(QTextCursor.End)

    def _on_quota(self, remaining: dict) -> None:
        if not remaining:
            self.quota_label.setText("")
            return
        partes = [f"{nome}: {valor}" for nome, valor in sorted(remaining.items())]
        self.quota_label.setText("Requisições restantes hoje — " + " · ".join(partes))

    # ------------------------------------------------------------ auxiliar

    def set_listening(self, listening: bool) -> None:
        self._listening = bool(listening)
        self.pause_button.setText("Pausar escuta" if listening else "Retomar escuta")

    def closeEvent(self, event) -> None:  # noqa: N802 — assinatura do Qt
        """Fechar a janela esconde o James, não o encerra.

        Ele continua escutando pela bandeja; sair de verdade é pelo menu da
        bandeja. Encerrar o assistente por engano ao fechar uma janela seria
        surpreendente.
        """
        event.ignore()
        self.hide()


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
