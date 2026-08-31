/* Uma janela de projeção — um canvas, uma cena, um objeto.
 *
 * Cada janela holográfica aberta tem a sua instância. Isso custa um contexto
 * WebGL por janela, e é por isso que a interface limita quantas ficam abertas:
 * navegador tem teto de contextos (por volta de 16), e estourar esse teto
 * derruba os mais antigos sem aviso.
 *
 * A cena aqui é deliberadamente magra — sem bloom, sem pós-processamento. O
 * brilho vem do material holográfico com blending aditivo, que não custa passe
 * extra. O núcleo central (core-scene.js) é quem paga por bloom, porque é um
 * só; multiplicar isso por janela travaria a máquina.
 */

import { CORES, createHolographicMaterial } from './holo-material.js';
import { animarCatalogo, resolver } from './holo-resolver.js';
import { criarGovernador, observarTamanho, observarVisibilidade } from './perf.js';

export function createHoloScene(T, GLTFLoader, canvas, assunto, modo = 'jarvis', opcoes = {}) {
  const renderer = new T.WebGLRenderer({ canvas, alpha: true, antialias: true });

  /* Cada janela aberta multiplica este custo, então o governador é
     compartilhado: se a máquina não segura, todas baixam juntas. Uma janela
     decidindo sozinha que está tudo bem enquanto as outras engasgam não
     descreve máquina nenhuma. */
  const governador = opcoes.governador || criarGovernador();
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, governador.perfil.dpr));

  const ritmo = governador.criarRitmo();

  const cena = new T.Scene();
  const camera = new T.PerspectiveCamera(42, 1, 0.1, 100);
  const suporte = new T.Group();
  cena.add(suporte);

  const material = createHolographicMaterial(T, { hologramColor: CORES[modo] || CORES.jarvis });
  const relogio = new T.Clock();

  let objeto = null;
  let atual = modo;
  let raf = 0;
  let vivo = true;
  let geracao = 0;                  // corrida: descarta resposta de pedido antigo
  const camadas = { th: 0.6, ph: 1.35, d: 3.4, tth: 0.6, tph: 1.35, td: 3.4 };

  /* --------------------------------------------------------------- objeto */

  function limpar() {
    if (!objeto) return;
    suporte.remove(objeto);
    objeto.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      // O material holográfico é compartilhado e vive enquanto a cena viver;
      // descartá-lo aqui deixaria o próximo objeto sem material.
      if (o.material && o.material !== material) {
        (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m.dispose());
      }
    });
    objeto = null;
  }

  async function montar(texto) {
    const meu = ++geracao;
    const grupo = await resolver(T, {
      GLTFLoader,
      assunto: texto,
      material,
      cache: opcoes.cache,
      buscarRemoto: opcoes.buscarRemoto,
      aoRegistrar: opcoes.aoRegistrar,
    });
    // Trocaram de assunto (ou fecharam a janela) enquanto isto carregava.
    if (!vivo || meu !== geracao) return;
    limpar();
    objeto = grupo;
    suporte.add(grupo);
    if (opcoes.aoMontar) opcoes.aoMontar(grupo.userData || {});
  }

  /* ------------------------------------------------------------- controles */

  let largura = 0;
  /* Ler `clientWidth` dentro do laço força recálculo de layout a cada quadro,
     por janela aberta. O observador entrega o número já calculado. */
  const pararTamanho = observarTamanho(canvas, (l, a) => {
    largura = l;
    renderer.setSize(l, a, false);
    camera.aspect = l / a;
    camera.updateProjectionMatrix();
  });

  /* Janela rolada para fora da vista continuava desenhando a 60 fps. Aqui não
     se trata de aparar gordura: é deixar de pagar inteiro por algo que
     ninguém está olhando. */
  let visivel = true;
  const pararVisibilidade = observarVisibilidade(canvas, (v) => { visivel = v; });

  let arrastando = null;
  const aoPressionar = (e) => { arrastando = { x: e.clientX, y: e.clientY, th: camadas.tth, ph: camadas.tph }; };
  const aoMover = (e) => {
    if (!arrastando) return;
    camadas.tth = arrastando.th + (e.clientX - arrastando.x) * 0.008;
    camadas.tph = Math.max(0.3, Math.min(2.85, arrastando.ph - (e.clientY - arrastando.y) * 0.006));
  };
  const aoSoltar = () => { arrastando = null; };
  const aoRodar = (e) => {
    e.preventDefault();
    camadas.td = Math.max(1.9, Math.min(7, camadas.td + (e.deltaY > 0 ? 0.35 : -0.35)));
  };

  canvas.addEventListener('pointerdown', aoPressionar);
  window.addEventListener('pointermove', aoMover);
  window.addEventListener('pointerup', aoSoltar);
  canvas.addEventListener('wheel', aoRodar, { passive: false });

  /* ------------------------------------------------------------------ laço */

  (function laco(agora) {
    if (!vivo) return;
    raf = requestAnimationFrame(laco);
    if (!largura || !visivel) return;
    if (!ritmo(agora || performance.now())) return;
    const delta = relogio.getDelta();
    const t = relogio.elapsedTime;

    material.update(delta);

    camadas.th += (camadas.tth - camadas.th) * 0.09;
    camadas.ph += (camadas.tph - camadas.ph) * 0.09;
    camadas.d += (camadas.td - camadas.d) * 0.09;
    const orbita = camadas.th + t * 0.12;      // rotação lenta, para dar volume
    camera.position.set(
      camadas.d * Math.sin(camadas.ph) * Math.sin(orbita),
      camadas.d * Math.cos(camadas.ph),
      camadas.d * Math.sin(camadas.ph) * Math.cos(orbita),
    );
    camera.lookAt(0, 0, 0);

    if (objeto) {
      animarCatalogo(objeto, t, delta);
      // Flutuação: um holograma parado no ar parece um adesivo.
      suporte.position.y = Math.sin(t * 0.9) * 0.035;
    }
    renderer.render(cena, camera);
  })();

  montar(assunto);

  return {
    setSubject(texto) { montar(texto); },
    setMode(md) {
      if (md === atual) return;
      atual = md;
      material.setColor(CORES[md] || CORES.jarvis);
    },
    get info() { return objeto?.userData || {}; },
    dispose() {
      vivo = false;
      cancelAnimationFrame(raf);
      canvas.removeEventListener('pointerdown', aoPressionar);
      window.removeEventListener('pointermove', aoMover);
      window.removeEventListener('pointerup', aoSoltar);
      canvas.removeEventListener('wheel', aoRodar);
      pararTamanho();
      pararVisibilidade();
      limpar();
      material.dispose();
      // Devolve o contexto WebGL na hora: esperar o coletor de lixo é o que
      // estoura o teto de contextos do navegador ao abrir e fechar janelas.
      renderer.dispose();
      renderer.forceContextLoss?.();
    },
  };
}
