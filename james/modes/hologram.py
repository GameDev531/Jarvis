"""Modo holograma — a interface volumétrica, ligada sob comando.

"Jarvis, ativa o holograma" sobe um servidor em 127.0.0.1 e abre o navegador
nele. "Jarvis, desativa o holograma" derruba o servidor; a aba que ficou aberta
perde a conexão e diz isso na tela, em vez de continuar mostrando telemetria
velha.

Por que isto é um modo, e não a interface padrão: o núcleo é Three.js com bloom
aditivo, uma carga de GPU contínua. Ela é linda e custa caro. A janela Qt comum
continua sendo o padrão porque funciona em qualquer máquina; o holograma é o
que você liga quando quer a experiência completa e tem folga para ela.

O recurso declarado é `interface_web` — não `camera`. Holograma e gestos podem
conviver: um usa GPU, o outro usa câmera. É exatamente o caso que a regra "um
recurso, um dono" precisa deixar passar.
"""

from __future__ import annotations

import threading
import webbrowser

from james.logs import get_logger
from james.modes.base import Mode, ModeError

logger = get_logger("james.modes.holograma")


class HologramMode(Mode):
    nome = "holograma"
    descricao = "Interface holográfica volumétrica no navegador."
    recursos = ("interface_web",)
    # Não expõe câmera nem microfone: o servidor é local e só mostra estado.
    sensivel = False

    def __init__(
        self,
        *,
        server_factory,
        abrir_navegador: bool = True,
        _browser=None,
    ) -> None:
        super().__init__()
        self._server_factory = server_factory
        self._abrir_navegador = abrir_navegador
        self._browser = _browser or webbrowser
        self._server = None
        self.url = ""

    def _ligar(self) -> str:
        try:
            self._server = self._server_factory()
            self.url = self._server.start()
        except FileNotFoundError as exc:
            self._server = None
            raise ModeError(str(exc)) from exc
        except OSError as exc:
            self._server = None
            raise ModeError(f"Não consegui abrir a porta da interface: {exc}") from exc

        if self._abrir_navegador:
            # Em thread separada: `webbrowser.open` bloqueia em algumas
            # configurações do Windows, e ligar o modo não pode travar o turno.
            threading.Thread(
                target=self._abrir, name="james-holo-browser", daemon=True
            ).start()

        return "Holograma ativo. Abri a interface no navegador."

    def _abrir(self) -> None:
        try:
            self._browser.open(self.url)
        except Exception as exc:  # noqa: BLE001 — sem navegador o modo continua de pé
            logger.error("Não consegui abrir o navegador: %s", exc)

    def _desligar(self) -> str:
        servidor, self._server = self._server, None
        if servidor is not None:
            servidor.stop()
        self.url = ""
        return "Holograma desligado."
