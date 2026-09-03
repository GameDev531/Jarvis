"""A conexão com o navegador, e o dono das abas.

## A ordem de tentativa, e por que ela inverteu

Antes: anexar ao seu Chrome pessoal por CDP, e só cair para um navegador
próprio se falhasse. O argumento era suas contas já logadas. Ver
`james/browser/perfil.py` para as três razões que inverteram isso; a curta é
que dar ao assistente o seu perfil dá a ele o seu banco logado, e isso não é
padrão que se assume por conveniência.

Hoje:

  1. **Perfil gerenciado** (`state/browser_profiles/jarvis-default`) —
     persistente, então você loga uma vez e ele lembra, mas separado do seu
     Chrome e apagável sem tocar nele.
  2. **Anexar ao seu Chrome** — só com `navegador.anexar: true` no config,
     ligado a dedo, sabendo o que significa.

## O que este arquivo NÃO decide

Se uma ação pode acontecer. O tipo do campo é conferido em `actions.py`, o
alvo em `sessao.py`, a validade da leitura em `snapshot.py`, o endereço em
`network_policy.py`, e o nível de risco no guard. Aqui só se abre a conexão e
se entregam abas com identidade.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from james.browser.network_policy import RedeBloqueada, exigir as exigir_rede, instalar
from james.browser.perfil import PERFIL_PADRAO, GerenteDePerfis
from james.browser.sessao import BrowserSession, BrowserTab
from james.browser.snapshot import Snapshots
from james.logs import get_logger

logger = get_logger("james.browser")

PORTA_PADRAO = 9222
_TIMEOUT_ANEXO_S = 5.0


class BrowserUnavailable(RuntimeError):
    """Não foi possível abrir ou anexar a um navegador."""


def _explicar_como_ligar(porta: int) -> str:
    """A instrução que transforma um erro em algo acionável."""
    return (
        f"Nenhum Chrome escutando na porta {porta}. Para anexar ao seu Chrome, "
        f"feche-o e reabra assim:\n"
        f"  chrome.exe --remote-debugging-port={porta}\n"
        "Ou deixe `navegador.anexar: false` (o padrão) para eu usar o meu "
        "próprio perfil, separado do seu."
    )


class NavegadorDriver:
    """Dono da conexão e do registro de abas. Um por vez — o modo garante."""

    def __init__(
        self,
        anexar: bool = False,
        porta: int = PORTA_PADRAO,
        headless: bool = False,
        timeout_ms: int = 15000,
        perfis_dir: Path | str | None = None,
        perfil: str = PERFIL_PADRAO,
    ) -> None:
        self.anexar = bool(anexar)
        self.porta = int(porta)
        self.headless = bool(headless)
        self.timeout_ms = int(timeout_ms)
        self.perfil = perfil
        self.perfis = GerenteDePerfis(perfis_dir or Path("state") / "browser_profiles")

        self.sessao = BrowserSession()
        self.snapshots = Snapshots()

        self._pw = None
        self._browser = None
        self._contexto = None
        self._proprio = False        # nós lançamos, então nós fechamos
        self._roteadas: set[int] = set()

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
            frase = self._tentar_anexar()
            if frase:
                return frase

        return self._abrir_perfil_gerenciado()

    def _tentar_anexar(self) -> str | None:
        try:
            self._browser = self._pw.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.porta}",
                timeout=_TIMEOUT_ANEXO_S * 1000,
            )
            self._proprio = False
            self._contexto = (
                self._browser.contexts[0]
                if self._browser.contexts
                else self._browser.new_context()
            )
            logger.info("Anexado ao Chrome na porta %d.", self.porta)
            return (
                f"Conectado ao seu Chrome (porta {self.porta}). "
                "Estou no seu perfil, com as suas contas."
            )
        except Exception as exc:  # noqa: BLE001 — a lib levanta tipos variados
            logger.info("Não deu para anexar (%s); usando o meu perfil.", exc)
            self._browser = None
            return None

    def _abrir_perfil_gerenciado(self) -> str:
        perfil = self.perfis.preparar(self.perfil)
        try:
            # `launch_persistent_context` devolve o CONTEXTO, não o browser: é
            # a API de perfil persistente do Playwright, e é o que faz um login
            # sobreviver entre execuções.
            self._contexto = self._pw.chromium.launch_persistent_context(
                str(perfil.caminho),
                headless=self.headless,
            )
            self._browser = self._contexto.browser
            self._proprio = True
        except Exception as exc:  # noqa: BLE001
            self.parar()
            raise BrowserUnavailable(
                f"Não consegui abrir um navegador: {exc}\n\n"
                + _explicar_como_ligar(self.porta)
            ) from exc

        logger.info("Navegador aberto no perfil '%s'.", perfil.nome)
        return (
            f"Navegador aberto no meu perfil ('{perfil.nome}'), separado do seu Chrome."
        )

    def parar(self) -> None:
        """Fecha o que é nosso e solta o que é do usuário.

        Anexado, o navegador é DELE: fechar mataria as abas de trabalho da
        pessoa junto. Só se desconecta.
        """
        try:
            if self._proprio and self._contexto is not None:
                self._contexto.close()
            elif self._browser is not None and self._proprio:
                self._browser.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Erro ao fechar o navegador: %s", exc)
        finally:
            self._browser = None
            self._contexto = None
            self._roteadas.clear()
            self.sessao = BrowserSession()
            self.snapshots = Snapshots()
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

    def _preparar(self, pagina) -> None:
        """Liga a política de rede nesta página, uma vez só.

        Vale para navegação, subrecurso e cada salto de redirecionamento — é o
        único ponto por onde os três passam. Instalar duas vezes empilharia
        dois manipuladores e cada requisição seria decidida em dobro.
        """
        chave = id(pagina)
        if chave in self._roteadas:
            return
        try:
            instalar(pagina)
            self._roteadas.add(chave)
        except Exception as exc:  # noqa: BLE001
            # Sem interceptação não há trava de rede. Recusar é a única saída
            # segura: seguir "só que sem a proteção" é o modo silencioso de
            # perder uma trava inteira.
            raise BrowserUnavailable(
                f"Não consegui instalar a proteção de rede na aba: {exc}"
            ) from exc

    def sincronizar(self) -> list[BrowserTab]:
        contexto = self._exigir()
        abas = self.sessao.sincronizar(list(contexto.pages))
        for aba in abas:
            self._preparar(aba.page)
        # Snapshot de aba que sumiu não pode sobreviver ao dono.
        vivos = {a.tab_id for a in abas}
        for tab_id in [t for t in list(self.snapshots._por_aba) if t not in vivos]:
            self.snapshots.esquecer_aba(tab_id)
        return abas

    def abrir(self, url: str) -> BrowserTab:
        """Abre uma aba nova. A URL passa pela política ANTES de navegar."""
        exigir_rede(url)
        contexto = self._exigir()

        pagina = contexto.new_page()
        pagina.set_default_timeout(self.timeout_ms)
        self._preparar(pagina)
        try:
            # `domcontentloaded` e não `networkidle`: página com conexão
            # persistente (chat, telemetria, SSE) nunca fica ociosa.
            pagina.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        except Exception:
            try:
                pagina.close()
            except Exception:  # noqa: BLE001
                pass
            raise

        aba = self.sessao.adotar(pagina)
        aba.tocar()
        self.sessao.selecionar(aba.tab_id)
        return aba

    def navegar(self, tab_id: str, url: str) -> BrowserTab:
        """Leva uma aba EXISTENTE para outro endereço."""
        exigir_rede(url)
        aba = self.sessao.exigir(tab_id)
        self._preparar(aba.page)
        aba.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        # A aba é outra página agora: a leitura anterior morreu com ela.
        aba.geracao += 1
        aba.tocar()
        self.snapshots.esquecer_aba(aba.tab_id)
        return aba

    def fechar_aba(self, tab_id: str) -> str:
        aba = self.sessao.exigir(tab_id)
        dominio = aba.resumo()["dominio"]
        try:
            aba.page.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Erro ao fechar a aba %s: %s", tab_id, exc)
        self.snapshots.esquecer_aba(tab_id)
        self.sincronizar()
        return f"Fechei a aba {tab_id} ({dominio})."

    def abas(self) -> list[dict]:
        self.sincronizar()
        return self.sessao.listar()

    def aba_para_ler(self, tab_id: str | None) -> BrowserTab:
        self.sincronizar()
        return self.sessao.para_leitura(tab_id)

    def aba_para_agir(self, tab_id: str | None) -> BrowserTab:
        """Agir SEMPRE exige o alvo — não existe padrão aqui."""
        self.sincronizar()
        return self.sessao.exigir(tab_id)


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


__all__ = [
    "BrowserUnavailable",
    "NavegadorDriver",
    "PORTA_PADRAO",
    "RedeBloqueada",
    "chrome_com_depuracao",
]
