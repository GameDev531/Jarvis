"""Modo navegador — o Ultron com as mãos no Chrome.

Um modo, e não uma ferramenta solta, porque o navegador é um recurso contínuo
como a câmera: enquanto ligado, existe uma conexão aberta e um processo do lado
de lá. "Um recurso, um dono" vale aqui igual.

E é `sensivel = True`. Ligar pede confirmação, pelo mesmo motivo da webcam:
anexado ao SEU Chrome, o James enxerga as abas abertas — que é onde estão seu
e-mail, seu banco, seu trabalho. Isso é mais íntimo que a webcam, não menos.

O que ele NÃO faz nunca, nem com o modo ligado, nem com confirmação: digitar em
campo de senha, mandar arquivo do disco para um site, mexer em campo oculto.
Ver `james/browser/actions.py` — a recusa é determinística e não passa pelo
julgamento do modelo.
"""

from __future__ import annotations

from james.browser.driver import BrowserUnavailable, NavegadorDriver
from james.logs import get_logger
from james.modes.base import Mode, ModeError

logger = get_logger("james.modes.navegador")


class BrowserMode(Mode):
    nome = "navegador"
    descricao = "Controla o navegador: abre abas, preenche formulários, inspeciona páginas."
    recursos = ("navegador",)
    # Anexado ao Chrome real, ele vê as abas abertas. Mais íntimo que a webcam.
    sensivel = True

    def __init__(
        self,
        *,
        anexar: bool = True,
        porta: int = 9222,
        headless: bool = False,
        timeout_ms: int = 15000,
    ) -> None:
        super().__init__()
        self._opcoes = {
            "anexar": anexar,
            "porta": porta,
            "headless": headless,
            "timeout_ms": timeout_ms,
        }
        self.driver: NavegadorDriver | None = None

    def _ligar(self) -> str:
        driver = NavegadorDriver(**self._opcoes)
        try:
            detalhe = driver.iniciar()
        except BrowserUnavailable as exc:
            raise ModeError(str(exc)) from exc
        self.driver = driver
        self._detalhe = detalhe
        return detalhe

    def _desligar(self) -> str:
        driver, self.driver = self.driver, None
        if driver is not None:
            driver.parar()
        self._detalhe = ""
        # Anexado, o navegador continua aberto — as abas são do usuário, e
        # fechá-las junto seria destruir o trabalho dele por tabela.
        return "Soltei o navegador."

    def exigir_driver(self) -> NavegadorDriver:
        if self.driver is None or not self.driver.ativo:
            raise ModeError(
                "O modo navegador está desligado. Diga 'liga o navegador' primeiro."
            )
        return self.driver
