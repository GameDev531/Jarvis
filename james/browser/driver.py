"""Conexão com o navegador. Anexa ao Chrome que já existe, não baixa outro.

## A decisão que define este arquivo

O Playwright, por padrão, baixa e usa um Chromium **próprio** — uns 150 MB, e
um perfil vazio: sem suas contas, sem seus cookies, sem suas extensões. Para
"abrir uma aba e preencher um formulário" isso é quase inútil: metade dos
sites pede login que você já tem no seu Chrome de verdade.

Então a ordem é:

  1. **Anexar** ao Chrome já aberto, via CDP. Zero download, seu perfil, suas
     sessões. Exige que o Chrome tenha subido com `--remote-debugging-port`.
  2. Só se isso falhar, **lançar** um navegador do Playwright.

Numa máquina de 2011 com internet lenta, a diferença entre anexar e baixar não
é conveniência — é o recurso existir ou não.

## O que este arquivo NÃO decide

Se uma ação pode acontecer. Digitar num campo de senha é recusado em
`actions.py`, de forma determinística, e o guard decide o nível de risco. Aqui
só se abre a conexão e se entregam páginas.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from james.logs import get_logger

logger = get_logger("james.browser")

PORTA_PADRAO = 9222
_TIMEOUT_ANEXO_S = 5.0


class BrowserUnavailable(RuntimeError):
    """Não foi possível abrir ou anexar a um navegador."""


def _explicar_como_ligar(porta: int) -> str:
    """A instrução que transforma um erro em algo acionável.

    Sem esta frase, "não consegui conectar" manda a pessoa pesquisar na
    internet o que é CDP. Com ela, é copiar e colar.
    """
    return (
        f"Nenhum Chrome escutando na porta {porta}. Feche o Chrome e reabra assim:\n"
        f'  chrome.exe --remote-debugging-port={porta}\n'
        "Ou deixe `navegador.anexar: false` no config.yaml para eu abrir um "
        "navegador próprio (sem as suas contas)."
    )


class NavegadorDriver:
    """Dono da conexão. Um por vez — o modo garante isso."""

    def __init__(
        self,
        anexar: bool = True,
        porta: int = PORTA_PADRAO,
        headless: bool = False,
        timeout_ms: int = 15000,
    ) -> None:
        self.anexar = bool(anexar)
        self.porta = int(porta)
        self.headless = bool(headless)
        self.timeout_ms = int(timeout_ms)
        self._pw = None
        self._browser = None
        self._contexto = None
        self._proprio = False        # nós lançamos, então nós fechamos

    # ------------------------------------------------------------ conexão

    def iniciar(self) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable(
                "Pacote 'playwright' não instalado. "
                'Instale com: pip install -e ".[navegador]" '
                "e depois: python -m playwright install chromium"
            ) from exc

        self._pw = sync_playwright().start()

        if self.anexar:
            try:
                self._browser = self._pw.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{self.porta}",
                    timeout=_TIMEOUT_ANEXO_S * 1000,
                )
                self._proprio = False
                # Anexado: o contexto já existe, com o perfil real da pessoa.
                self._contexto = (
                    self._browser.contexts[0]
                    if self._browser.contexts
                    else self._browser.new_context()
                )
                logger.info("Anexado ao Chrome na porta %d.", self.porta)
                return f"Conectado ao seu Chrome (porta {self.porta})."
            except Exception as exc:  # noqa: BLE001 — a lib levanta tipos variados
                logger.info("Não deu para anexar (%s); tentando navegador próprio.", exc)

        try:
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._contexto = self._browser.new_context()
            self._proprio = True
        except Exception as exc:  # noqa: BLE001
            self.parar()
            raise BrowserUnavailable(
                f"Não consegui abrir um navegador: {exc}\n\n"
                + _explicar_como_ligar(self.porta)
            ) from exc

        logger.info("Navegador próprio aberto (sem o seu perfil).")
        return "Navegador aberto — perfil limpo, sem as suas contas."

    def parar(self) -> None:
        """Fecha o que é nosso e solta o que é do usuário.

        Anexado, o navegador é DELE: fechar mataria as abas de trabalho da
        pessoa junto. Só se desconecta.
        """
        try:
            if self._browser is not None:
                if self._proprio:
                    self._browser.close()
                else:
                    self._browser = None      # anexado: só solta
        except Exception as exc:  # noqa: BLE001
            logger.debug("Erro ao fechar o navegador: %s", exc)
        finally:
            self._browser = None
            self._contexto = None
            try:
                if self._pw is not None:
                    self._pw.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Erro ao encerrar o Playwright: %s", exc)
            self._pw = None

    # -------------------------------------------------------------- páginas

    @property
    def ativo(self) -> bool:
        return self._contexto is not None

    def _exigir(self):
        if self._contexto is None:
            raise BrowserUnavailable("O modo navegador não está ligado.")
        return self._contexto

    def pagina_atual(self):
        """A aba em foco, ou uma nova se não houver nenhuma."""
        contexto = self._exigir()
        paginas = [p for p in contexto.pages if not p.is_closed()]
        if not paginas:
            return contexto.new_page()
        return paginas[-1]

    def abrir(self, url: str):
        pagina = self._exigir().new_page()
        pagina.set_default_timeout(self.timeout_ms)
        # `domcontentloaded` e não `networkidle`: página com conexão persistente
        # (chat, telemetria, SSE) nunca fica ociosa, e a espera estoura sozinha.
        pagina.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        return pagina

    def abas(self) -> list[dict]:
        contexto = self._exigir()
        return [
            {"titulo": p.title(), "url": p.url}
            for p in contexto.pages
            if not p.is_closed()
        ]


def chrome_com_depuracao(porta: int = PORTA_PADRAO) -> str | None:
    """Caminho do chrome.exe, para a mensagem de ajuda. `None` se não achar."""
    for nome in ("chrome", "chrome.exe", "google-chrome", "chromium"):
        caminho = shutil.which(nome)
        if caminho:
            return caminho
    for palpite in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if Path(palpite).exists():
            return palpite
    return None
