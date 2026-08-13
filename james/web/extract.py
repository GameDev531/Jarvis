"""HTML para texto legível, com a biblioteca padrão.

Poderia usar BeautifulSoup ou readability, mas o ganho não paga a dependência:
o que interessa aqui é o texto corrido de uma página, e o `html.parser` do
Python resolve isso em cem linhas.

O que sai fora do texto, e por quê:

- `script` e `style`: é código, e código dentro do contexto de um modelo é
  ruído no melhor caso e instrução disfarçada no pior;
- `nav`, `header`, `footer`, `aside`: menu e rodapé repetem em toda página do
  site e afogariam o conteúdo real;
- comentários HTML: lugar clássico para esconder texto que o humano não vê.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Blocos cujo conteúdo não é texto da página.
_IGNORAR = frozenset({"script", "style", "noscript", "svg", "canvas", "template"})
# Blocos que existem em toda página do site e não são o assunto dela.
_CROMO = frozenset({"nav", "header", "footer", "aside", "form"})
# Tags que separam parágrafos: sem isso o texto vira uma linha só.
_QUEBRA = frozenset(
    {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
     "section", "article", "blockquote", "pre", "td"}
)

_ESPACOS = re.compile(r"[ \t]+")
_LINHAS = re.compile(r"\n{3,}")


class _Extractor(HTMLParser):
    def __init__(self, ignorar_cromo: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self.pedacos: list[str] = []
        self.titulo = ""
        self._pilha_ignorada = 0
        self._em_titulo = False
        self._ignorar_cromo = ignorar_cromo

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.lower()
        if tag in _IGNORAR or (self._ignorar_cromo and tag in _CROMO):
            self._pilha_ignorada += 1
            return
        if tag == "title":
            self._em_titulo = True
        if tag in _QUEBRA:
            self.pedacos.append("\n")

    def handle_endtag(self, tag) -> None:
        tag = tag.lower()
        if tag in _IGNORAR or (self._ignorar_cromo and tag in _CROMO):
            # Nunca deixar o contador negativo: HTML da vida real tem tag
            # fechada sem ter sido aberta, e isso destravaria o filtro.
            self._pilha_ignorada = max(0, self._pilha_ignorada - 1)
            return
        if tag == "title":
            self._em_titulo = False
        if tag in _QUEBRA:
            self.pedacos.append("\n")

    def handle_data(self, data) -> None:
        if self._pilha_ignorada > 0:
            return
        if self._em_titulo:
            self.titulo += data
            return
        if data.strip():
            self.pedacos.append(data)

    def error(self, message) -> None:  # pragma: no cover - exigido em versões antigas
        """HTML quebrado é a regra, não a exceção: seguir em frente."""


def _parse(html: str, ignorar_cromo: bool = True) -> _Extractor:
    extractor = _Extractor(ignorar_cromo=ignorar_cromo)
    try:
        extractor.feed(html or "")
        extractor.close()
    except Exception:  # noqa: BLE001 — página malformada não pode derrubar nada
        pass
    return extractor


def extract_text(html: str, max_chars: int = 8000, ignorar_cromo: bool = True) -> str:
    """Texto corrido da página, com parágrafos preservados."""
    extractor = _parse(html, ignorar_cromo)
    bruto = "".join(extractor.pedacos)

    linhas = [_ESPACOS.sub(" ", linha).strip() for linha in bruto.split("\n")]
    # Linhas de uma ou duas palavras costumam ser resto de menu que escapou do
    # filtro; texto de verdade quase nunca é tão curto.
    uteis = [linha for linha in linhas if len(linha) > 2]

    texto = _LINHAS.sub("\n\n", "\n".join(uteis)).strip()
    if len(texto) > max_chars:
        texto = texto[:max_chars].rstrip() + "\n[...página truncada]"
    return texto


def extract_title(html: str) -> str:
    titulo = _parse(html, ignorar_cromo=False).titulo
    return " ".join(titulo.split())[:200]
