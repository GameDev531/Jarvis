/* Interface holográfica do James — porte para JS puro do desenho original.
 *
 * O que mudou em relação ao arquivo do estúdio (design/original.dc.html), e por quê:
 *
 *   - **Sem React e sem runtime proprietário.** O original carregava React,
 *     ReactDOM e Babel de CDN em tempo de execução. Um assistente que se
 *     orgulha de funcionar offline não pode depender do unpkg para desenhar a
 *     própria tela. Aqui a estrutura é montada uma vez e só os textos mudam.
 *   - **Sem CDN.** Three.js está em `vendor/`, sob licença MIT.
 *   - **Dados reais.** Onde o original simulava CPU, memória e estado com
 *     `Math.sin(t)`, esta versão escuta o James por SSE. Quando a conexão cai,
 *     a interface diz que caiu em vez de continuar mostrando número bonito e
 *     falso — um HUD que mente é pior que um HUD desligado.
 *
 * Uma coisa que NÃO mudou e não deve mudar: o botão ULTRON é **cosmético**.
 * Ele troca a paleta e o texto da moldura. Não troca uma única permissão: o
 * guard vive no processo Python e não sabe que esta tela existe. "SEM
 * RESTRIÇÕES" é cenografia. Se algum dia isso deixar de ser verdade, o erro
 * está no Python, não aqui.
 */

import * as THREE from 'three';
import { GLTFLoader } from './vendor/GLTFLoader.js';

const MODS = [
  ['core', 'NUCLEO', '◉'], ['vision', 'VISAO', '◎'], ['files', 'ARQUIV', '▤'], ['browser', 'WEB', '⬡'],
  ['maps', 'MAPAS', '◈'], ['weather', 'ATMOSF', '✧'], ['media', 'MIDIA', '⏣'], ['systems', 'SISTEM', '⌬'],
  ['network', 'TRAFEG', '⧉'], ['analytics', 'ANALIS', '⌗'], ['security', 'SEGUR', '⎔'], ['lab', 'LAB 3D', '⬢'],
  ['memory', 'MEMOR', '☰'], ['comms', 'COMUNI', '⌘'],
];

/* g = geometria projetada na janela holográfica (null = janela só de dados) */
const CTX = {
  core: { t: 'NÚCLEO DE INTELIGÊNCIA', c: 'CR-01', g: null, s: 'Supervisão do reator cognitivo e alocação de ciclos.', rows: [['CARGA COGNITIVA', 62], ['SINCRONIA', 97], ['ENTROPIA', 18]], lines: ['O núcleo central é o próprio J.A.R.V.I.S.'], tags: ['ESTÁVEL', 'NÍVEL 01'] },
  vision: { t: 'VISÃO COMPUTACIONAL', c: 'VS-04', g: 'drone', s: 'Análise de tela e câmera sob confirmação.', rows: [['CONFIANÇA', 94]], lines: ['Nível 2: pergunta antes de capturar'], tags: ['NÍVEL 2'] },
  files: { t: 'ARQUIVOS', c: 'FS-11', g: null, s: 'Índice das pastas permitidas.', rows: [['ÍNDICE', 88]], lines: ['Fora da whitelist, nada é tocado'], tags: ['WHITELIST'] },
  browser: { t: 'REDE EXTERNA', c: 'NX-02', g: 'rede', s: 'Consultas e coleta de fontes abertas.', rows: [['LARGURA', 73], ['CACHE', 44]], lines: ['Conteúdo externo passa pelo sanitizador'], tags: ['SANITIZADO'] },
  maps: { t: 'GEOTELEMETRIA', c: 'MP-06', g: 'terra', s: 'Camadas orbitais e rotas ativas.', rows: [['SATÉLITES', 82], ['PRECISÃO', 96]], lines: ['LAT -23.55 / LON -46.63'], tags: ['ORBITAL', 'GPS'] },
  weather: { t: 'ANÁLISE ATMOSFÉRICA', c: 'AT-03', g: 'orbita', s: 'Clima do briefing diário (wttr.in).', rows: [['UMIDADE', 62], ['VENTO', 34]], lines: ['Fonte: wttr.in, sem chave'], tags: ['BRIEFING'] },
  media: { t: 'MÍDIA', c: 'MD-08', g: 'toro', s: 'Volume e reprodução do sistema.', rows: [['VOLUME', 58]], lines: ['Gestos controlam o volume'], tags: ['NÍVEL 1'] },
  systems: { t: 'DIAGNÓSTICO', c: 'SY-00', g: 'reator', s: 'Integridade de hardware e térmica.', rows: [['CPU', 88], ['MEMÓRIA', 72]], lines: ['check_hardware.py mede o real'], tags: ['FASE 0'] },
  network: { t: 'TRÁFEGO DE REDE', c: 'NT-05', g: 'rede', s: 'Fluxo entre os dois processos.', rows: [['DOWNLINK', 91], ['UPLINK', 37]], lines: ['IPC local na porta 47821'], tags: ['LOCAL'] },
  analytics: { t: 'ANÁLISE DE DADOS', c: 'AN-09', g: 'grafo', s: 'Métricas de mercado e correlação.', rows: [['MODELOS', 77], ['CONFIANÇA', 89]], lines: ['Análise, nunca recomendação'], tags: ['MATEMÁTICA PURA'] },
  security: { t: 'PERÍMETRO', c: 'SC-07', g: 'cristal', s: 'Guard determinístico e confirmações.', rows: [['GUARD', 100]], lines: ['O LLM decide o quê; o guard decide se pode'], tags: ['DETERMINÍSTICO'] },
  lab: { t: 'LABORATÓRIO 3D', c: 'LB-12', g: 'knot', s: 'Construção e inspeção de malhas holográficas.', rows: [['VÉRTICES', 68], ['MATERIAL', 52]], lines: ['Arraste para orbitar · roda: zoom'], tags: ['WIREFRAME'] },
  memory: { t: 'MEMÓRIA DE LONGO PRAZO', c: 'MM-10', g: 'cerebro', s: 'Fatos indexados com grau de confiança.', rows: [['ÍNDICE', 84]], lines: ['SQLite + FTS5, busca sem acento'], tags: ['DUAS CAMADAS'] },
  comms: { t: 'COMUNICAÇÃO', c: 'CM-13', g: 'satelite', s: 'Canais abertos e criptografia.', rows: [['SINAL', 86]], lines: ['Nada sai sem você mandar'], tags: ['E2E'] },
};

const MAX_WINDOWS = 3;

/* Estado do orquestrador Python -> legenda do HUD. O Python é a fonte da
   verdade; esta tabela só traduz para a linguagem da tela. */
const ESTADOS = {
  hidden: 'PRONTO', listening: 'ESCUTANDO', thinking: 'PROCESSANDO',
  speaking: 'RESPONDENDO', confirming: 'CONFIRMANDO', executing: 'EXECUTANDO',
  error: 'LIMITADO',
};

/* Quanto o núcleo pulsa em cada estado. 1 = repouso. */
const INTENSIDADE = {
  listening: 1.15, thinking: 1.35, executing: 1.5, speaking: 1.2, confirming: 1.25,
};

const norm = (s) => (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').trim();

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') n.className = v;
    else if (k === 'style') n.style.cssText = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v != null) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
}

class App {
  constructor(root) {
    this.root = root;
    this.mode = 'jarvis';
    this.active = 'core';
    this.open = ['maps'];
    this.sys = 'PRONTO';
    this.listening = false;
    this.conectado = false;
    this.pos = {};
    this.logs = [];
    this.vitals = { CPU: '—', MEM: '—', COTA: '—' };
    this.holos = {};
    this.tick = 0;

    this.build();
    this.mountCore();
    this.connect();
    this.renderWindows();

    setInterval(() => { this.tick++; this.paint(); }, 1000);
    window.addEventListener('pointermove', (e) => this.onMove(e));
    window.addEventListener('pointerup', () => { this.drag = null; });
    this.log('Interface holográfica montada.');
  }

  /* ------------------------------------------------------------ estrutura */

  build() {
    const hud = document.getElementById('hud');

    this.$identity = el('div', { class: 'identity' }, 'J A R V I S');
    this.$designation = el('div', { class: 'mono', 'data-narrow-hide2': '1', style: 'font-size:10px;letter-spacing:1.5px;color:var(--tech);white-space:nowrap' });
    this.$link = el('div', { class: 'mono', 'data-narrow-hide': '1', style: 'font-size:10px;letter-spacing:1.5px;color:var(--acc3);border:1px solid var(--line);padding:2px 6px;white-space:nowrap' });
    this.$operation = el('div', { class: 'mono', style: 'min-width:0;font-size:10.5px;letter-spacing:2px;color:var(--tech);overflow:hidden;text-overflow:ellipsis;white-space:nowrap' });
    this.$proc = el('div', { style: 'display:flex;gap:3px;align-items:flex-end;height:14px;flex:none' });
    this.$vitals = el('div', { style: 'display:flex;align-items:center;gap:14px;flex:none;font-family:var(--mono);font-size:10px;letter-spacing:1px;color:var(--tech)' });
    this.$clock = el('div', { class: 'mono', style: 'font-size:13px;letter-spacing:2px;color:var(--soft)' });

    const bar = el('div', { class: 'bar' },
      el('div', { style: 'display:flex;align-items:center;gap:12px;min-width:0;flex:0 1 auto;overflow:hidden' },
        el('div', { class: 'dot' }), this.$identity, this.$designation, this.$link),
      el('div', { style: 'flex:1;display:flex;align-items:center;justify-content:center;gap:12px;min-width:0' },
        this.$operation, this.$proc),
      el('div', { style: 'display:flex;align-items:center;gap:14px;flex:none' }, this.$vitals, this.$clock));

    // trilha de módulos
    this.$rail = el('div', { class: 'rail' });
    this.railBtns = {};
    for (const [k, n, ic] of MODS) {
      const b = el('button', { onclick: () => this.toggleWindow(k) },
        el('span', { class: 'ic' }, ic), el('span', { class: 'nm' }, n));
      this.railBtns[k] = b;
      this.$rail.append(b);
    }

    // centro: emblema de anéis + núcleo volumétrico atrás
    this.$sysState = el('div', { class: 'mono', style: 'font-size:10px;letter-spacing:5px;color:var(--acc);text-shadow:var(--glow)' });
    this.$coreSub = el('div', { class: 'mono', style: 'font-size:9px;letter-spacing:2px;color:var(--tech)' });
    this.$coreLabel = el('small', {}, 'PRONTO');
    this.$ultronLabel = el('small', {}, 'PRONTO');
    this.$canvas = el('canvas', { id: 'core' });
    this.$emblem = this.buildEmblem();
    this.$ultronRings = this.buildUltronRings();

    const center = el('div', { class: 'center' },
      el('div', { class: 'corewrap' }, this.$canvas, this.$emblem, this.$ultronRings),
      el('div', { style: 'position:absolute;left:0;right:0;bottom:6%;display:flex;flex-direction:column;align-items:center;gap:5px;pointer-events:none' },
        this.$sysState, this.$coreSub));

    // painel de contexto
    this.$ctxTitle = el('div', { style: 'font-size:11px;font-weight:600;letter-spacing:3.5px;color:var(--acc);text-shadow:var(--glow);overflow:hidden;text-overflow:ellipsis;white-space:nowrap' });
    this.$ctxCode = el('div', { class: 'mono', style: 'font-size:9px;color:var(--tech);flex:none' });
    this.$ctxSub = el('div', { class: 'mono', style: 'font-size:9.5px;letter-spacing:1.2px;color:var(--tech);line-height:1.5' });
    this.$ctxRows = el('div', { style: 'display:flex;flex-direction:column;gap:8px' });
    this.$graph = el('div', { style: 'display:flex;gap:2px;align-items:flex-end;height:44px;margin-top:2px' });
    this.$ctxLines = el('div', { style: 'display:flex;flex-direction:column;gap:5px' });
    this.$ctxTags = el('div', { style: 'display:flex;flex-wrap:wrap;gap:4px;margin-top:2px' });
    this.$logs = el('div', { style: 'margin-top:auto;display:flex;flex-direction:column;gap:3px;padding-top:10px' });

    const ctx = el('div', { class: 'ctx' },
      el('div', { style: 'display:flex;align-items:baseline;justify-content:space-between;gap:8px' }, this.$ctxTitle, this.$ctxCode),
      el('div', { style: 'height:1px;background:linear-gradient(90deg,var(--acc),transparent);opacity:.55' }),
      this.$ctxSub, this.$ctxRows, this.$graph, this.$ctxLines, this.$ctxTags, this.$logs);

    // barra de comando
    this.$input = el('input', {
      placeholder: 'Digite uma ordem ou fale "Jarvis"…',
      onkeydown: (e) => { if (e.key === 'Enter') this.submit(); },
    });
    this.$mic = el('button', { class: 'mono', onclick: () => this.toggleMic() }, 'VOZ');
    this.$modeBtn = el('button', { class: 'mono', onclick: () => this.toggleMode() }, 'ATIVAR ULTRON');
    this.$footer = el('div', { class: 'mono', style: 'font-size:8.5px;letter-spacing:2px;color:var(--tech)' });

    const cmd = el('div', { class: 'cmd' },
      el('div', { class: 'entry' },
        el('div', { class: 'mono', style: 'font-size:12px;color:var(--acc);text-shadow:var(--glow)' }, '>'),
        this.$input, this.$mic, this.$modeBtn),
      this.$footer);

    hud.append(bar, el('div', { class: 'body' }, this.$rail, center, ctx), cmd);
    this.paint();
  }

  /* O emblema: anéis segmentados vistos de frente, todos em CSS.
   *
   * Cada `<i>` é um anel: um `conic-gradient` recortado por uma máscara radial
   * que deixa só a faixa entre `--i` (borda de dentro) e `--o` (borda de fora).
   * É o truque que faz um gradiente cônico virar anel sem uma única imagem, e
   * é o que sustenta o custo baixo — o navegador compõe isso na GPU sem
   * geometria nenhuma.
   */
  buildEmblem() {
    const anel = (inset, fundo, extra = {}) => {
      const n = el('i', { class: `ring ${extra.cls || ''}`.trim() });
      n.style.inset = inset;
      n.style.background = fundo;
      n.style.setProperty('--i', extra.i || '90%');
      n.style.setProperty('--o', extra.o || '93%');
      if (extra.o2) n.style.setProperty('--o2', extra.o2);
      if (extra.opacity) n.style.opacity = extra.opacity;
      if (extra.filter) n.style.filter = extra.filter;
      return n;
    };

    const emblem = el('div', { class: 'emblem' });

    // halo interno
    const halo = el('i', {});
    halo.style.cssText = 'inset:6%;border-radius:50%;background:radial-gradient(closest-side,rgba(60,190,240,.16),rgba(20,120,180,.05) 62%,transparent 82%)';
    emblem.append(halo);

    // aro externo segmentado
    emblem.append(anel('0',
      'conic-gradient(from 24deg,#8fe4ff 0deg 22deg,transparent 22deg 68deg,#8fe4ff 68deg 90deg,transparent 90deg 158deg,#8fe4ff 158deg 178deg,transparent 178deg 248deg,#8fe4ff 248deg 268deg,transparent 268deg 336deg,#8fe4ff 336deg 356deg,transparent 356deg 360deg)',
      { i: '95.2%', o: '96.4%', o2: '99.4%', opacity: '.85' }));

    const borda = el('i', {});
    borda.style.cssText = 'inset:2.6%;border-radius:50%;border:1px solid rgba(140,225,255,.30)';
    emblem.append(borda);

    // pente fino girando devagar
    emblem.append(anel('4.5%',
      'repeating-conic-gradient(#a8ecff 0deg .55deg,transparent .55deg 4.4deg)',
      { i: '88.5%', o: '90.5%', o2: '99%', opacity: '.55', cls: 'spin-slow' }));

    // faixa larga — o "corpo" do reator
    emblem.append(anel('13%',
      'conic-gradient(from -6deg,#4fb6de 0deg 74deg,transparent 74deg 90deg,#5cc4ea 90deg 166deg,transparent 166deg 176deg,#49aed8 176deg 258deg,transparent 258deg 270deg,#56bfe6 270deg 350deg,transparent 350deg 360deg)',
      { i: '57%', o: '61%', o2: '95%', opacity: '.92', cls: 'spin-med' }));
    emblem.append(anel('13%',
      'radial-gradient(closest-side,transparent 58%,rgba(255,255,255,.34) 74%,rgba(255,255,255,.05) 86%,transparent 96%)',
      { i: '57%', o: '61%', o2: '95%', opacity: '.8' }));
    emblem.append(anel('14.5%',
      'repeating-conic-gradient(rgba(4,26,40,.85) 0deg 1.2deg,transparent 1.2deg 3.4deg)',
      { i: '78%', o: '81%', o2: '91%', opacity: '.7', cls: 'spin-rev' }));

    // os dois arcos âmbar: o contraste quente que faz o conjunto parecer ligado
    emblem.append(anel('16%',
      'conic-gradient(from 146deg,#ffb000 0deg 58deg,transparent 58deg 360deg)',
      { i: '54%', o: '57%', o2: '64%', filter: 'drop-shadow(0 0 6px rgba(255,176,0,.7))', cls: 'spin-fast' }));
    emblem.append(anel('11%',
      'conic-gradient(from 200deg,#ffc247 0deg 9deg,transparent 9deg 360deg)',
      { i: '86%', o: '88%', o2: '96%', opacity: '.9' }));

    // poço central
    const poco = el('i', {});
    poco.style.cssText = 'inset:30.5%;border-radius:50%;border:1px solid rgba(150,230,255,.45);'
      + 'box-shadow:0 0 14px rgba(80,200,245,.28),inset 0 0 22px rgba(10,60,90,.5);'
      + 'background:radial-gradient(closest-side,rgba(2,12,20,.72),rgba(2,10,16,.55))';
    emblem.append(poco);
    emblem.append(anel('33%',
      'repeating-conic-gradient(rgba(140,225,255,.22) 0deg .5deg,transparent .5deg 7deg)',
      { i: '74%', o: '78%', o2: '97%', cls: 'spin-inner' }));

    emblem.append(el('div', { class: 'core-name' },
      el('b', {}, 'J.A.R.V.I.S.'), this.$coreLabel));
    return emblem;
  }

  /* Os anéis do Ultron. No desenho original o emblema do J.A.R.V.I.S. some por
   * inteiro e entra este conjunto — não é o mesmo emblema repintado. Faz
   * sentido: o azul do reator é a identidade do J.A.R.V.I.S., e reaproveitá-lo
   * em âmbar entregaria "Jarvis de roupa nova" em vez de outra entidade.
   * Aqui os anéis usam `var(--acc)`, então seguem a paleta sozinhos.
   */
  buildUltronRings() {
    const anel = (inset, fundo, i, o, opacity, cls) => {
      const n = el('i', { class: `ring ${cls}` });
      n.style.inset = inset;
      n.style.background = fundo;
      n.style.setProperty('--i', i);
      n.style.setProperty('--o', o);
      n.style.setProperty('--o2', '100%');
      n.style.opacity = opacity;
      return n;
    };
    const wrap = el('div', { class: 'emblem ultron-rings' });
    const aro = el('i', {});
    aro.style.cssText = 'inset:0;border:1px solid var(--line2);border-radius:50%';
    wrap.append(aro);
    wrap.append(anel('1%', 'repeating-conic-gradient(var(--acc) 0deg .5deg,transparent .5deg 3.4deg)', '96.6%', '97.6%', '.40', 'spin-slow'));
    wrap.append(anel('4.5%', 'conic-gradient(from 0deg,var(--acc) 0deg 30deg,transparent 30deg 178deg,var(--acc) 178deg 202deg,transparent 202deg 360deg)', '96.4%', '97.4%', '.75', 'spin-fast'));
    wrap.append(anel('8%', 'repeating-conic-gradient(var(--acc3) 0deg 1.1deg,transparent 1.1deg 5.2deg)', '95%', '96.6%', '.30', 'spin-rev'));
    wrap.append(el('div', { class: 'core-name' },
      el('b', {}, 'U.L.T.R.O.N.'), this.$ultronLabel));
    return wrap;
  }

  /* --------------------------------------------------------------- pintura */

  /* Repintar é caro: reconstrói umas 60 caixas do DOM do zero. Vários pontos
   * chamam `paint()` em sequência — `log()` é o pior, e uma rajada de linhas
   * (na conexão, por exemplo) virava uma rajada de reconstruções, todas
   * descartadas menos a última.
   *
   * Juntar tudo num quadro faz N repinturas custarem uma. E amarrar ao rAF, em
   * vez de a um temporizador, faz o trabalho acontecer quando o navegador vai
   * desenhar de qualquer jeito — e parar sozinho com a aba escondida. */
  paint() {
    if (this._pinturaAgendada) return;
    this._pinturaAgendada = requestAnimationFrame(() => {
      this._pinturaAgendada = 0;
      this.pintarAgora();
    });
  }

  pintarAgora() {
    const ult = this.mode === 'ultron';
    const ctx = CTX[this.active] || CTX.core;
    const acc = ult ? '#ff8a00' : '#00e5ff';

    this.root.dataset.mode = this.mode;
    this.$identity.textContent = ult ? 'U L T R O N' : 'J A R V I S';
    this.$designation.textContent = ult ? 'ENTIDADE AUTÔNOMA · REV.∞' : 'SIST. INTEL. STARK · REV.7.2';
    this.$link.textContent = this.conectado ? (ult ? 'SEM RESTRIÇÕES' : 'ENLACE SEGURO') : 'JAMES OFFLINE';
    this.$link.classList.toggle('link-off', !this.conectado);
    this.$modeBtn.textContent = ult ? 'RESTAURAR JARVIS' : 'ATIVAR ULTRON';
    this.$mic.classList.toggle('live', this.listening);

    const op = this.sys === 'PRONTO' ? 'AGUARDANDO INSTRUÇÃO' : this.sys;
    this.$operation.textContent = `${op} · ${ctx.t}`;
    this.$sysState.textContent = this.sys;
    this.$coreLabel.textContent = this.sys;
    this.$ultronLabel.textContent = this.sys;
    // Um emblema por vez: o do Ultron não é o do J.A.R.V.I.S. repintado.
    this.$emblem.style.display = ult ? 'none' : 'grid';
    this.$ultronRings.style.display = ult ? 'grid' : 'none';
    this.$coreSub.textContent = ult
      ? `ENTIDADE ULTRON · ${this.open.length} JANELA(S) ABERTA(S)`
      : `NÚCLEO J.A.R.V.I.S. · ${this.open.length} JANELA(S) ABERTA(S)`;
    this.$clock.textContent = new Date().toLocaleTimeString('pt-BR', { hour12: false });
    this.$footer.textContent = 'ENTER EXECUTA · ARRASTE O NÚCLEO PARA ORBITAR · RODA PARA ZOOM';

    // equalizador da barra superior
    this.$proc.replaceChildren(...Array.from({ length: 9 }, (_, i) => el('div', {
      style: `width:3px;height:${20 + Math.abs(Math.sin(this.tick * 1.4 + i * 0.8)) * 80}%;`
        + `background:${acc};opacity:${0.4 + (i % 3) * 0.2};box-shadow:0 0 6px ${acc}`,
    })));

    // vitais reais, vindos do James
    this.$vitals.replaceChildren(...Object.entries(this.vitals).map(([k, v]) =>
      el('div', { style: 'display:flex;gap:5px;align-items:baseline;white-space:nowrap' },
        el('span', {}, k), el('span', { style: 'color:var(--acc3)' }, String(v)))));

    // trilha
    for (const [k] of MODS) {
      const b = this.railBtns[k];
      if (!b) continue;
      b.classList.toggle('on', this.active === k);
      b.classList.toggle('open', this.open.includes(k));
    }

    // contexto
    this.$ctxTitle.textContent = ctx.t;
    this.$ctxCode.textContent = ctx.c;
    this.$ctxSub.textContent = ctx.s;
    this.$ctxRows.replaceChildren(...ctx.rows.map(([l, v]) => el('div', { class: 'row' },
      el('div', { class: 'lbl' }, el('span', { style: 'color:var(--tech)' }, l),
        el('span', { style: 'color:var(--acc3)' }, `${v}%`)),
      el('div', { class: 'track' }, el('div', { class: 'fill', style: `width:${v}%` })))));
    this.$graph.replaceChildren(...Array.from({ length: 22 }, (_, i) => el('div', {
      style: `flex:1;height:${18 + Math.abs(Math.sin(this.tick * 0.7 + i * 0.55)) * 78}%;`
        + `background:${acc};opacity:${0.25 + (i % 4) * 0.14}`,
    })));
    this.$ctxLines.replaceChildren(...ctx.lines.map((l) => el('div', { class: 'bullet' },
      el('span', { style: 'color:var(--acc);font-size:7px' }, '◆'), el('span', {}, l))));
    this.$ctxTags.replaceChildren(...ctx.tags.map((g) => el('div', { class: 'tag' }, g)));
    this.$logs.replaceChildren(...this.logs.slice(-5).map(([t, m]) =>
      el('div', { style: 'display:flex;gap:6px;font-family:var(--mono);font-size:9px;color:var(--tech)' },
        el('span', { style: 'color:var(--acc);opacity:.75' }, t),
        el('span', { style: 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }, m))));
  }

  log(m) {
    const d = new Date();
    const t = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    this.logs = [...this.logs, [t, m]].slice(-40);
    this.paint();
  }

  /* -------------------------------------------------------- janelas holo */

  toggleWindow(k) {
    this.open = this.open.includes(k)
      ? this.open.filter((x) => x !== k)
      : [...this.open, k].slice(-MAX_WINDOWS);
    this.active = k;
    this.log(`${this.open.includes(k) ? 'Abrindo' : 'Encerrando'} ${CTX[k].t.toLowerCase()}`);
    this.renderWindows();
  }

  slot(i) {
    const W = window.innerWidth;
    const left = 110;
    const right = Math.max(left, W - Math.min(310, Math.max(230, W * 0.19)) - 285);
    return { x: i % 2 ? right : left, y: 70 + (i >= 2 ? 300 : 0) };
  }

  renderWindows() {
    const host = document.getElementById('windows');
    // solta as cenas das janelas que fecharam — cada uma tem seu WebGL
    for (const k of Object.keys(this.holos)) {
      if (!this.open.includes(k)) { this.holos[k].dispose?.(); delete this.holos[k]; }
    }
    host.replaceChildren();

    this.open.forEach((k, i) => {
      const c = CTX[k];
      const p = this.pos[k] || this.slot(i);
      const box = el('div', { class: 'win', style: `left:${p.x}px;top:${p.y}px;width:250px` });
      for (const s of ['left:0;top:0;width:16px;height:1px', 'left:0;top:0;width:1px;height:16px',
        'right:0;bottom:0;width:16px;height:1px', 'right:0;bottom:0;width:1px;height:16px']) {
        box.append(el('div', { class: 'corner', style: s }));
      }
      const head = el('div', { class: 'head', onpointerdown: (e) => this.dragStart(k, e, p) },
        el('div', { style: 'display:flex;align-items:center;gap:7px;min-width:0' },
          el('div', { style: 'width:6px;height:6px;background:var(--acc);box-shadow:var(--glow);flex:none' }),
          el('div', { style: 'font-size:10px;font-weight:600;letter-spacing:2.5px;color:var(--acc);overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }, c.t)),
        el('div', { style: 'display:flex;align-items:center;gap:8px;flex:none' },
          el('div', { class: 'mono', style: 'font-size:8.5px;color:var(--tech)' }, c.c),
          el('button', { class: 'mono', style: 'font-size:11px;color:var(--tech)', onclick: () => this.toggleWindow(k) }, '✕')));
      box.append(head);

      if (c.g) {
        const canvas = el('canvas', { style: 'position:absolute;inset:0;width:100%;height:100%;cursor:grab;touch-action:none' });
        box.append(el('div', { class: 'stage' }, canvas,
          el('div', { class: 'mono', style: 'position:absolute;left:6px;bottom:5px;font-size:8px;letter-spacing:1.4px;color:var(--tech);pointer-events:none' }, c.g.toUpperCase()),
          el('div', { class: 'mono credito', style: 'position:absolute;left:6px;top:5px;font-size:7.5px;letter-spacing:1px;color:var(--tech);opacity:.75;pointer-events:none;max-width:70%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }),
          el('div', { class: 'mono', style: 'position:absolute;right:6px;bottom:5px;font-size:8px;letter-spacing:1.4px;color:var(--acc3);pointer-events:none' }, 'ARRASTE')));
        this.spawnHolo(k, canvas, c.g);
      }

      const body = el('div', { style: 'display:flex;flex-direction:column;gap:7px;padding-top:9px' });
      for (const [l, v] of c.rows) {
        body.append(el('div', { class: 'row' },
          el('div', { class: 'lbl' }, el('span', { style: 'color:var(--tech)' }, l),
            el('span', { style: 'color:var(--acc3)' }, `${v}%`)),
          el('div', { class: 'track', style: 'margin-top:3px' }, el('div', { class: 'fill', style: `width:${v}%` }))));
      }
      for (const l of c.lines) {
        body.append(el('div', { class: 'bullet' },
          el('span', { style: 'color:var(--acc);font-size:7px' }, '◆'), el('span', {}, l)));
      }
      box.append(body);
      host.append(box);
      box.dataset.key = k;
    });
    this.paint();
  }

  dragStart(k, e, p) {
    this.drag = { k, x0: e.clientX, y0: e.clientY, px: p.x, py: p.y };
  }

  onMove(e) {
    if (!this.drag) return;
    const d = this.drag;
    const x = Math.max(0, Math.min(window.innerWidth - 260, d.px + (e.clientX - d.x0)));
    const y = Math.max(52, Math.min(window.innerHeight - 120, d.py + (e.clientY - d.y0)));
    this.pos[d.k] = { x, y };
    const box = document.querySelector(`.win[data-key="${d.k}"]`);
    if (box) { box.style.left = `${x}px`; box.style.top = `${y}px`; }
  }

  /* ------------------------------------------------------------------ 3D */

  /* Um governador para TODAS as cenas.
   *
   * O custo é somado: o núcleo mais cada janela holográfica aberta, cada um
   * com seu contexto WebGL. Se cada cena medisse a si mesma, cada uma
   * concluiria que está indo bem enquanto juntas travam a máquina — o quadro
   * lento aparece em todas, mas nenhuma sozinha explica o porquê.
   *
   * Medindo em conjunto, quando a máquina não segura, todas baixam juntas. */
  async governador() {
    if (!this.perf) {
      const { criarGovernador } = await import('./perf.js');
      this.perf = criarGovernador({
        aoMudar: (nivel) => this.log(`Qualidade reduzida para "${nivel}" — quadros lentos.`),
      });
    }
    return this.perf;
  }

  async mountCore() {
    const canvas = this.$canvas;
    if (!canvas) return;
    try {
      const { createCoreScene } = await import('./core-scene.js');
      this.core = createCoreScene(THREE, canvas, { governador: await this.governador() });
      this.log(`Motor volumétrico ativo · qualidade ${this.perf.nivel}`);
    } catch (err) {
      this.log('Falha ao montar o núcleo 3D.');
      console.error(err);
    }
  }

  async spawnHolo(k, canvas, subject) {
    try {
      const { createHoloScene } = await import('./holo-scene.js');
      this.holos[k] = createHoloScene(THREE, GLTFLoader, canvas, subject, this.mode, {
        governador: await this.governador(),
        cache: './models',
        // Cada resolução conta de onde o objeto veio. É o que deixa visível,
        // no próprio log da interface, qual nível da cascata respondeu.
        aoRegistrar: (nivel, detalhe) => this.registrarFonte(k, nivel, detalhe),
        aoMontar: (info) => this.creditar(k, info),
      });
    } catch (err) { console.error(err); }
  }

  /* De onde veio o objeto — aparece no log lateral. */
  registrarFonte(chave, nivel, detalhe) {
    const rotulos = {
      catalogo: 'catálogo', cache: 'cache local', remoto: 'baixado',
      generico: 'forma genérica', cache_falhou: 'cache falhou',
      remoto_falhou: 'download falhou',
    };
    this.log(`${chave}: ${rotulos[nivel] || nivel}${detalhe ? ` (${detalhe})` : ''}`);
  }

  /* CC-BY exige crédito. O rodapé da janela já existe: é onde ele cabe. */
  creditar(chave, info) {
    const box = document.querySelector(`.win[data-key="${chave}"] .credito`);
    if (!box) return;
    box.textContent = info.autor
      ? `${info.autor}${info.licenca ? ` · ${info.licenca}` : ''}`
      : (info.fonte === 'catalogo' ? 'PROCEDURAL' : '');
  }

  /* -------------------------------------------------------- ligação James */

  connect() {
    // O token vem na URL que o James abriu. Sem ele o servidor recusa — vale
    // tanto para o fluxo de estado quanto para os comandos.
    this.token = new URLSearchParams(location.search).get('token') || '';
    const source = new EventSource(`./events?token=${encodeURIComponent(this.token)}`);
    source.onopen = () => { this.conectado = true; this.paint(); };
    source.onerror = () => { this.conectado = false; this.paint(); };
    source.onmessage = (e) => {
      let dados;
      try { dados = JSON.parse(e.data); } catch { return; }
      this.apply(dados);
    };
    this.source = source;
  }

  apply(d) {
    if (d.estado) {
      this.sys = ESTADOS[d.estado] || d.estado.toUpperCase();
      // O núcleo pulsa mais forte quando o James está mesmo trabalhando. É o
      // ganho real de ligar dados verdadeiros: a tela para de fingir.
      this.core?.setIntensity?.(INTENSIDADE[d.estado] ?? 1);
    }
    if (d.vitals) this.vitals = d.vitals;
    if (d.modos) this.modos = d.modos;
    if (d.transcricao) this.log(`Você: "${d.transcricao}"`);
    if (d.resposta) this.log(d.resposta);
    if (d.log) this.log(d.log);
    if (d.holograma) this.projetar(d.holograma);
    if (d.holograma_fechar) this.fecharProjecoes();
    this.paint();
  }

  /* "Jarvis, mostra um cérebro" chega aqui pelo SSE.
   *
   * Assuntos livres viram uma janela `projecao:<assunto>`, fora do catálogo
   * fixo de módulos — assim o modelo pode pedir qualquer coisa sem que alguém
   * tenha que cadastrar um módulo antes. */
  projetar({ assunto, titulo }) {
    const texto = String(assunto || '').trim();
    if (!texto) return;
    const chave = `projecao:${norm(texto)}`;

    CTX[chave] = {
      t: (titulo || texto).toUpperCase().slice(0, 34),
      c: 'PRJ', g: texto,
      s: 'Projeção volumétrica sob demanda.',
      rows: [], lines: [], tags: ['HOLOGRAMA'],
    };
    if (!this.open.includes(chave)) {
      this.open = [...this.open, chave].slice(-MAX_WINDOWS);
    }
    this.active = chave;
    this.log(`Projetando: ${texto}`);
    this.renderWindows();
  }

  fecharProjecoes() {
    const antes = this.open.length;
    this.open = this.open.filter((k) => !k.startsWith('projecao:'));
    if (this.open.length !== antes) {
      this.log('Projeções encerradas.');
      this.renderWindows();
    }
  }

  async submit() {
    const texto = this.$input.value.trim();
    if (!texto) return;
    this.$input.value = '';

    // Comandos que são só da tela — não incomodam o James.
    const n = norm(texto);
    if (/^(ativar |modo )?ultron$/.test(n)) return this.toggleMode('ultron');
    if (/^(restaurar |modo )?jarvis$/.test(n)) return this.toggleMode('jarvis');
    const m = n.match(/^(abrir|abra|abre|fechar|feche|painel)\s+(.+)$/);
    if (m) {
      const alvo = MODS.find(([k]) => norm(CTX[k].t).includes(m[2]) || k === m[2]);
      if (alvo) return this.toggleWindow(alvo[0]);
    }

    this.log(`Você: "${texto}"`);
    try {
      const r = await fetch('./comando', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-James-Token': this.token },
        body: JSON.stringify({ texto }),
      });
      if (!r.ok) this.log(`James recusou o comando (${r.status}).`);
    } catch {
      this.log('Sem conexão com o James.');
    }
  }

  toggleMode(to) {
    const alvo = to || (this.mode === 'jarvis' ? 'ultron' : 'jarvis');
    if (alvo === this.mode) return;
    this.mode = alvo;
    this.core?.setMix?.(alvo === 'ultron' ? 1 : 0);
    for (const h of Object.values(this.holos)) h.setMode?.(alvo);
    // Apenas paleta e moldura. Nenhuma permissão muda — o guard está no Python.
    this.log(alvo === 'ultron' ? 'PROTOCOLO ULTRON · paleta reescrita' : 'Arquitetura J.A.R.V.I.S. restaurada');
  }

  toggleMic() {
    // O microfone de verdade é do processo Python (meia-duplex: só um dono).
    // Aqui o botão apenas avisa o James de que você quer falar agora.
    fetch('./escutar', { method: 'POST', headers: { 'X-James-Token': this.token } })
      .then(() => this.log('Escuta solicitada — diga o comando.'))
      .catch(() => this.log('Não consegui pedir a escuta ao James.'));
  }
}

new App(document.getElementById('root'));
