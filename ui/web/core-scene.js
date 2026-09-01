/* J.A.R.V.I.S. / U.L.T.R.O.N. core orb — framework-agnostic Three.js scene.
   Portable to Next.js as coreScene.ts: createCoreScene(THREE, canvas) -> handle.
   Owns its renderer, camera orbit, palette morph and a 2-pass bloom composite. */

import { criarGovernador, observarTamanho } from './perf.js';

export function createCoreScene(T, canvas, opcoes = {}) {
  /* `antialias` fica FALSO de propósito, e isso não piora a imagem.
     A cena inteira é desenhada para `rtScene`, um alvo de render sem
     multisampling; o framebuffer padrão só recebe um quadrado de tela cheia
     com essa textura colada. Pedir MSAA aqui aloca um buffer multiamostrado
     para antisserrilhar as duas bordas de um retângulo — custo de banda de
     memória por absolutamente nada. Numa GPU integrada, banda É o gargalo. */
  const renderer = new T.WebGLRenderer({ canvas, alpha: true, antialias: false, powerPreference: 'high-performance' });

  /* O governador pode baixar o nível sozinho, e aí os alvos de bloom e a
     densidade de pixel precisam ser refeitos. `reaplicarQualidade` só existe
     mais abaixo (depende do renderer montado), então o gancho é indireto. */
  let aoTrocarQualidade = () => {};
  const governador = opcoes.governador
    || criarGovernador({ aoMudar: () => aoTrocarQualidade() });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, governador.perfil.dpr));
  renderer.autoClear = false;
  const ritmo = governador.criarRitmo();

  /* Perda de contexto WebGL.
   *
   * O navegador mata contextos quando falta memória de vídeo — e aqui há um
   * por cena: o núcleo mais cada janela holográfica aberta. Numa máquina
   * modesta com outras abas pesadas, isso acontece de verdade.
   *
   * Sem tratamento, o Chromium desenha o canvas morto como um RETÂNGULO
   * BRANCO com ícone de imagem quebrada, e ele fica assim para sempre. Foi
   * exatamente o que apareceu no meio da interface.
   *
   * `preventDefault()` é obrigatório e não é detalhe: sem ele o navegador
   * nunca dispara `webglcontextrestored`, e não há como voltar. */
  const aoPerderContexto = (evento) => {
    evento.preventDefault();
    cancelAnimationFrame(raf);
    // Esconder o canvas evita o retângulo branco. O emblema em CSS que fica
    // atrás continua desenhando — a interface degrada em vez de quebrar.
    canvas.style.visibility = 'hidden';
    console.warn('[james] Contexto WebGL do núcleo perdido.');
    opcoes.aoPerderContexto?.();
  };
  const aoVoltarContexto = () => {
    canvas.style.visibility = '';
    console.info('[james] Contexto WebGL restaurado.');
    opcoes.aoRestaurarContexto?.();
  };
  canvas.addEventListener('webglcontextlost', aoPerderContexto, false);
  canvas.addEventListener('webglcontextrestored', aoVoltarContexto, false);
  const scene = new T.Scene();
  const camera = new T.PerspectiveCamera(38, 1, 0.1, 200);
  const root = new T.Group();
  scene.add(root);

  const C = (h) => new T.Color(h);
  const PAL = {
    j: { arc: C(0x2ff0ff), wire: C(0x1f8fc4), dot: C(0x8fe9ff), nuc: C(0x9beeff), dust: C(0x7cf6ff) },
    u: { arc: C(0xffa317), wire: C(0xff5f00), dot: C(0xffc25c), nuc: C(0xffd15e), dust: C(0xff8a2a) }
  };
  const tmp = new T.Color();
  const addMat = (color, opacity) => new T.MeshBasicMaterial({ color, transparent: true, opacity, blending: T.AdditiveBlending, depthWrite: false });
  const lineMat = (color, opacity) => new T.LineBasicMaterial({ color, transparent: true, opacity, blending: T.AdditiveBlending, depthWrite: false });

  /* ---- bright meridian bands (the three heavy glowing arcs) ---- */
  const arcMats = [];
  const arcs = new T.Group(); root.add(arcs);
  [[0, 0, 0], [0, Math.PI / 2, 0], [Math.PI / 2, 0, 0]].forEach((r, i) => {
    const m = addMat(0xffffff, i === 2 ? 0.5 : 0.78);
    const band = new T.Mesh(new T.TorusGeometry(1, i === 2 ? 0.008 : 0.013, 6, 300), m);
    band.rotation.set(r[0], r[1], r[2]);
    arcMats.push(m); arcs.add(band);
    const halo = new T.Mesh(new T.TorusGeometry(1, i === 2 ? 0.028 : 0.046, 6, 180), addMat(0xffffff, 0.07));
    halo.rotation.set(r[0], r[1], r[2]);
    arcMats.push(halo.material); arcs.add(halo);
  });

  /* ---- wire shells: dense latitude bands + sparse meridians ---- */
  const wireMats = [];
  const shell = new T.Group(); root.add(shell);
  const circle = (radius, y, seg) => {
    const p = [];
    for (let i = 0; i <= seg; i++) { const a = (i / seg) * Math.PI * 2; p.push(new T.Vector3(Math.cos(a) * radius, y, Math.sin(a) * radius)); }
    return new T.BufferGeometry().setFromPoints(p);
  };
  for (let i = 1; i < 40; i++) {
    const y = -1 + 2 * (i / 40), rr = Math.sqrt(Math.max(0, 1 - y * y)) * 0.995;
    const m = lineMat(0xffffff, 0.07 + 0.09 * rr);
    wireMats.push(m);
    const l = new T.Line(circle(rr, y, 72), m);
    l.userData.spin = (i % 2 ? 1 : -1) * (0.02 + (i % 5) * 0.012);
    shell.add(l);
  }
  for (let m2 = 0; m2 < 14; m2++) {
    const pts = [], a = (m2 / 14) * Math.PI;
    for (let s = 0; s <= 60; s++) { const p = -Math.PI / 2 + Math.PI * (s / 60); pts.push(new T.Vector3(Math.cos(p) * Math.cos(a), Math.sin(p), Math.cos(p) * Math.sin(a))); }
    const m = lineMat(0xffffff, 0.055);
    wireMats.push(m);
    shell.add(new T.Line(new T.BufferGeometry().setFromPoints(pts), m));
  }

  /* ---- inner data field (glyph-dense point cloud) ---- */
  const N = 2100;
  const pos = new Float32Array(N * 3), sd = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
    const r = 0.30 + Math.pow(Math.random(), 0.55) * 0.68;
    pos[i * 3] = r * Math.sin(ph) * Math.cos(th); pos[i * 3 + 1] = r * Math.cos(ph); pos[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
    sd[i] = Math.random();
  }
  const fieldGeo = new T.BufferGeometry();
  fieldGeo.setAttribute('position', new T.BufferAttribute(pos, 3));
  fieldGeo.setAttribute('sd', new T.BufferAttribute(sd, 1));
  const fieldMat = new T.ShaderMaterial({
    uniforms: { t: { value: 0 }, chaos: { value: 0 }, inten: { value: 1 }, fade: { value: 1 }, col: { value: new T.Color(0xdcfbff) }, hot: { value: new T.Color(0xffffff) } },
    transparent: true, blending: T.AdditiveBlending, depthWrite: false,
    vertexShader: [
      'attribute float sd; uniform float t,chaos,inten; varying float vI;',
      'void main(){ vec3 d=normalize(position); float r=length(position);',
      ' float turb=sin(t*1.25+sd*31.0)*0.022+sin(t*0.52+r*6.5+sd*13.0)*0.035;',
      ' vec3 p=d*(r+turb*(0.5+chaos*3.4)*inten);',
      ' float a=t*(0.05+chaos*0.12)+sd*0.25; mat2 rot=mat2(cos(a),-sin(a),sin(a),cos(a)); p.xz=rot*p.xz;',
      ' vI=0.18+0.82*pow(abs(sin(t*(1.1+sd*2.6)+sd*34.0)),2.0);',
      ' vec4 mv=modelViewMatrix*vec4(p,1.0);',
      ' gl_PointSize=clamp((0.8+sd*1.5)*(1.0+chaos*0.5)*(100.0/max(0.1,-mv.z)),1.0,7.0);',
      ' gl_Position=projectionMatrix*mv; }'
    ].join('\n'),
    fragmentShader: [
      'uniform vec3 col,hot; uniform float fade; varying float vI;',
      'void main(){ vec2 c=gl_PointCoord-0.5; float d=length(c); if(d>0.5) discard;',
      ' float a=smoothstep(0.5,0.0,d)*vI*0.5*fade;',
      ' gl_FragColor=vec4(mix(col,hot,pow(vI,5.0)),a); }'
    ].join('\n')
  });
  const field = new T.Points(fieldGeo, fieldMat);
  root.add(field);

  /* ---- nucleus ---- */
  const nucleus = new T.Group(); root.add(nucleus);
  const nucCore = new T.Mesh(new T.SphereGeometry(0.115, 26, 18), addMat(0xffffff, 0.6));
  const nucGlow = new T.Mesh(new T.SphereGeometry(0.28, 22, 16), addMat(0xffffff, 0.22));
  const nucHalo = new T.Mesh(new T.SphereGeometry(0.62, 20, 14), addMat(0xffffff, 0.07));
  nucleus.add(nucCore, nucGlow, nucHalo);
  const nucMats = [nucCore.material, nucGlow.material, nucHalo.material];

  /* ---- scan latitude ring ---- */
  const scanMat = lineMat(0xffffff, 0.35);
  const scan = new T.Line(circle(1, 0, 90), scanMat);
  root.add(scan);

  /* ---- ambient dust ---- */
  const D = 700, dp = new Float32Array(D * 3);
  for (let i = 0; i < D; i++) {
    const th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1), r = 1.8 + Math.random() * 6;
    dp[i * 3] = r * Math.sin(ph) * Math.cos(th); dp[i * 3 + 1] = r * Math.cos(ph) * 0.6; dp[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
  }
  const dustGeo = new T.BufferGeometry();
  dustGeo.setAttribute('position', new T.BufferAttribute(dp, 3));
  const dustMat = new T.PointsMaterial({ color: 0xffffff, size: 0.05, transparent: true, opacity: 0.35, blending: T.AdditiveBlending, depthWrite: false });
  const dust = new T.Points(dustGeo, dustMat);
  scene.add(dust);

  /* ---- ultron-only debris shards ---- */
  const shards = new T.Group(); shards.visible = false; root.add(shards);
  const shardMats = [];
  for (let i = 0; i < 40; i++) {
    const m = addMat(0xffab1f, 0.3);
    const f = new T.Mesh(new T.TetrahedronGeometry(0.02 + Math.random() * 0.045), m);
    f.userData = { r: 1.2 + Math.random() * 0.85, s: 0.15 + Math.random() * 0.6, o: Math.random() * 6.28 };
    shardMats.push(m); shards.add(f);
  }

  /* ================= bloom composite ================= */
  const rtOpt = { minFilter: T.LinearFilter, magFilter: T.LinearFilter, format: T.RGBAFormat };
  const rtScene = new T.WebGLRenderTarget(2, 2, rtOpt);
  const rtA = new T.WebGLRenderTarget(2, 2, rtOpt);
  const rtB = new T.WebGLRenderTarget(2, 2, rtOpt);
  const VS = 'varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.0,1.0); }';
  const quadScene = new T.Scene(), quadCam = new T.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const quad = new T.Mesh(new T.PlaneGeometry(2, 2));
  quadScene.add(quad);
  const brightMat = new T.ShaderMaterial({
    uniforms: { tex: { value: null }, thr: { value: 0.44 } }, vertexShader: VS,
    fragmentShader: 'varying vec2 vUv; uniform sampler2D tex; uniform float thr; void main(){ vec4 c=texture2D(tex,vUv); float l=max(max(c.r,c.g),c.b); gl_FragColor=c*smoothstep(thr,thr+0.28,l); }'
  });
  const blurFrag = 'varying vec2 vUv; uniform sampler2D tex; uniform vec2 res,dir; void main(){ vec2 o=dir/res; vec4 s=texture2D(tex,vUv)*0.2270; s+=(texture2D(tex,vUv+o*1.3846)+texture2D(tex,vUv-o*1.3846))*0.3162; s+=(texture2D(tex,vUv+o*3.2308)+texture2D(tex,vUv-o*3.2308))*0.0702; gl_FragColor=s; }';
  const blurH = new T.ShaderMaterial({ uniforms: { tex: { value: null }, res: { value: new T.Vector2(1, 1) }, dir: { value: new T.Vector2(1, 0) } }, vertexShader: VS, fragmentShader: blurFrag });
  const blurV = new T.ShaderMaterial({ uniforms: { tex: { value: null }, res: { value: new T.Vector2(1, 1) }, dir: { value: new T.Vector2(0, 1) } }, vertexShader: VS, fragmentShader: blurFrag });
  const compMat = new T.ShaderMaterial({
    uniforms: { sceneT: { value: null }, bloomT: { value: null }, str: { value: 1.15 } }, vertexShader: VS, transparent: true, depthTest: false, depthWrite: false,
    fragmentShader: 'varying vec2 vUv; uniform sampler2D sceneT,bloomT; uniform float str; void main(){ vec4 s=texture2D(sceneT,vUv); vec4 b=texture2D(bloomT,vUv); vec3 c=s.rgb+b.rgb*str; float a=clamp(s.a+max(max(b.r,b.g),b.b)*str*0.85,0.0,1.0); gl_FragColor=vec4(c,a); }'
  });

  /* ================= orbit ================= */
  const cam = { th: 0.3, ph: 1.42, d: 4.35, tth: 0.3, tph: 1.42, td: 4.35 };
  let drag = null;
  const onDown = (e) => { drag = { x: e.clientX, y: e.clientY, th: cam.tth, ph: cam.tph }; };
  const onMove = (e) => {
    if (!drag) return;
    cam.tth = drag.th + (e.clientX - drag.x) * 0.006;
    cam.tph = Math.max(0.35, Math.min(2.78, drag.ph - (e.clientY - drag.y) * 0.005));
  };
  const onUp = () => { drag = null; };
  const onWheel = (e) => { cam.td = Math.max(2.6, Math.min(8.5, cam.td + (e.deltaY > 0 ? 0.32 : -0.32))); };
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('wheel', onWheel, { passive: true });
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);

  /* ================= loop ================= */
  let w = 0, h = 0, mix = 0, targetMix = 0, inten = 1, raf = 0, bloomBase = 0.95;
  const clock = new T.Clock();

  const aplicarTamanho = (cw, ch) => {
    w = cw; h = ch;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    const s = renderer.getDrawingBufferSize(new T.Vector2());
    rtScene.setSize(s.x, s.y);
    /* O bloom é borrão: resolução plena nele é dinheiro jogado fora. A escala
       vem do perfil de qualidade — 1/2 na alta, 1/4 quando a máquina não
       segura, o que corta 75% dos pixels dos quatro passes de uma vez. */
    const div = governador.perfil.escalaBloom;
    const hw = Math.max(2, Math.round(s.x / div)), hh = Math.max(2, Math.round(s.y / div));
    rtA.setSize(hw, hh); rtB.setSize(hw, hh);
    blurH.uniforms.res.value.set(hw, hh); blurV.uniforms.res.value.set(hw, hh);
  };

  /* Antes isto era `resize()` chamado a cada quadro, lendo `clientWidth` — o
     que força o navegador a recalcular o layout antes de responder, 60 vezes
     por segundo, para descobrir um número que só muda quando a janela muda. */
  const pararDeObservar = observarTamanho(canvas, aplicarTamanho);

  /* Quando a qualidade cai, o tamanho dos alvos de bloom e a densidade de
     pixel precisam ser refeitos — senão o nível novo não vale para nada. */
  const reaplicarQualidade = () => {
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, governador.perfil.dpr));
    if (w && h) aplicarTamanho(w, h);
  };
  aoTrocarQualidade = reaplicarQualidade;

  const paint = () => {
    const p = PAL.j, u = PAL.u;
    arcMats.forEach(m => m.color.copy(tmp.lerpColors(p.arc, u.arc, mix)));
    wireMats.forEach(m => m.color.copy(tmp.lerpColors(p.wire, u.wire, mix)));
    fieldMat.uniforms.col.value.copy(tmp.lerpColors(p.dot, u.dot, mix));
    fieldMat.uniforms.hot.value.copy(tmp.lerpColors(p.nuc, u.nuc, mix));
    nucMats.forEach(m => m.color.copy(tmp.lerpColors(p.nuc, u.nuc, mix)));
    scanMat.color.copy(tmp.lerpColors(p.dot, u.dot, mix));
    dustMat.color.copy(tmp.lerpColors(p.dust, u.dust, mix));
  };
  paint();

  (function loop(agora) {
    raf = requestAnimationFrame(loop);
    if (!w || !h) return;
    if (!ritmo(agora || performance.now())) return;

    const t = clock.getElapsedTime();
    const antes = mix;
    mix += (targetMix - mix) * 0.08;
    /* `paint()` reescreve a cor de todos os materiais. Enquanto o mix está em
       trânsito isso é necessário; depois que ele assenta, é o mesmo trabalho
       produzindo o mesmo resultado, para sempre. */
    if (Math.abs(mix - antes) > 1e-4) paint();

    cam.th += (cam.tth - cam.th) * 0.08; cam.ph += (cam.tph - cam.ph) * 0.08; cam.d += (cam.td - cam.d) * 0.08;
    const orbit = cam.th + t * (0.03 + mix * 0.05);
    camera.position.set(cam.d * Math.sin(cam.ph) * Math.sin(orbit), cam.d * Math.cos(cam.ph), cam.d * Math.sin(cam.ph) * Math.cos(orbit));
    camera.lookAt(0, 0, 0);

    const breathe = 1 + Math.sin(t * (0.9 + mix * 1.4)) * (0.012 + mix * 0.016);
    root.scale.setScalar(breathe * (1 + mix * 0.04));
    root.rotation.y = t * (0.05 + mix * 0.1);
    root.rotation.z = Math.sin(t * 0.2) * 0.05 * (1 + mix * 2);

    arcs.rotation.y = -t * (0.06 + mix * 0.16);
    arcs.rotation.x = Math.sin(t * 0.27) * (0.08 + mix * 0.22);
    shell.children.forEach(l => { l.rotation.y += (l.userData.spin || 0.02) * (0.008 + mix * 0.02); });

    fieldMat.uniforms.t.value = t;
    fieldMat.uniforms.chaos.value = mix;
    fieldMat.uniforms.inten.value = inten;
    /* J.A.R.V.I.S. mode: the DOM ring emblem is the face — the volumetric shell
       retreats to a faint inner data sphere behind it. Ultron takes it over fully. */
    const structural = mix > 0.12;
    arcs.visible = structural; shell.visible = structural; scan.visible = structural;
    fieldMat.uniforms.fade.value = 0.7 + mix * 0.3;
    field.scale.setScalar(0.5 + mix * 0.5);
    dustMat.opacity = 0.16 + mix * 0.24;

    const beat = 0.9 + 0.1 * Math.sin(t * (2.6 + mix * 3)) + mix * 0.07 * Math.sin(t * 9.3);
    nucleus.scale.setScalar(beat * (1 + mix * 0.35) * inten * (0.5 + 0.5 * mix));

    const sy = Math.sin(t * 0.42) * 0.97;
    scan.position.y = sy;
    scan.scale.setScalar(Math.sqrt(Math.max(0.001, 1 - sy * sy)));
    scanMat.opacity = 0.22 + 0.3 * Math.abs(Math.cos(t * 0.42));

    dust.rotation.y = t * (0.01 + mix * 0.02) * (mix > 0.5 ? -1 : 1);
    shards.visible = mix > 0.05;
    if (shards.visible) {
      shards.children.forEach(f => {
        const d = f.userData;
        f.position.set(Math.cos(t * d.s + d.o) * d.r, Math.sin(t * d.s * 1.3 + d.o) * d.r * 0.5, Math.sin(t * d.s + d.o) * d.r);
        f.rotation.x += d.s * 0.02; f.rotation.y += d.s * 0.03;
        f.material.opacity = 0.3 * mix;
      });
    }

    compMat.uniforms.str.value = bloomBase * (1 + mix * 0.55) + (inten - 1) * 0.4;

    if (!governador.perfil.bloom) {
      /* Nível "baixa": um passe em vez de cinco. O brilho aditivo dos próprios
         materiais continua ali — o que se perde é o halo em volta, não a
         imagem. Numa GPU integrada essa é a diferença entre travar e rodar. */
      renderer.setRenderTarget(null); renderer.clear(); renderer.render(scene, camera);
    } else {
      renderer.setRenderTarget(rtScene); renderer.clear(); renderer.render(scene, camera);
      quad.material = brightMat; brightMat.uniforms.tex.value = rtScene.texture;
      renderer.setRenderTarget(rtA); renderer.clear(); renderer.render(quadScene, quadCam);
      quad.material = blurH; blurH.uniforms.tex.value = rtA.texture;
      renderer.setRenderTarget(rtB); renderer.clear(); renderer.render(quadScene, quadCam);
      quad.material = blurV; blurV.uniforms.tex.value = rtB.texture;
      renderer.setRenderTarget(rtA); renderer.clear(); renderer.render(quadScene, quadCam);
      quad.material = compMat; compMat.uniforms.sceneT.value = rtScene.texture; compMat.uniforms.bloomT.value = rtA.texture;
      renderer.setRenderTarget(null); renderer.clear(); renderer.render(quadScene, quadCam);
    }

  })();

  return {
    setMix(v) { targetMix = Math.max(0, Math.min(1, v)); },
    setIntensity(v) { inten = v; },
    setBloom(v) { bloomBase = v; },
    resetView() { cam.tth = 0.3; cam.tph = 1.42; cam.td = 4.35; },
    /* Deixa a interface mostrar e mudar o nível — a pessoa na frente da tela
       às vezes prefere trocar brilho por fluidez antes de o medidor decidir. */
    qualidade(nivel) {
      if (nivel === undefined) return governador.nivel;
      const mudou = governador.definir(nivel);
      if (mudou) reaplicarQualidade();
      return governador.nivel;
    },
    dispose() {
      cancelAnimationFrame(raf);
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('wheel', onWheel);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      pararDeObservar();
      canvas.removeEventListener('webglcontextlost', aoPerderContexto);
      canvas.removeEventListener('webglcontextrestored', aoVoltarContexto);
      scene.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
      [rtScene, rtA, rtB].forEach(r => r.dispose());
      renderer.dispose();
    }
  };
}
