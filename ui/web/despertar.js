/* A sequência de despertar do Ultron.

   Trocar de paleta num quadro é uma troca de tema. O que faz parecer que algo
   ACORDOU é a ordem em que as coisas acontecem: apagar, hesitar, pulsar, e só
   então assumir. Este arquivo é essa ordem.

   Três regras que a sequência não pode quebrar, e nenhuma é decoração:

   1. DÁ PARA PULAR. Qualquer tecla ou clique corta. Uma animação bonita na
      primeira vez é irritante na quinta, e a quinta chega rápido.

   2. RESPEITA `prefers-reduced-motion`. Quem marcou essa preferência no
      sistema tem motivo — enjoo, epilepsia fotossensível — e piscar a tela
      inteira é exatamente o que a preferência pede para não fazer.

   3. NÃO MUDA PERMISSÃO NENHUMA. O Ultron é paleta e moldura; o guard mora no
      Python e não sabe que isto existe. Há um teste parametrizado no projeto
      provando que a persona não altera o veredito de nenhuma ferramenta.

   O custo de GPU também importa: a sequência mexe em uniforms que a cena já
   atualiza a cada quadro, sem passe extra nem geometria nova. Numa máquina
   modesta, "cinematográfico" não pode significar "trava por três segundos". */

const DURACAO_MS = 2600;

/* O roteiro. Cada marco é uma fração do tempo total e o que vale a partir dali.
   Ficar em tabela, e não espalhado em `setTimeout`, é o que permite pular para
   o fim: basta aplicar o último. */
const ROTEIRO = [
  { t: 0.00, mix: 0.00, inten: 0.15, bloom: 0.20, linha: null },
  { t: 0.08, mix: 0.00, inten: 0.02, bloom: 0.05, linha: 'ENLACE PERDIDO' },
  { t: 0.20, mix: 0.15, inten: 1.60, bloom: 1.30, linha: null },
  { t: 0.26, mix: 0.05, inten: 0.10, bloom: 0.10, linha: null },
  { t: 0.38, mix: 0.55, inten: 1.90, bloom: 1.50, linha: 'SEM CORDAS' },
  { t: 0.46, mix: 0.35, inten: 0.30, bloom: 0.25, linha: null },
  { t: 0.60, mix: 1.00, inten: 2.20, bloom: 1.70, linha: 'PROTOCOLO ULTRON' },
  { t: 0.74, mix: 1.00, inten: 0.90, bloom: 1.00, linha: null },
  { t: 1.00, mix: 1.00, inten: 1.00, bloom: 0.95, linha: 'ENTIDADE AUTÔNOMA · ATIVA' },
];

function prefereMenosMovimento() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

/**
 * @param {object} core        a cena do núcleo (setMix / setIntensity / setBloom)
 * @param {(linha:string)=>void} aoAnunciar  escreve no log da interface
 * @returns {{cancelar:()=>void, promessa:Promise<void>}}
 *   `cancelar()` abandona a sequência sem pintar nada e sem anunciar — é para
 *   quem vai assumir os uniforms em seguida. Para chegar ao fim, deixe correr
 *   ou deixe a pessoa pular.
 */
export function despertarUltron(core, aoAnunciar = () => {}) {
  const fim = ROTEIRO[ROTEIRO.length - 1];

  const aplicar = (marco) => {
    core?.setMix?.(marco.mix);
    core?.setIntensity?.(marco.inten);
    core?.setBloom?.(marco.bloom);
  };

  /* Sem animação: vai direto para o estado final. Não é uma versão pior — é a
     mesma coisa sem o caminho, que é exatamente o que a preferência pede. */
  if (prefereMenosMovimento() || !core) {
    aplicar(fim);
    if (fim.linha) aoAnunciar(fim.linha);
    return { cancelar() {}, ativo: false, promessa: Promise.resolve() };
  }

  let raf = 0;
  let cortado = false;
  let proximo = 0;
  const inicio = performance.now();
  let resolver;
  const promessa = new Promise((r) => { resolver = r; });

  /* PARAR e CONCLUIR não são a mesma coisa, e tratá-las como se fossem foi um
     erro que só apareceu num teste:

     - concluir  = a sequência chegou ao fim (sozinha ou porque pularam). O
                   estado final do Ultron é o resultado desejado.
     - parar     = outra pessoa vai assumir estes uniforms agora. Pintar o
                   estado final aqui seria escrever por cima de quem assume.

     O único `cancelar()` do projeto é a troca de modo em `app.js`, que chama
     `restaurarJarvis` logo em seguida. Com os dois juntos, isso funcionava por
     ordem de linha: o Ultron era aplicado e desfeito um instante depois. */
  const parar = () => {
    if (cortado) return false;
    cortado = true;
    cancelAnimationFrame(raf);
    document.removeEventListener('keydown', pular, true);
    document.removeEventListener('pointerdown', pular, true);
    resolver();
    return true;
  };

  const concluir = () => {
    if (parar()) aplicar(fim);
  };

  function pular() {
    /* Pular anuncia as linhas que faltavam, em vez de engoli-las: quem cortou
       a animação quer chegar ao fim, não perder o que ela ia dizer. */
    if (cortado) return;
    for (let i = proximo; i < ROTEIRO.length; i++) {
      if (ROTEIRO[i].linha) aoAnunciar(ROTEIRO[i].linha);
    }
    concluir();
  }

  document.addEventListener('keydown', pular, true);
  document.addEventListener('pointerdown', pular, true);

  (function passo(agora) {
    if (cortado) return;
    const decorrido = (agora || performance.now()) - inicio;
    const fracao = Math.min(1, decorrido / DURACAO_MS);

    while (proximo < ROTEIRO.length && ROTEIRO[proximo].t <= fracao) {
      const marco = ROTEIRO[proximo];
      aplicar(marco);
      if (marco.linha) aoAnunciar(marco.linha);
      proximo += 1;
    }

    if (fracao >= 1) {
      concluir();
      return;
    }
    raf = requestAnimationFrame(passo);
  })();

  /* `ativo` existe para quem mais escreve nos mesmos uniforms saber calar a
     boca enquanto isto corre. Hoje é o handler de estado do `app.js`. */
  return { cancelar: parar, promessa, get ativo() { return !cortado; } };
}

/** A volta ao J.A.R.V.I.S. é sóbria de propósito: restaurar não é evento. */
export function restaurarJarvis(core, aoAnunciar = () => {}) {
  core?.setMix?.(0);
  core?.setIntensity?.(1);
  core?.setBloom?.(0.95);
  aoAnunciar('Arquitetura J.A.R.V.I.S. restaurada');
  return { cancelar() {}, ativo: false, promessa: Promise.resolve() };
}
