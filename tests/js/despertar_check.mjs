/* Verificação da sequência de despertar do Ultron, fora do navegador.

   `despertar.js` é animação, e animação é o tipo de código que ninguém testa —
   "é só visual". Mas as três regras que esse arquivo promete no cabeçalho não
   são visuais: pular de verdade, respeitar `prefers-reduced-motion` e não
   deixar ouvinte pendurado no `document`. Nenhuma delas aparece olhando a tela
   uma vez; todas aparecem na quinta vez, e aí já incomodam.

   O relógio aqui é falso de propósito. Uma animação de 2,6 s testada em tempo
   real levaria 2,6 s e ainda seria instável; com `performance.now` e
   `requestAnimationFrame` sob controle, cada quadro é uma linha de teste.

   Saída: uma linha `ok - <nome>` por caso, e código 1 na primeira falha. */

import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const MODULO = pathToFileURL(
  new URL('../../ui/web/despertar.js', import.meta.url).pathname,
).href;

/* ---------------------------------------------------------------- o palco */

/** DOM mínimo: só o que `despertar.js` realmente toca. */
function montarPalco({ reduzirMovimento = false } = {}) {
  const ouvintes = new Map();          // tipo -> Set de funções
  const quadros = new Map();           // id -> callback pendente
  let proximoId = 1;
  let agora = 0;

  globalThis.document = {
    addEventListener(tipo, fn) {
      if (!ouvintes.has(tipo)) ouvintes.set(tipo, new Set());
      ouvintes.get(tipo).add(fn);
    },
    removeEventListener(tipo, fn) {
      ouvintes.get(tipo)?.delete(fn);
    },
  };
  globalThis.window = {
    matchMedia: (consulta) => ({
      matches: reduzirMovimento && consulta.includes('reduce'),
    }),
  };
  globalThis.performance = { now: () => agora };
  globalThis.requestAnimationFrame = (fn) => {
    const id = proximoId++;
    quadros.set(id, fn);
    return id;
  };
  globalThis.cancelAnimationFrame = (id) => quadros.delete(id);

  return {
    /** Avança o relógio e entrega UM quadro, como o navegador faria. */
    avancar(ms) {
      agora += ms;
      const pendentes = [...quadros.entries()];
      quadros.clear();
      for (const [, fn] of pendentes) fn(agora);
    },
    disparar(tipo) {
      for (const fn of [...(ouvintes.get(tipo) ?? [])]) fn();
    },
    ouvintesDe: (tipo) => ouvintes.get(tipo)?.size ?? 0,
    quadrosPendentes: () => quadros.size,
  };
}

/** Um núcleo que só anota o que mandaram nele. */
function nucleoFalso() {
  const registro = { mix: [], inten: [], bloom: [] };
  return {
    registro,
    setMix: (v) => registro.mix.push(v),
    setIntensity: (v) => registro.inten.push(v),
    setBloom: (v) => registro.bloom.push(v),
    ultimo: () => ({
      mix: registro.mix.at(-1),
      inten: registro.inten.at(-1),
      bloom: registro.bloom.at(-1),
    }),
  };
}

const FINAL = { mix: 1, inten: 1, bloom: 0.95 };
const LINHAS = [
  'ENLACE PERDIDO',
  'SEM CORDAS',
  'PROTOCOLO ULTRON',
  'ENTIDADE AUTÔNOMA · ATIVA',
];

/* --------------------------------------------------------------- os casos */

const casos = [];
const caso = (nome, fn) => casos.push([nome, fn]);

caso('a sequência inteira anuncia as quatro linhas, em ordem, uma vez cada', async () => {
  const palco = montarPalco();
  const { despertarUltron } = await import(MODULO);
  const core = nucleoFalso();
  const ditas = [];

  const seq = despertarUltron(core, (l) => ditas.push(l));
  for (let i = 0; i < 200; i++) palco.avancar(16.7);   // ~3,3 s de quadros
  await seq.promessa;

  assert.deepEqual(ditas, LINHAS);
  assert.deepEqual(core.ultimo(), FINAL);
});

caso('pular no meio corta a animação e não perde nenhuma linha', async () => {
  const palco = montarPalco();
  const { despertarUltron } = await import(MODULO);
  const core = nucleoFalso();
  const ditas = [];

  const seq = despertarUltron(core, (l) => ditas.push(l));
  palco.avancar(300);                                  // passou o 1º marco
  assert.deepEqual(ditas, ['ENLACE PERDIDO']);

  palco.disparar('keydown');
  await seq.promessa;

  // O acordo é chegar ao fim, não perder o que faltava ser dito.
  assert.deepEqual(ditas, LINHAS);
  assert.deepEqual(core.ultimo(), FINAL);
  assert.equal(palco.quadrosPendentes(), 0, 'ficou quadro agendado após pular');
});

caso('clique também pula', async () => {
  const palco = montarPalco();
  const { despertarUltron } = await import(MODULO);
  const ditas = [];
  const seq = despertarUltron(nucleoFalso(), (l) => ditas.push(l));

  palco.avancar(16.7);
  palco.disparar('pointerdown');
  await seq.promessa;

  assert.deepEqual(ditas, LINHAS);
});

caso('os ouvintes saem do document quando termina', async () => {
  const palco = montarPalco();
  const { despertarUltron } = await import(MODULO);
  const ditas = [];
  const seq = despertarUltron(nucleoFalso(), (l) => ditas.push(l));

  assert.equal(palco.ouvintesDe('keydown'), 1);
  for (let i = 0; i < 200; i++) palco.avancar(16.7);
  await seq.promessa;

  assert.equal(palco.ouvintesDe('keydown'), 0, 'ouvinte de teclado ficou pendurado');
  assert.equal(palco.ouvintesDe('pointerdown'), 0, 'ouvinte de clique ficou pendurado');

  // E a prova de que importa: uma tecla depois do fim não repete nada.
  palco.disparar('keydown');
  assert.deepEqual(ditas, LINHAS);
});

caso('prefers-reduced-motion vai direto ao fim, sem agendar um quadro sequer', async () => {
  const palco = montarPalco({ reduzirMovimento: true });
  const { despertarUltron } = await import(MODULO);
  const core = nucleoFalso();
  const ditas = [];

  const seq = despertarUltron(core, (l) => ditas.push(l));
  await seq.promessa;

  assert.equal(palco.quadrosPendentes(), 0, 'animou apesar da preferência');
  assert.equal(palco.ouvintesDe('keydown'), 0, 'prendeu o teclado sem precisar');
  assert.deepEqual(core.ultimo(), FINAL);
  // Fica o anúncio final: o estado é dito, só a piscada é que não acontece.
  assert.deepEqual(ditas, ['ENTIDADE AUTÔNOMA · ATIVA']);
});

caso('cancelar abandona em silêncio, sem pintar o estado final', async () => {
  /* Cancelar é troca de dono, não conclusão. Quem cancela vai escrever nos
     mesmos uniforms na linha seguinte; aplicar o Ultron aqui seria pintar por
     cima de quem assume — e funcionava só pela ordem das linhas em `app.js`. */
  const palco = montarPalco();
  const { despertarUltron } = await import(MODULO);
  const core = nucleoFalso();
  const ditas = [];

  const seq = despertarUltron(core, (l) => ditas.push(l));
  palco.avancar(300);
  const antes = core.ultimo();
  seq.cancelar();
  seq.cancelar();                                      // idempotente
  await seq.promessa;

  assert.deepEqual(core.ultimo(), antes, 'cancelar mexeu na cena');
  assert.deepEqual(ditas, ['ENLACE PERDIDO'], 'cancelar anunciou o que não viveu');
  assert.equal(palco.quadrosPendentes(), 0);
  assert.equal(palco.ouvintesDe('keydown'), 0);
});

caso('trocar de modo no meio do despertar termina no Jarvis', async () => {
  /* O caso real do `app.js`: apertar ATIVAR ULTRON e voltar antes de acabar.
     O que não pode acontecer é a sequência morta dar o último palpite. */
  const palco = montarPalco();
  const { despertarUltron, restaurarJarvis } = await import(MODULO);
  const core = nucleoFalso();

  const seq = despertarUltron(core, () => {});
  palco.avancar(900);
  seq.cancelar();
  restaurarJarvis(core, () => {});

  for (let i = 0; i < 200; i++) palco.avancar(16.7);   // quadros zumbis
  assert.equal(core.ultimo().mix, 0, 'a cena voltou a ficar roxa sozinha');
});

caso('`ativo` diz a verdade — é ele que cala o resto enquanto a cena é nossa', async () => {
  /* O handler de estado do `app.js` escreve na MESMA intensidade a cada evento
     recebido. Se `ativo` mentisse, um estado chegando no meio devolveria o
     núcleo ao repouso por um quadro, e a sequência tremeria. */
  const palco = montarPalco();
  const { despertarUltron, restaurarJarvis } = await import(MODULO);

  const seq = despertarUltron(nucleoFalso(), () => {});
  assert.equal(seq.ativo, true);
  palco.avancar(300);
  assert.equal(seq.ativo, true);

  for (let i = 0; i < 200; i++) palco.avancar(16.7);
  await seq.promessa;
  assert.equal(seq.ativo, false, 'continuou "ativo" depois de acabar');

  // Quem não anima nunca é dono da cena: não pode calar ninguém.
  assert.equal(despertarUltron(null).ativo, false);
  assert.equal(restaurarJarvis(nucleoFalso()).ativo, false);
  assert.equal(
    montarPalco({ reduzirMovimento: true }) && (await import(MODULO)).despertarUltron(
      nucleoFalso(),
    ).ativo,
    false,
  );
});

caso('cancelar também libera a cena para os outros', async () => {
  const palco = montarPalco();
  const { despertarUltron } = await import(MODULO);
  const seq = despertarUltron(nucleoFalso(), () => {});
  palco.avancar(300);
  seq.cancelar();
  assert.equal(seq.ativo, false, 'cancelou mas continuou bloqueando a cena');
});

caso('sem núcleo (WebGL ainda não montou) não estoura', async () => {
  montarPalco();
  const { despertarUltron } = await import(MODULO);
  const ditas = [];
  const seq = despertarUltron(null, (l) => ditas.push(l));
  await seq.promessa;
  assert.deepEqual(ditas, ['ENTIDADE AUTÔNOMA · ATIVA']);
});

caso('sem callback de anúncio também não estoura', async () => {
  const palco = montarPalco();
  const { despertarUltron } = await import(MODULO);
  const seq = despertarUltron(nucleoFalso());
  for (let i = 0; i < 200; i++) palco.avancar(16.7);
  await seq.promessa;
});

caso('restaurar volta a paleta do J.A.R.V.I.S. sem animação', async () => {
  const palco = montarPalco();
  const { restaurarJarvis } = await import(MODULO);
  const core = nucleoFalso();
  const ditas = [];

  restaurarJarvis(core, (l) => ditas.push(l));

  assert.equal(core.ultimo().mix, 0);
  assert.equal(palco.quadrosPendentes(), 0);
  assert.equal(ditas.length, 1);
});

caso('a mistura sai de 0, hesita, e nunca volta a ser Jarvis no caminho', async () => {
  /* Os recuos são o efeito: a paleta do Ultron entra, é empurrada de volta, e
     na terceira tentativa assume. É a "hesitação" do roteiro, não um defeito.

     O que seria defeito é a mistura VOLTAR A ZERO no meio: por um quadro a tela
     inteira diria "sou o Jarvis de novo", bem no meio do despertar do Ultron.
     E os picos precisam subir — se o segundo lampejo fosse mais fraco que o
     primeiro, a leitura vira "está desistindo", não "está assumindo". */
  const palco = montarPalco();
  const { despertarUltron } = await import(MODULO);
  const core = nucleoFalso();

  const seq = despertarUltron(core, () => {});
  for (let i = 0; i < 200; i++) palco.avancar(16.7);
  await seq.promessa;

  const mix = core.registro.mix;
  assert.equal(mix[0], 0, 'começou já misturado');
  assert.equal(mix.at(-1), 1, 'não terminou Ultron por inteiro');

  const depoisDoPrimeiroLampejo = mix.slice(mix.findIndex((v) => v > 0));
  assert.ok(
    depoisDoPrimeiroLampejo.every((v) => v > 0),
    'a mistura voltou a zero no meio — pisca "sou o Jarvis" durante o despertar',
  );

  const picos = [];
  for (let i = 1; i < mix.length - 1; i++) {
    if (mix[i] > mix[i - 1] && mix[i] >= mix[i + 1]) picos.push(mix[i]);
  }
  picos.push(mix.at(-1));
  // Repetidos não contam: chegando a 1 a mistura fica em 1, e ficar não é
  // enfraquecer. O que precisa subir é cada lampejo NOVO.
  const distintos = picos.filter((v, i) => i === 0 || v !== picos[i - 1]);
  assert.ok(distintos.length >= 3, `hesitação de menos: ${distintos}`);
  for (let i = 1; i < distintos.length; i++) {
    assert.ok(
      distintos[i] > distintos[i - 1],
      `lampejo enfraqueceu: ${distintos[i - 1]} -> ${distintos[i]}`,
    );
  }
});

/* ------------------------------------------------------------------ corrida */

let falhas = 0;
for (const [nome, fn] of casos) {
  try {
    await fn();
    console.log(`ok - ${nome}`);
  } catch (erro) {
    falhas += 1;
    console.log(`FALHOU - ${nome}`);
    console.log(String(erro.message ?? erro).split('\n').map((l) => `    ${l}`).join('\n'));
  }
}
console.log(`\n${casos.length - falhas}/${casos.length} casos`);
process.exit(falhas ? 1 : 0);
