"""Identidade de aba. Quem é "a página" quando o James vai clicar.

## O defeito que este arquivo existe para corrigir

Antes, toda ação mutável ia para `contexto.pages[-1]` — a última aba da lista.
"Última" não quer dizer "a que você está vendo", nem "aquela de que falamos": é
só a ordem interna do Playwright. Basta um site abrir um pop-up, ou você abrir
uma aba enquanto o James trabalha, e o clique acontece em outro lugar.

E o modo como isso falha é o pior possível: **funciona quase sempre.** Com uma
aba só, `pages[-1]` está certo. O erro só aparece quando há várias — que é
exatamente quando alguém está trabalhando de verdade, e exatamente quando um
clique no lugar errado custa caro. Um formulário de compra é um formulário de
compra em qualquer aba.

## O contrato

Toda aba tem um `tab_id` estável, curto e dizível ("aba 3"). Quem quer mudar
alguma coisa PRECISA dizer em qual — não existe alvo implícito para ação
mutável. Ler pode ter um padrão; agir, não.

Duas regras que parecem detalhe e não são:

  - **`tab_id` nunca é reaproveitado.** Se a aba 3 fecha e outra abre, ela é a
    4. Reaproveitar faria uma referência velha do modelo apontar para uma
    página nova, que é a mesma classe de erro que estamos consertando.

  - **Aba fechada não resolve.** Devolve erro nomeado, não a "mais próxima".
    Adivinhar aqui seria reintroduzir `pages[-1]` com outro nome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from james.logs import get_logger

logger = get_logger("james.browser.sessao")


class AbaDesconhecida(LookupError):
    """O `tab_id` não existe, ou a aba já foi fechada."""


class AlvoAusente(ValueError):
    """Uma ação mutável foi pedida sem dizer em qual aba."""


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dominio(url: str) -> str:
    """Só o host, para falar e para comparar. Nunca a URL inteira em voz alta."""
    try:
        return urlparse(url).netloc or ""
    except ValueError:
        return ""


@dataclass
class BrowserTab:
    """Uma aba, com identidade própria e independente da ordem do Playwright."""

    tab_id: str
    page: object = field(repr=False)
    criada_em: str = field(default_factory=_agora)
    ultima_interacao: str | None = None
    # Sobe a cada navegação de documento que a gente detecta. É o que permite
    # um snapshot dizer "a página em que fui tirado não existe mais".
    geracao: int = 0

    @property
    def fechada(self) -> bool:
        try:
            return bool(self.page.is_closed())
        except Exception:  # noqa: BLE001 — página morta levanta tipos variados
            return True

    def _seguro(self, chamada, padrao=""):
        """Título e URL vêm do processo do navegador e podem falhar sozinhos.

        Uma aba que morreu no meio de `listar_abas` não pode derrubar a
        listagem inteira — a informação das outras continua boa.
        """
        try:
            return chamada()
        except Exception:  # noqa: BLE001
            return padrao

    @property
    def url(self) -> str:
        return self._seguro(lambda: self.page.url)

    @property
    def titulo(self) -> str:
        return self._seguro(lambda: self.page.title())

    def tocar(self) -> None:
        self.ultima_interacao = _agora()

    def resumo(self) -> dict:
        """O que o modelo vê. URL inteira NÃO entra por padrão.

        Uma URL carrega token de sessão, id de pedido, termo de busca — e a
        listagem de abas vai inteira para o histórico. Domínio e título já
        respondem "que aba é essa"; quem precisar da URL pede a inspeção.
        """
        return {
            "tab_id": self.tab_id,
            "titulo": self.titulo[:120],
            "dominio": dominio(self.url),
            "geracao": self.geracao,
        }


class BrowserSession:
    """O registro de abas. Reconcilia com o navegador a cada consulta.

    Reconciliar sempre, em vez de confiar num evento de "aba aberta", é o que
    faz a aba que VOCÊ abriu à mão também ganhar id. Se o registro só soubesse
    das abas que o James abriu, `listar_abas` mostraria metade do navegador — e
    a metade invisível é justamente onde o seu trabalho está.
    """

    def __init__(self, session_id: str = "principal") -> None:
        self.session_id = session_id
        self._abas: dict[str, BrowserTab] = {}
        self._por_pagina: dict[int, str] = {}      # id(page) -> tab_id
        self._proximo = 1
        self._selecionada: str | None = None

    # ------------------------------------------------------------ registro

    def _novo_id(self) -> str:
        # Nunca reaproveitado: o contador só sobe, mesmo depois de fechar.
        tab_id = str(self._proximo)
        self._proximo += 1
        return tab_id

    def adotar(self, page) -> BrowserTab:
        """Dá identidade a uma página, ou devolve a que ela já tinha."""
        chave = id(page)
        existente = self._por_pagina.get(chave)
        if existente and existente in self._abas:
            return self._abas[existente]

        aba = BrowserTab(tab_id=self._novo_id(), page=page)
        self._abas[aba.tab_id] = aba
        self._por_pagina[chave] = aba.tab_id
        logger.debug("Aba %s adotada (%s).", aba.tab_id, dominio(aba.url))
        return aba

    def sincronizar(self, paginas) -> list[BrowserTab]:
        """Alinha o registro com o navegador: adota as novas, tira as mortas."""
        vivas = []
        for page in paginas:
            try:
                if page.is_closed():
                    continue
            except Exception:  # noqa: BLE001
                continue
            vivas.append(self.adotar(page))

        ids_vivos = {a.tab_id for a in vivas}
        for tab_id in [t for t in self._abas if t not in ids_vivos]:
            self._esquecer(tab_id)

        if self._selecionada not in ids_vivos:
            self._selecionada = None
        return vivas

    def _esquecer(self, tab_id: str) -> None:
        aba = self._abas.pop(tab_id, None)
        if aba is not None:
            self._por_pagina.pop(id(aba.page), None)

    # -------------------------------------------------------------- acesso

    @property
    def abas(self) -> list[BrowserTab]:
        return [a for a in self._abas.values() if not a.fechada]

    @property
    def selecionada(self) -> str | None:
        return self._selecionada

    def selecionar(self, tab_id: str) -> BrowserTab:
        aba = self.exigir(tab_id)
        self._selecionada = aba.tab_id
        return aba

    def exigir(self, tab_id: str | None) -> BrowserTab:
        """Resolve um `tab_id`. Sem adivinhação, sem "a mais próxima".

        Este método é o coração da correção: se ele algum dia devolver um
        palpite quando o id não bate, `pages[-1]` volta com outro nome.
        """
        if tab_id is None or str(tab_id).strip() == "":
            raise AlvoAusente(
                "Preciso saber em qual aba. Peça a lista de abas primeiro — "
                "cada uma tem um número."
            )

        tab_id = str(tab_id).strip()
        aba = self._abas.get(tab_id)
        if aba is None:
            conhecidas = ", ".join(sorted(self._abas, key=_ordem)) or "nenhuma"
            raise AbaDesconhecida(
                f"Não tenho aba {tab_id}. Abertas agora: {conhecidas}."
            )
        if aba.fechada:
            self._esquecer(tab_id)
            raise AbaDesconhecida(
                f"A aba {tab_id} foi fechada. Peça a lista de novo."
            )
        return aba

    def para_leitura(self, tab_id: str | None) -> BrowserTab:
        """Ler aceita um padrão; agir, não.

        A assimetria é o ponto: ler a aba errada gasta uma leitura, clicar na
        aba errada compra uma passagem. Quando há UMA aba só, não há ambiguidade
        para resolver e exigir o número seria burocracia; com várias, a escolha
        volta a ser obrigatória mesmo para leitura.
        """
        if tab_id not in (None, ""):
            return self.exigir(tab_id)
        if self._selecionada:
            return self.exigir(self._selecionada)

        vivas = self.abas
        if len(vivas) == 1:
            return vivas[0]
        if not vivas:
            raise AbaDesconhecida("Não há nenhuma aba aberta.")
        numeros = ", ".join(a.tab_id for a in sorted(vivas, key=lambda x: _ordem(x.tab_id)))
        raise AlvoAusente(
            f"Há {len(vivas)} abas abertas ({numeros}). Diga em qual."
        )

    def listar(self) -> list[dict]:
        saida = []
        for aba in sorted(self.abas, key=lambda a: _ordem(a.tab_id)):
            item = aba.resumo()
            item["selecionada"] = aba.tab_id == self._selecionada
            saida.append(item)
        return saida


def _ordem(tab_id: str) -> tuple[int, str]:
    """Ordena "2" antes de "10" — ordenação de texto poria 10 antes de 2."""
    try:
        return (int(tab_id), "")
    except (TypeError, ValueError):
        return (10**9, str(tab_id))
