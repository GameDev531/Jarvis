"""Inspetor de QA — o que está errado nesta página, sem perguntar a um modelo.

Mandar uma captura de tela para um modelo de visão descrever "o design" gasta
requisição, demora, e devolve impressão. Acessibilidade e estrutura de página
não são questão de opinião: ou o botão tem nome acessível, ou não tem.

O que dá para afirmar lendo o DOM, este arquivo afirma. O que é gosto — se o
azul combina, se o espaçamento respira — continua sendo trabalho do modelo de
visão, com a captura que `ver_pagina` tira.

Cada achado sai com `seletor` justamente para o passo seguinte ser possível:
descobrir o problema e já poder apontar para o elemento.
"""

from __future__ import annotations

# Roda DENTRO da página. Sem dependência, sem framework — é o DOM cru, que é o
# único contrato que todo site cumpre.
_SCRIPT = r"""
() => {
  const texto = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();
  const seletor = (el) => {
    if (!el) return '';
    if (el.id) return '#' + CSS.escape(el.id);
    const partes = [];
    let no = el;
    while (no && no.nodeType === 1 && partes.length < 4) {
      let p = no.tagName.toLowerCase();
      if (no.classList.length) p += '.' + CSS.escape(no.classList[0]);
      const irmaos = no.parentElement
        ? [...no.parentElement.children].filter((x) => x.tagName === no.tagName)
        : [];
      if (irmaos.length > 1) p += `:nth-of-type(${irmaos.indexOf(no) + 1})`;
      partes.unshift(p);
      no = no.parentElement;
    }
    return partes.join(' > ');
  };

  const achados = [];
  const anota = (tipo, gravidade, msg, el) =>
    achados.push({ tipo, gravidade, mensagem: msg, seletor: seletor(el) });

  /* --- hierarquia de títulos: um h1, sem pular níveis --- */
  const titulos = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')];
  const h1s = titulos.filter((h) => h.tagName === 'H1');
  if (h1s.length === 0) anota('estrutura', 'alto', 'A página não tem <h1>.', document.body);
  if (h1s.length > 1)
    anota('estrutura', 'medio', `${h1s.length} elementos <h1> — deveria haver um.`, h1s[1]);
  let anterior = 0;
  for (const h of titulos) {
    const n = +h.tagName[1];
    if (anterior && n > anterior + 1)
      anota('estrutura', 'medio',
        `Título pula de h${anterior} para h${n}: "${texto(h).slice(0, 40)}"`, h);
    anterior = n;
  }

  /* --- imagens sem alternativa textual --- */
  for (const img of document.querySelectorAll('img')) {
    if (!img.hasAttribute('alt'))
      anota('acessibilidade', 'alto', 'Imagem sem atributo alt.', img);
  }

  /* --- controles sem nome acessível: o leitor de tela anuncia "botão" --- */
  for (const el of document.querySelectorAll('button,a,input,select,textarea')) {
    const nome = (el.getAttribute('aria-label') || el.getAttribute('title') ||
                  texto(el) || el.getAttribute('placeholder') ||
                  el.getAttribute('value') || '').trim();
    if (!nome && el.type !== 'hidden')
      anota('acessibilidade', 'alto',
        `<${el.tagName.toLowerCase()}> sem nome acessível.`, el);
  }

  /* --- "clique aqui": inútil fora do contexto visual --- */
  const vagos = ['clique aqui', 'aqui', 'leia mais', 'saiba mais', 'click here', 'more'];
  for (const a of document.querySelectorAll('a')) {
    if (vagos.includes(texto(a).toLowerCase()))
      anota('acessibilidade', 'baixo', `Link com texto vago: "${texto(a)}"`, a);
  }

  /* --- campos de formulário sem rótulo --- */
  for (const campo of document.querySelectorAll('input,select,textarea')) {
    if (['hidden', 'submit', 'button', 'image'].includes(campo.type)) continue;
    const temLabel = (campo.id && document.querySelector(`label[for="${CSS.escape(campo.id)}"]`))
      || campo.closest('label') || campo.getAttribute('aria-label');
    if (!temLabel)
      anota('formulario', 'alto',
        `Campo "${campo.name || campo.type}" sem rótulo.`, campo);
  }

  /* --- alvos de toque pequenos: 24px é o mínimo do WCAG 2.2 --- */
  for (const el of document.querySelectorAll('button,a,input[type=checkbox],input[type=radio]')) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && (r.width < 24 || r.height < 24))
      anota('design', 'baixo',
        `Alvo de ${Math.round(r.width)}x${Math.round(r.height)}px (mínimo 24).`, el);
  }

  /* --- rolagem horizontal: quase sempre um elemento estourando --- */
  const estoura = document.documentElement.scrollWidth > window.innerWidth + 1;

  /* --- inventário dos formulários, para poder preencher depois --- */
  const formularios = [...document.querySelectorAll('form')].map((f) => ({
    seletor: seletor(f),
    acao: f.getAttribute('action') || '',
    campos: [...f.querySelectorAll('input,select,textarea')]
      .filter((c) => c.type !== 'hidden')
      .map((c) => ({
        nome: c.name || c.id || '',
        tipo: (c.type || c.tagName).toLowerCase(),
        obrigatorio: c.required || false,
        seletor: seletor(c),
      })),
  }));

  return {
    url: location.href,
    titulo: document.title,
    idioma: document.documentElement.lang || '',
    achados,
    formularios,
    rolagem_horizontal: estoura,
    contagem: {
      imagens: document.images.length,
      links: document.links.length,
      formularios: formularios.length,
    },
  };
}
"""

# Um relatório sem teto vira um despejo que ninguém lê — e, pior, um despejo
# que vai inteiro para o histórico do modelo e come o contexto.
MAX_ACHADOS = 40

_ORDEM = {"alto": 0, "medio": 1, "baixo": 2}


def inspecionar(pagina) -> dict:
    """Roda o inspetor na página e devolve o relatório, já priorizado."""
    dados = pagina.evaluate(_SCRIPT)

    achados = dados.get("achados") or []
    # Grave primeiro: quem lê em voz alta ouve o que importa antes de cansar.
    achados.sort(key=lambda a: _ORDEM.get(a.get("gravidade"), 9))
    dados["total_achados"] = len(achados)
    dados["achados"] = achados[:MAX_ACHADOS]

    if not dados.get("idioma"):
        dados["achados"].insert(0, {
            "tipo": "acessibilidade",
            "gravidade": "medio",
            # Sem `lang`, o leitor de tela lê português com fonética inglesa.
            "mensagem": "<html> sem atributo lang.",
            "seletor": "html",
        })

    if dados.get("rolagem_horizontal"):
        dados["achados"].insert(0, {
            "tipo": "design",
            "gravidade": "medio",
            "mensagem": "A página rola na horizontal — algum elemento estoura a largura.",
            "seletor": "body",
        })

    dados["resumo"] = _resumir(dados)
    return dados


def _resumir(dados: dict) -> str:
    """Uma frase dizível. O relatório inteiro não cabe numa resposta falada."""
    total = dados.get("total_achados", 0)
    if not total:
        return f"'{dados.get('titulo', '')}' passou sem apontamentos."

    graves = sum(1 for a in dados["achados"] if a.get("gravidade") == "alto")
    partes = [f"{total} apontamento{'s' if total != 1 else ''}"]
    if graves:
        partes.append(f"{graves} grave{'s' if graves != 1 else ''}")
    return f"{dados.get('titulo', 'A página')}: " + ", ".join(partes) + "."
