"""Busca na web que devolve CONTEÚDO, não só abre o navegador.

A ferramenta `pesquisar_web` que já existia abre uma aba para o usuário ler.
Esta é diferente: traz os resultados para dentro, que é o que a pesquisa
aprofundada precisa para encadear buscas e sintetizar.

Fonte: o endpoint HTML do DuckDuckGo. Gratuito, sem cadastro e sem chave — os
buscadores com API oficial cobram ou exigem cadastro, o que quebraria a regra
do projeto.

Ressalva honesta: é raspagem de HTML. Vai quebrar quando o DuckDuckGo mudar o
layout. Quando quebrar, a falha é explícita — o James diz que não conseguiu
buscar, em vez de responder com resultado inventado. O parser está separado da
rede justamente para poder ser testado e consertado sem depender de conexão.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlsplit

from james.logs import audit, get_logger
from james.web.safe_http import NomeNaoResolvido, RedirecionamentoBloqueado, obter

logger = get_logger("james.web.search")

_ENDPOINT = "https://html.duckduckgo.com/html/"
_TIMEOUT_S = 15
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}
MAX_RESULTADOS = 10


class SearchError(RuntimeError):
    """A busca não pôde ser realizada."""


@dataclass
class SearchResult:
    titulo: str
    url: str
    resumo: str = ""

    def to_dict(self) -> dict:
        return {"titulo": self.titulo, "url": self.url, "resumo": self.resumo}


def _desembrulhar(href: str) -> str:
    """O DuckDuckGo embrulha os links num redirecionador próprio.

    `//duckduckgo.com/l/?uddg=https%3A%2F%2Fsite.com` precisa virar
    `https://site.com`, senão todo resultado apontaria para o buscador.
    """
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href

    partes = urlsplit(href)
    if "duckduckgo.com" in (partes.netloc or "") and partes.path.startswith("/l/"):
        alvo = parse_qs(partes.query).get("uddg", [])
        if alvo:
            return unquote(alvo[0])
    return href


class _ResultParser(HTMLParser):
    """Lê a lista de resultados do HTML do buscador."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.resultados: list[SearchResult] = []
        self._em_titulo = False
        self._em_resumo = False
        self._titulo = ""
        self._resumo = ""
        self._url = ""

    def handle_starttag(self, tag, attrs) -> None:
        atributos = dict(attrs)
        classes = (atributos.get("class") or "").split()

        if tag == "a" and "result__a" in classes:
            self._fechar_pendente()
            self._em_titulo = True
            self._titulo = ""
            self._url = _desembrulhar(atributos.get("href") or "")
        elif "result__snippet" in classes:
            self._em_resumo = True
            self._resumo = ""

    def handle_endtag(self, tag) -> None:
        if tag == "a" and self._em_titulo:
            self._em_titulo = False
        elif self._em_resumo and tag in ("a", "div", "td"):
            self._em_resumo = False

    def handle_data(self, data) -> None:
        if self._em_titulo:
            self._titulo += data
        elif self._em_resumo:
            self._resumo += data

    def _fechar_pendente(self) -> None:
        if self._url and self._titulo.strip():
            self.resultados.append(
                SearchResult(
                    titulo=" ".join(self._titulo.split())[:200],
                    url=self._url,
                    resumo=" ".join(self._resumo.split())[:400],
                )
            )
        self._titulo = self._resumo = self._url = ""

    def finalizar(self) -> list[SearchResult]:
        self._fechar_pendente()
        # O mesmo domínio costuma aparecer várias vezes; para pesquisa
        # aprofundada, variedade de fonte vale mais que repetição.
        vistos: set[str] = set()
        unicos: list[SearchResult] = []
        for resultado in self.resultados:
            chave = resultado.url.rstrip("/")
            if chave and chave not in vistos:
                vistos.add(chave)
                unicos.append(resultado)
        return unicos

    def error(self, message) -> None:  # pragma: no cover
        """HTML quebrado não pode derrubar a busca."""


def parse_results(html: str, limite: int = 5) -> list[SearchResult]:
    """Extrai os resultados. Separado da rede para poder ser testado."""
    parser = _ResultParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001
        logger.debug("HTML de busca malformado; usando o que deu para ler.")
    return parser.finalizar()[: max(1, int(limite))]


def search_web(consulta: str, limite: int = 5) -> list[SearchResult]:
    """Busca e devolve os resultados. Levanta SearchError se não der."""
    texto = " ".join(str(consulta or "").split())
    if not texto:
        raise SearchError("Preciso saber o que buscar.")

    try:
        import httpx
    except ImportError as exc:
        raise SearchError("Pacote 'httpx' não instalado.") from exc

    try:
        # `follow_redirects=False`: quem segue os saltos é `obter`, validando
        # cada destino. Ver james/web/safe_http.py.
        with httpx.Client(timeout=_TIMEOUT_S, headers=_HEADERS, follow_redirects=False) as client:
            # POST é o que o formulário do endpoint HTML usa; com GET o
            # buscador às vezes devolve a página de consentimento em vez dos
            # resultados.
            resposta = obter(client, _ENDPOINT, metodo="POST", data={"q": texto[:400]})
            resposta.raise_for_status()
    except RedirecionamentoBloqueado as exc:
        # Não é falha de rede: é uma tentativa de levar o James para dentro da
        # rede local. Dizer "não consegui buscar" esconderia isso.
        audit("redirecionamento_bloqueado", origem="busca", motivo=str(exc))
        raise SearchError(f"Busca interrompida por segurança: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — httpx tem vários tipos de erro
        raise SearchError(f"Não consegui fazer a busca: {exc}") from exc

    resultados = parse_results(resposta.text, limite=limite)
    if not resultados:
        logger.info("Busca por %r não devolveu resultados legíveis.", texto[:60])
    return resultados


def fetch_page(url: str, max_chars: int = 8000) -> tuple[str, str]:
    """Baixa uma página e devolve (título, texto). Levanta SearchError."""
    from james.web.extract import extract_text, extract_title

    try:
        import httpx
    except ImportError as exc:
        raise SearchError("Pacote 'httpx' não instalado.") from exc

    try:
        with httpx.Client(timeout=_TIMEOUT_S, headers=_HEADERS, follow_redirects=False) as client:
            resposta = obter(client, url)
            resposta.raise_for_status()
    except NomeNaoResolvido as exc:
        raise SearchError(f"Endereço não resolveu: {exc}") from exc
    except RedirecionamentoBloqueado as exc:
        audit("redirecionamento_bloqueado", origem="pagina", url=url, motivo=str(exc))
        raise SearchError(f"Página recusada por segurança: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SearchError(f"Não consegui abrir a página: {exc}") from exc

    tipo = resposta.headers.get("content-type", "")
    if "html" not in tipo.lower() and "text" not in tipo.lower():
        raise SearchError(f"Esse endereço não é uma página de texto ({tipo or 'tipo desconhecido'}).")

    html = resposta.text
    return extract_title(html), extract_text(html, max_chars=max_chars)


_PALAVRA = re.compile(r"[\wÀ-ÿ]{3,}", re.UNICODE)


def termos_relevantes(texto: str, limite: int = 8) -> list[str]:
    """Palavras mais frequentes de um texto, para encadear a próxima busca.

    Heurística simples e suficiente: numa pesquisa aprofundada, o objetivo é
    descobrir termos que valem investigar, não fazer análise linguística.
    """
    vazias = {
        "para", "como", "que", "com", "uma", "dos", "das", "por", "mais", "não",
        "são", "seu", "sua", "ele", "ela", "isso", "esse", "essa", "the", "and",
        "for", "with", "this", "that", "from", "was", "were", "have",
    }
    contagem: dict[str, int] = {}
    for palavra in _PALAVRA.findall((texto or "").lower()):
        if palavra in vazias:
            continue
        contagem[palavra] = contagem.get(palavra, 0) + 1
    ordenadas = sorted(contagem.items(), key=lambda item: (-item[1], item[0]))
    return [palavra for palavra, _ in ordenadas[:limite]]
