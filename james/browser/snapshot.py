"""Snapshot: o modelo aponta para um elemento que ele realmente viu.

## O problema

Entre "o James inspeciona a página" e "o James clica" passam segundos, e nesses
segundos a página pode ter feito qualquer coisa: navegar, trocar de rota, um
banner de cookies pode ter aparecido e empurrado tudo para baixo, uma lista
pode ter recarregado. O seletor `button.primary:nth-of-type(2)` continuava
casando — com outro botão.

Pior, o modelo podia INVENTAR um seletor. Nada o impedia de escrever
`button.comprar` porque parecia razoável, sem nunca ter visto esse elemento.

## O contrato

Inspecionar devolve um `snapshot_id` e uma lista de `ElementRef` com ids curtos
(`e1`, `e2`). Agir exige os dois. Antes de executar, seis conferências, e
qualquer uma que falhe cancela a ação:

  1. a aba é a mesma;
  2. o snapshot é daquela aba;
  3. a ORIGEM da página não mudou (mudou = outro site, outras regras);
  4. o documento é o mesmo (a marca morre com a árvore de elementos);
  5. o seletor ainda casa com EXATAMENTE UM elemento;
  6. o elemento ainda é o mesmo: mesma tag, mesmo papel, mesmo nome acessível.

Falhou? `PAGINA_MUDOU_INSPECIONE_DE_NOVO`. Não é erro: é a resposta certa.
Clicar "provavelmente no lugar certo" é pior que não clicar.

## Onde a marca mora, e por que não é no `window`

A primeira versão marcava `window.__james_snapshot`. Um teste contra Chromium
real derrubou isso: `document.write` (que é o que `set_content` faz, e o que
alguns sites ainda fazem) **substitui o documento inteiro e o `window`
sobrevive** — a marca continuava lá, apontando para um DOM que não existia
mais. Medido: `window` e `document` sobrevivem; só uma marca no elemento
`<html>` morre, porque a árvore de elementos é reconstruída.

Então a marca é uma propriedade em `document.documentElement`. Propriedade, e
não atributo `data-`: atributo apareceria no DOM, entraria em seletores
`[data-*]` da página e mudaria o que estamos inspecionando.

## O que a marca pega, e o que não pega

Pega navegação e substituição de documento, as duas com certeza. O que ela NÃO
pega é mudança dentro do MESMO documento: um app de página única que troca a
tela sem navegar mantém a árvore. Por isso a revalidação do elemento (5 e 6)
não é redundante — é ela que cobre esse caso, comparando o que o elemento é
agora com o que ele era quando foi visto.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from james.logs import get_logger

logger = get_logger("james.browser.snapshot")

# Um snapshot velho é um snapshot perigoso. Trinta segundos é generoso para um
# turno de voz e curto o bastante para não atravessar uma conversa inteira.
VALIDADE_S = 30.0

# Teto de elementos devolvidos. Sem ele, uma página de catálogo com 2.000 links
# entra inteira no histórico do modelo e come o contexto que a conversa precisa.
MAX_ELEMENTOS = 60

CODIGO_PAGINA_MUDOU = "PAGINA_MUDOU_INSPECIONE_DE_NOVO"


class SnapshotInvalido(RuntimeError):
    """O snapshot não vale mais. A ação não acontece."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.codigo = CODIGO_PAGINA_MUDOU
        self.motivo = motivo


class ElementoNaoEncontrado(RuntimeError):
    """O `element_id` não está neste snapshot."""


def origem(url: str) -> str:
    """esquema://host:porta — o que define "o mesmo site" para o navegador."""
    try:
        p = urlparse(url)
    except ValueError:
        return ""
    if not p.scheme:
        return ""
    return f"{p.scheme}://{p.netloc}"


@dataclass(frozen=True)
class ElementRef:
    """Um elemento como o modelo o vê: id curto, papel, nome, e se dá para escrever."""

    element_id: str
    seletor: str
    papel: str
    nome: str
    tipo: str = ""
    pode_escrever: bool = False
    visivel: bool = True

    def resumo(self) -> dict:
        """Sem o seletor: o modelo trabalha com `element_id`, não com CSS.

        Devolver o seletor convidaria o modelo a montar variações dele — que é
        exatamente o hábito que este arquivo existe para acabar.
        """
        d = {
            "element_id": self.element_id,
            "papel": self.papel,
            "nome": self.nome[:80],
        }
        if self.tipo:
            d["tipo"] = self.tipo
        if self.papel in ("input", "textarea", "select"):
            d["pode_escrever"] = self.pode_escrever
        return d


@dataclass
class Snapshot:
    snapshot_id: str
    tab_id: str
    origem: str
    marca: str
    criado_em: float
    elementos: dict[str, ElementRef] = field(default_factory=dict)
    url: str = ""
    titulo: str = ""

    def elemento(self, element_id: str) -> ElementRef:
        ref = self.elementos.get(str(element_id).strip())
        if ref is None:
            disponiveis = ", ".join(sorted(self.elementos)[:12])
            raise ElementoNaoEncontrado(
                f"Não vi nenhum elemento '{element_id}' nessa página. "
                f"Os que eu vi: {disponiveis}."
            )
        return ref

    def resumo(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "tab_id": self.tab_id,
            "titulo": self.titulo[:120],
            "elementos": [e.resumo() for e in self.elementos.values()],
        }


# Roda dentro da página. Marca o documento e cataloga o que dá para operar.
_CAPTURAR = r"""
(marca) => {
  /* No <html>, não no window: `document.write` troca o documento inteiro e o
     window sobrevive junto com o que estiver nele. A árvore de elementos, não. */
  document.documentElement.__james_snapshot = marca;

  const texto = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();
  const seletor = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const partes = [];
    let no = el;
    while (no && no.nodeType === 1 && partes.length < 5) {
      let p = no.tagName.toLowerCase();
      if (no.classList.length) p += '.' + CSS.escape(no.classList[0]);
      const irmaos = no.parentElement
        ? [...no.parentElement.children].filter((x) => x.tagName === no.tagName)
        : [];
      if (irmaos.length > 1) p += `:nth-of-type(${irmaos.indexOf(no) + 1})`;
      partes.unshift(p);
      if (no.id) { partes[0] = '#' + CSS.escape(no.id); break; }
      no = no.parentElement;
    }
    return partes.join(' > ');
  };

  const nomeDe = (el) =>
    (el.getAttribute('aria-label') || el.getAttribute('title') ||
     (el.labels && el.labels[0] ? el.labels[0].textContent : '') ||
     el.getAttribute('placeholder') || el.getAttribute('value') ||
     texto(el) || '').replace(/\s+/g, ' ').trim();

  const visivel = (el) => {
    if (!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return false;
    const e = getComputedStyle(el);
    return e.visibility !== 'hidden' && e.display !== 'none' && e.opacity !== '0';
  };

  const alvos = document.querySelectorAll(
    'a[href],button,input,select,textarea,[role=button],[role=link],[onclick]'
  );

  const saida = [];
  let n = 0;
  for (const el of alvos) {
    if (!visivel(el)) continue;
    const tag = el.tagName.toLowerCase();
    const tipo = (el.type || '').toLowerCase();
    if (tipo === 'hidden') continue;
    n += 1;
    saida.push({
      element_id: 'e' + n,
      seletor: seletor(el),
      papel: tag,
      nome: nomeDe(el).slice(0, 120),
      tipo: tipo,
      /* `disabled` e `readonly` decidem se DÁ para escrever. Se é PERMITIDO
         escrever quem decide é o Python, olhando tipo e nome — a página não
         tem autoridade nenhuma sobre isso. */
      editavel: ['input', 'textarea', 'select'].includes(tag)
                && !el.disabled && !el.readOnly,
    });
  }

  return {
    url: location.href,
    titulo: document.title,
    elementos: saida,
  };
}
"""

# Confere a marca. Árvore nova = `undefined`, e `undefined !== marca`.
_CONFERIR_MARCA = "(marca) => document.documentElement.__james_snapshot === marca"

# Revalida um elemento: quantos casam, e o que o primeiro é agora.
_REVALIDAR = r"""
({ sel }) => {
  const achados = document.querySelectorAll(sel);
  if (achados.length !== 1) return { quantos: achados.length };
  const el = achados[0];
  const texto = (x) => (x.textContent || '').replace(/\s+/g, ' ').trim();
  const nome = (el.getAttribute('aria-label') || el.getAttribute('title') ||
    (el.labels && el.labels[0] ? el.labels[0].textContent : '') ||
    el.getAttribute('placeholder') || el.getAttribute('value') ||
    texto(el) || '').replace(/\s+/g, ' ').trim();
  const e = getComputedStyle(el);
  return {
    quantos: 1,
    papel: el.tagName.toLowerCase(),
    nome: nome.slice(0, 120),
    tipo: (el.type || '').toLowerCase(),
    nome_campo: el.name || '',
    id_campo: el.id || '',
    autocomplete: el.getAttribute('autocomplete') || '',
    rotulo: (el.labels && el.labels[0] ? texto(el.labels[0]) : ''),
    placeholder: el.getAttribute('placeholder') || '',
    visivel: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
             && e.visibility !== 'hidden' && e.display !== 'none',
    ativo: !el.disabled,
  };
}
"""


def capturar(pagina, tab_id: str, agora: float) -> Snapshot:
    """Tira um snapshot da página e marca o documento."""
    marca = secrets.token_hex(8)
    dados = pagina.evaluate(_CAPTURAR, marca)

    elementos: dict[str, ElementRef] = {}
    for bruto in (dados.get("elementos") or [])[:MAX_ELEMENTOS]:
        ref = ElementRef(
            element_id=bruto["element_id"],
            seletor=bruto["seletor"],
            papel=bruto.get("papel", ""),
            nome=bruto.get("nome", ""),
            tipo=bruto.get("tipo", ""),
            pode_escrever=bool(bruto.get("editavel")),
        )
        elementos[ref.element_id] = ref

    url = dados.get("url", "")
    return Snapshot(
        snapshot_id=secrets.token_hex(6),
        tab_id=tab_id,
        origem=origem(url),
        marca=marca,
        criado_em=agora,
        elementos=elementos,
        url=url,
        titulo=dados.get("titulo", ""),
    )


def revalidar(pagina, snap: Snapshot, ref: ElementRef, agora: float) -> dict:
    """As seis conferências. Devolve o elemento ATUAL, ou levanta.

    A ordem importa: as checagens baratas e categóricas vêm antes das que
    tocam a página. Não adianta consultar o DOM de um documento que já sabemos
    que não é o mesmo.
    """
    if agora - snap.criado_em > VALIDADE_S:
        raise SnapshotInvalido(
            f"Essa leitura da página tem mais de {int(VALIDADE_S)} segundos."
        )

    atual = origem(_url_segura(pagina))
    if atual != snap.origem:
        # Mudança de origem é a mais grave: outro site, outras regras, e um
        # clique "no mesmo lugar" acontece num domínio que ninguém autorizou.
        raise SnapshotInvalido(
            f"A aba saiu de {snap.origem or 'lugar nenhum'} para {atual or 'lugar nenhum'}."
        )

    if not pagina.evaluate(_CONFERIR_MARCA, snap.marca):
        raise SnapshotInvalido("A página foi recarregada ou navegou.")

    estado = pagina.evaluate(_REVALIDAR, {"sel": ref.seletor})
    quantos = int(estado.get("quantos", 0))
    if quantos == 0:
        raise SnapshotInvalido(f"O elemento '{ref.element_id}' não está mais na página.")
    if quantos > 1:
        # Ambiguidade nunca vira escolha: com dois candidatos, "o primeiro" é
        # uma moeda jogada para o alto sobre uma ação irreversível.
        raise SnapshotInvalido(
            f"'{ref.element_id}' agora casa com {quantos} elementos — ficou ambíguo."
        )

    if estado.get("papel") != ref.papel:
        raise SnapshotInvalido(
            f"Ali era um <{ref.papel}> e agora é um <{estado.get('papel')}>."
        )

    # O nome pode variar em espaço e caixa sem ser outro elemento; mudar de
    # "Cancelar" para "Confirmar" é outro elemento no mesmo lugar.
    if _diferente(estado.get("nome", ""), ref.nome):
        raise SnapshotInvalido(
            f"O elemento mudou de '{ref.nome[:40]}' para '{estado.get('nome', '')[:40]}'."
        )

    if not estado.get("visivel"):
        raise SnapshotInvalido(f"'{ref.element_id}' não está visível agora.")
    if not estado.get("ativo"):
        raise SnapshotInvalido(f"'{ref.element_id}' está desabilitado.")

    return estado


def _diferente(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) != " ".join(b.lower().split())


def _url_segura(pagina) -> str:
    try:
        return pagina.url
    except Exception:  # noqa: BLE001
        return ""


class Snapshots:
    """Guarda os snapshots por aba. Um por aba: o último vale.

    Guardar histórico convidaria o modelo a agir sobre uma leitura antiga
    porque ela "ainda estava na conversa" — e a leitura antiga é justamente o
    que este módulo existe para recusar.
    """

    def __init__(self) -> None:
        self._por_id: dict[str, Snapshot] = {}
        self._por_aba: dict[str, str] = {}

    def guardar(self, snap: Snapshot) -> None:
        anterior = self._por_aba.get(snap.tab_id)
        if anterior:
            self._por_id.pop(anterior, None)
        self._por_id[snap.snapshot_id] = snap
        self._por_aba[snap.tab_id] = snap.snapshot_id

    def exigir(self, snapshot_id: str | None, tab_id: str) -> Snapshot:
        if not snapshot_id:
            raise SnapshotInvalido(
                "Preciso do snapshot_id da inspeção. Inspecione a página primeiro."
            )
        snap = self._por_id.get(str(snapshot_id).strip())
        if snap is None:
            raise SnapshotInvalido(
                "Essa leitura da página não vale mais. Inspecione de novo."
            )
        if snap.tab_id != tab_id:
            # Cruzar snapshot de uma aba com ação em outra é exatamente o erro
            # de alvo que a Fase B existe para acabar, só que disfarçado.
            raise SnapshotInvalido(
                f"Essa leitura é da aba {snap.tab_id}, não da aba {tab_id}."
            )
        return snap

    def esquecer_aba(self, tab_id: str) -> None:
        anterior = self._por_aba.pop(tab_id, None)
        if anterior:
            self._por_id.pop(anterior, None)


def agora() -> float:
    return datetime.now(timezone.utc).timestamp()
