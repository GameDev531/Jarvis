/* Governador de quadros — mede a máquina em vez de adivinhar o que ela aguenta.
 *
 * A interface foi desenhada num computador bom e roda num Sandy Bridge de 2011
 * com gráficos integrados. Chutar um nível de qualidade fixo erra dos dois
 * lados: trava a máquina fraca ou entrega menos do que a boa poderia dar.
 *
 * Então o navegador decide, com o único dado que só ele tem: quanto tempo um
 * quadro está realmente levando.
 *
 * Três alavancas, nesta ordem de eficácia numa GPU integrada:
 *
 *   1. TETO DE QUADROS. Uma interface ambiente a 30 fps é indistinguível de
 *      60, e custa metade. É a economia mais barata que existe aqui.
 *   2. DENSIDADE DE PIXEL. Custo cresce com o quadrado. Numa tela 1.5x, cair
 *      para 1.0 corta 55% dos pixels.
 *   3. BLOOM. São 4 passes extras de tela cheia. Numa GPU integrada, onde o
 *      gargalo é banda de memória, é a conta mais cara do quadro.
 *
 * Nada disso muda o desenho — muda quanto ele custa.
 */

/** Ordem importa: cada nível é o anterior menos alguma coisa. */
export const NIVEIS = ['alta', 'media', 'baixa'];

const PERFIS = {
  alta:  { fps: 60, dpr: 1.5, bloom: true,  escalaBloom: 2 },
  media: { fps: 30, dpr: 1.0, bloom: true,  escalaBloom: 4 },
  baixa: { fps: 30, dpr: 1.0, bloom: false, escalaBloom: 4 },
};

const CHAVE = 'james.qualidade';

/* Quantos quadros lentos seguidos antes de baixar o nível. Um número baixo
 * demais reage a engasgo de carregamento — que é justamente quando o quadro
 * SEMPRE demora, e não diz nada sobre a máquina. */
const QUADROS_LENTOS_PARA_BAIXAR = 45;

/* Margem: só conta como lento o quadro que passa de 1,35x o orçamento. Sem
 * isso, um alvo de 30 fps (33,3 ms) rebaixaria a si mesmo pela variação normal
 * do próprio rAF. */
const TOLERANCIA = 1.35;


function lerPreferencia() {
  try {
    const salvo = localStorage.getItem(CHAVE);
    return NIVEIS.includes(salvo) ? salvo : null;
  } catch {
    // Modo privado, cookies bloqueados. Não saber a preferência é um
    // aborrecimento; quebrar a interface por causa disso, não.
    return null;
  }
}

function salvarPreferencia(nivel) {
  try { localStorage.setItem(CHAVE, nivel); } catch { /* idem */ }
}

/**
 * @param {object} opcoes
 * @param {string} [opcoes.nivel]      força um nível e desliga a adaptação
 * @param {boolean} [opcoes.adaptar]   baixar sozinho quando não segurar (padrão: true)
 * @param {(nivel: string, perfil: object) => void} [opcoes.aoMudar]
 */
export function criarGovernador(opcoes = {}) {
  const forcado = NIVEIS.includes(opcoes.nivel) ? opcoes.nivel : lerPreferencia();
  let indice = forcado ? NIVEIS.indexOf(forcado) : 0;
  // Preferência explícita é escolha de quem usa; adaptar por cima seria
  // desfazer em silêncio o que a pessoa pediu.
  let adaptar = opcoes.adaptar !== false && !forcado;

  let lentos = 0;

  const perfil = () => PERFIS[NIVEIS[indice]];

  function baixar() {
    if (indice >= NIVEIS.length - 1) {
      adaptar = false;                       // no fundo do poço, parar de medir
      return;
    }
    indice += 1;
    lentos = 0;
    const nivel = NIVEIS[indice];
    console.info(`[james] Qualidade reduzida para "${nivel}" — quadros acima do orçamento.`);
    opcoes.aoMudar?.(nivel, perfil());
  }

  const governo = {
    get nivel() { return NIVEIS[indice]; },
    get perfil() { return perfil(); },

    /** Fixa um nível e para de adaptar. Persiste entre sessões. */
    definir(nivel) {
      if (!NIVEIS.includes(nivel)) return false;
      indice = NIVEIS.indexOf(nivel);
      adaptar = false;
      lentos = 0;
      salvarPreferencia(nivel);
      opcoes.aoMudar?.(nivel, perfil());
      return true;
    },

    /**
     * Um marcador de ritmo POR CENA. Chame uma vez na montagem e use a função
     * devolvida no topo do laço; `false` = pular este quadro inteiro.
     *
     * O ritmo é por cena de propósito, e isso já custou caro uma vez: quando o
     * instante do último quadro morava aqui, compartilhado, a primeira cena a
     * pedir no quadro desenhava e carimbava o relógio — e a segunda levava
     * `false`, porque não havia passado tempo nenhum. A janela holográfica
     * simplesmente parou de desenhar, e só a comparação de imagem pegou.
     *
     * A DECISÃO de qualidade é compartilhada (a máquina é uma só); o RITMO
     * não pode ser (cada cena tem o seu laço).
     *
     * Aba escondida é o caso mais valioso: o navegador já estrangula o rAF,
     * mas numa janela lado a lado ele continua a 60 fps desenhando para
     * ninguém — e é aí que a máquina fraca estava sendo consumida.
     */
    criarRitmo() {
      let ultimo = 0;
      return (agora) => {
        if (typeof document !== 'undefined' && document.hidden) return false;
        const intervalo = 1000 / perfil().fps;
        // Meio quadro de folga: sem isso, o teto de 30 fps vira 20 na prática,
        // porque um rAF de 60 Hz quase nunca cai exatamente no múltiplo.
        if (agora - ultimo < intervalo - 8) return false;
        ultimo = agora;
        return true;
      };
    },

    /** Desliga o medidor. */
    parar() { medindo = false; },
  };

  /* ------------------------------------------------------- o medidor
   *
   * Mede o intervalo REAL entre quadros da página, com um rAF próprio que não
   * desenha nada — só subtrai dois números.
   *
   * A primeira versão disto media quanto cada cena levava para desenhar, e não
   * funcionava: o custo é somado (o núcleo mais cada janela holográfica), mas
   * cada cena via só a própria parte. Duas cenas de 12 ms cabem folgadamente
   * no orçamento de 16,7 ms *cada uma*, enquanto juntas entregam 39 fps. Toda
   * cena concluía que estava indo bem, e a adaptação nunca disparava —
   * confirmado medindo: a redução automática jamais aconteceu, nem quando a
   * página rodava a 25 fps.
   *
   * O intervalo entre quadros não tem esse ponto cego. Ele é o resultado, não
   * uma parcela dele: se a página não está entregando, aparece aqui,
   * independente de quantas cenas existem ou de qual delas é a culpada. */
  let medindo = true;
  let anterior = 0;

  (function medir(agora) {
    if (!medindo) return;
    requestAnimationFrame(medir);
    if (!adaptar || (typeof document !== 'undefined' && document.hidden)) {
      anterior = 0;                    // aba oculta: o intervalo não diz nada
      return;
    }
    if (!anterior) { anterior = agora || performance.now(); return; }

    const intervalo = (agora || performance.now()) - anterior;
    anterior = agora || performance.now();

    if (intervalo > (1000 / perfil().fps) * TOLERANCIA) {
      lentos += 1;
      if (lentos >= QUADROS_LENTOS_PARA_BAIXAR) baixar();
    } else if (lentos > 0) {
      // Um quadro bom no meio de vários ruins não zera a conta, mas alivia:
      // o que interessa é lentidão sustentada, não um pico isolado.
      lentos -= 1;
    }
  })();

  return governo;
}

/**
 * Observa o tamanho do canvas sem ler `clientWidth` a cada quadro.
 *
 * Ler `clientWidth` dentro do laço força o navegador a recalcular o layout
 * antes de responder — uma vez por quadro, por cena. Com duas cenas abertas
 * são 120 recálculos por segundo para descobrir um número que muda quando a
 * janela é redimensionada, ou seja, quase nunca.
 *
 * Devolve uma função que desliga o observador.
 */
export function observarTamanho(canvas, aoMudar) {
  let largura = 0;
  let altura = 0;

  const conferir = (w, h) => {
    w = Math.round(w); h = Math.round(h);
    if (!w || !h || (w === largura && h === altura)) return;
    largura = w; altura = h;
    aoMudar(w, h);
  };

  if (typeof ResizeObserver === 'undefined') {
    // Navegador antigo: cai para o evento de janela. Pior que o observador
    // (não pega mudança de layout sem resize), mas melhor que ler por quadro.
    const aoRedimensionar = () => conferir(canvas.clientWidth, canvas.clientHeight);
    window.addEventListener('resize', aoRedimensionar);
    aoRedimensionar();
    return () => window.removeEventListener('resize', aoRedimensionar);
  }

  const observador = new ResizeObserver((entradas) => {
    for (const entrada of entradas) {
      // `contentRect` já vem calculado pelo navegador: lê-lo não força layout.
      conferir(entrada.contentRect.width, entrada.contentRect.height);
    }
  });
  observador.observe(canvas);
  conferir(canvas.clientWidth, canvas.clientHeight);   // uma vez, na montagem
  return () => observador.disconnect();
}

/**
 * Diz se o canvas está visível na tela.
 *
 * Cada janela holográfica aberta é um contexto WebGL com laço próprio. Uma
 * janela rolada para fora da vista continuava desenhando a 60 fps. Aqui o
 * ganho não é aparar gordura: é deixar de pagar por completo pelo que ninguém
 * está vendo.
 */
export function observarVisibilidade(canvas, aoMudar) {
  if (typeof IntersectionObserver === 'undefined') {
    aoMudar(true);
    return () => {};
  }
  const observador = new IntersectionObserver(
    (entradas) => entradas.forEach((e) => aoMudar(e.isIntersecting)),
    { threshold: 0.01 },
  );
  observador.observe(canvas);
  return () => observador.disconnect();
}
