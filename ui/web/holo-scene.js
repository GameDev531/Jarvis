/* Hologram projection scene — one instance per floating HologramWindow.
   Portable to Next.js as holoScene.ts: createHoloScene(THREE, canvas, subject, mode). */

const norm = (s) => (s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();
const hash = (s) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return Math.abs(h); };

function geoFor(T, w) {
  const has = (...k) => k.some(x => w.includes(x));
  if (has('cubo', 'caixa', 'bloco')) return new T.BoxGeometry(2, 2, 2, 2, 2, 2);
  if (has('toro', 'anel', 'donut')) return new T.TorusGeometry(1.2, 0.4, 14, 42);
  if (has('no ', 'knot', 'nó')) return new T.TorusKnotGeometry(0.95, 0.28, 120, 18);
  if (has('cristal', 'diamante', 'gema')) return new T.OctahedronGeometry(1.5, 0);
  if (has('piramide', 'tetra')) return new T.TetrahedronGeometry(1.7);
  if (has('cilindro', 'tubo', 'motor', 'turbina', 'reator')) return new T.CylinderGeometry(0.9, 0.9, 2, 26, 3);
  if (has('capsula', 'drone')) return new T.CapsuleGeometry(0.7, 1.1, 6, 18);
  if (has('cone')) return new T.ConeGeometry(1, 2.2, 24);
  if (has('esfera', 'bola', 'orbe')) return new T.SphereGeometry(1.45, 22, 16);
  if (has('icosaedro', 'rocha', 'asteroide')) return new T.IcosahedronGeometry(1.6, 1);
  const h = hash(w || 'x'), p = h % 4;
  if (p === 0) return new T.TorusKnotGeometry(0.95, 0.24, 110, 16, 2 + (h % 4), 3 + (h >> 3) % 5);
  if (p === 1) return new T.IcosahedronGeometry(1.6, (h >> 2) % 3);
  if (p === 2) return new T.CylinderGeometry(0.35 + (h % 7) / 10, 1.05, 2.1, 6 + (h % 10), 3);
  const pts = [];
  for (let i = 0; i < 14; i++) {
    const f = i / 13, r = 0.32 + Math.abs(Math.sin(f * Math.PI * (1 + (h % 5)) + (h % 17) / 17 * 6.283)) * 1.1;
    pts.push(new T.Vector2(r * (0.35 + 0.65 * Math.sin(f * Math.PI)), -1.25 + f * 2.5));
  }
  return new T.LatheGeometry(pts, 22);
}

function buildObject(T, subject, C, C2) {
  const w = norm(subject), g = new T.Group();
  const has = (...k) => k.some(x => w.includes(x));
  const line = (pts, op) => new T.Line(new T.BufferGeometry().setFromPoints(pts), new T.LineBasicMaterial({ color: C, transparent: true, opacity: op || 0.5, blending: T.AdditiveBlending, depthWrite: false }));
  const holoSet = (geo) => {
    g.add(new T.LineSegments(new T.WireframeGeometry(geo), new T.LineBasicMaterial({ color: C, transparent: true, opacity: 0.45, blending: T.AdditiveBlending, depthWrite: false })));
    g.add(new T.Mesh(geo, new T.MeshBasicMaterial({ color: C, transparent: true, opacity: 0.06, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide })));
    g.add(new T.Points(geo, new T.PointsMaterial({ color: C2, size: 0.042, transparent: true, opacity: 0.85, blending: T.AdditiveBlending, depthWrite: false })));
  };

  if (has('terra', 'planeta', 'globo', 'mundo', 'earth', 'geo', 'mapa')) {
    const r = 1.4;
    for (let i = 1; i < 12; i++) {
      const y = -r + 2 * r * (i / 12), rr = Math.sqrt(Math.max(0, r * r - y * y)), pts = [];
      for (let a = 0; a <= 48; a++) { const t = a / 48 * 6.283; pts.push(new T.Vector3(Math.cos(t) * rr, y, Math.sin(t) * rr)); }
      g.add(line(pts, i === 6 ? 0.75 : 0.28));
    }
    for (let m = 0; m < 12; m++) {
      const pts = [], a = m / 12 * Math.PI;
      for (let s = 0; s <= 40; s++) { const p = -Math.PI / 2 + Math.PI * (s / 40); pts.push(new T.Vector3(Math.cos(p) * Math.cos(a) * r, Math.sin(p) * r, Math.cos(p) * Math.sin(a) * r)); }
      g.add(line(pts, 0.22));
    }
    const n = 800, pp = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const th = Math.random() * 6.283, ph = Math.acos(2 * Math.random() - 1), rr = r * 1.005;
      pp[i * 3] = rr * Math.sin(ph) * Math.cos(th); pp[i * 3 + 1] = rr * Math.cos(ph); pp[i * 3 + 2] = rr * Math.sin(ph) * Math.sin(th);
    }
    const pg = new T.BufferGeometry(); pg.setAttribute('position', new T.BufferAttribute(pp, 3));
    g.add(new T.Points(pg, new T.PointsMaterial({ color: C2, size: 0.035, transparent: true, opacity: 0.8, blending: T.AdditiveBlending, depthWrite: false })));
  } else if (has('satelite', 'satellite', 'sonda', 'comunica', 'canal')) {
    holoSet(new T.BoxGeometry(0.75, 0.55, 0.9));
    [-1, 1].forEach(s => {
      const pl = new T.PlaneGeometry(1.5, 0.62, 6, 3);
      const p = new T.Mesh(pl, new T.MeshBasicMaterial({ color: C, transparent: true, opacity: 0.1, blending: T.AdditiveBlending, side: T.DoubleSide, depthWrite: false }));
      p.position.x = s * 1.2; g.add(p);
      const wf = new T.LineSegments(new T.WireframeGeometry(pl), new T.LineBasicMaterial({ color: C, transparent: true, opacity: 0.5, blending: T.AdditiveBlending, depthWrite: false }));
      wf.position.x = s * 1.2; g.add(wf);
    });
    const dish = new T.LineSegments(new T.WireframeGeometry(new T.SphereGeometry(0.36, 14, 8, 0, 6.283, 0, 1.1)), new T.LineBasicMaterial({ color: C2, transparent: true, opacity: 0.55, blending: T.AdditiveBlending, depthWrite: false }));
    dish.position.set(0, -0.55, 0); dish.rotation.x = Math.PI; g.add(dish);
    g.add(line([new T.Vector3(0, 0.3, 0), new T.Vector3(0, 1.1, 0)], 0.6));
  } else if (has('cerebro', 'brain', 'neural', 'mente', 'memoria', 'cognitiv')) {
    const n = 2200, pp = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const th = Math.random() * 6.283, ph = Math.acos(2 * Math.random() - 1);
      const lobe = Math.random() < 0.5 ? -1 : 1, wob = 1 + 0.18 * Math.sin(th * 7) + 0.12 * Math.sin(ph * 9);
      pp[i * 3] = (Math.sin(ph) * Math.cos(th) * 0.62 + lobe * 0.42) * wob;
      pp[i * 3 + 1] = Math.cos(ph) * 0.66 * wob;
      pp[i * 3 + 2] = Math.sin(ph) * Math.sin(th) * 0.82 * wob;
    }
    const pg = new T.BufferGeometry(); pg.setAttribute('position', new T.BufferAttribute(pp, 3));
    g.add(new T.Points(pg, new T.PointsMaterial({ color: C2, size: 0.028, transparent: true, opacity: 0.7, blending: T.AdditiveBlending, depthWrite: false })));
    for (let i = 0; i < 22; i++) {
      const pts = []; let v = new T.Vector3((Math.random() - 0.5) * 1.6, (Math.random() - 0.5) * 1.2, (Math.random() - 0.5) * 1.6);
      for (let s = 0; s < 9; s++) { pts.push(v.clone()); v = v.clone().add(new T.Vector3((Math.random() - 0.5) * 0.34, (Math.random() - 0.5) * 0.28, (Math.random() - 0.5) * 0.34)); }
      g.add(line(pts, 0.3));
    }
  } else if (has('rede', 'network', 'trafego', 'grafo', 'nos', 'dados', 'analis')) {
    const N = 46, nodes = [];
    for (let i = 0; i < N; i++) {
      const th = Math.random() * 6.283, ph = Math.acos(2 * Math.random() - 1), r = 0.5 + Math.random() * 1.1;
      nodes.push(new T.Vector3(r * Math.sin(ph) * Math.cos(th), r * Math.cos(ph), r * Math.sin(ph) * Math.sin(th)));
    }
    const pp = new Float32Array(N * 3); nodes.forEach((v, i) => { pp[i * 3] = v.x; pp[i * 3 + 1] = v.y; pp[i * 3 + 2] = v.z; });
    const pg = new T.BufferGeometry(); pg.setAttribute('position', new T.BufferAttribute(pp, 3));
    g.add(new T.Points(pg, new T.PointsMaterial({ color: C2, size: 0.09, transparent: true, opacity: 0.95, blending: T.AdditiveBlending, depthWrite: false })));
    for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) if (nodes[i].distanceTo(nodes[j]) < 0.72) g.add(line([nodes[i], nodes[j]], 0.28));
  } else if (has('dna', 'helice', 'genoma')) {
    const mat = new T.MeshBasicMaterial({ color: C, transparent: true, opacity: 0.85, blending: T.AdditiveBlending, depthWrite: false }), sg = new T.SphereGeometry(0.07, 8, 6);
    for (let i = 0; i < 60; i++) {
      const a = i * 0.32, y = -1.4 + i * 0.047;
      const m1 = new T.Mesh(sg, mat), m2 = new T.Mesh(sg, mat);
      m1.position.set(Math.cos(a) * 0.72, y, Math.sin(a) * 0.72);
      m2.position.set(Math.cos(a + Math.PI) * 0.72, y, Math.sin(a + Math.PI) * 0.72);
      g.add(m1, m2);
      if (i % 4 === 0) g.add(line([m1.position.clone(), m2.position.clone()], 0.35));
    }
  } else if (has('nave', 'foguete', 'missil', 'aeronave')) {
    holoSet(new T.ConeGeometry(0.55, 2.1, 18));
    [0, 2.09, 4.18].forEach(a => {
      const pl = new T.PlaneGeometry(0.7, 0.6);
      const f = new T.Mesh(pl, new T.MeshBasicMaterial({ color: C, transparent: true, opacity: 0.12, blending: T.AdditiveBlending, side: T.DoubleSide, depthWrite: false }));
      f.position.set(Math.cos(a) * 0.42, -0.85, Math.sin(a) * 0.42); f.rotation.y = -a; g.add(f);
      const w2 = new T.LineSegments(new T.WireframeGeometry(pl), new T.LineBasicMaterial({ color: C2, transparent: true, opacity: 0.5, blending: T.AdditiveBlending, depthWrite: false }));
      w2.position.copy(f.position); w2.rotation.y = -a; g.add(w2);
    });
  } else if (has('solar', 'orbita', 'orbital', 'atmosf', 'clima')) {
    g.add(new T.Mesh(new T.SphereGeometry(0.34, 20, 14), new T.MeshBasicMaterial({ color: C2, transparent: true, opacity: 0.9, blending: T.AdditiveBlending, depthWrite: false })));
    for (let i = 1; i <= 5; i++) {
      const r = 0.55 + i * 0.32, pts = [];
      for (let a = 0; a <= 72; a++) { const t = a / 72 * 6.283; pts.push(new T.Vector3(Math.cos(t) * r, 0, Math.sin(t) * r * 0.94)); }
      g.add(line(pts, 0.3));
      const pl = new T.Mesh(new T.SphereGeometry(0.06 + i * 0.015, 12, 9), new T.MeshBasicMaterial({ color: C, transparent: true, opacity: 0.9, blending: T.AdditiveBlending, depthWrite: false }));
      const a = i * 1.7; pl.position.set(Math.cos(a) * r, 0, Math.sin(a) * r * 0.94); g.add(pl);
    }
  } else {
    holoSet(geoFor(T, w));
  }

  const base = new T.Mesh(new T.RingGeometry(1.72, 1.8, 72), new T.MeshBasicMaterial({ color: C, transparent: true, opacity: 0.3, blending: T.AdditiveBlending, side: T.DoubleSide, depthWrite: false }));
  base.rotation.x = Math.PI / 2; base.position.y = -1.7; g.add(base);
  return g;
}

export function createHoloScene(T, canvas, subject, mode) {
  const renderer = new T.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setPixelRatio(1);
  const scene = new T.Scene();
  const camera = new T.PerspectiveCamera(42, 1, 0.1, 60);
  const holder = new T.Group();
  scene.add(holder);

  const COL = { j: [0x00e5ff, 0xd8f8ff], u: [0xff9c22, 0xffe2a1] };
  let obj = null, cur = null;
  const build = (subj, md) => {
    if (obj) { holder.remove(obj); obj.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); }); }
    const c = COL[md === 'ultron' ? 'u' : 'j'];
    obj = buildObject(T, subj, c[0], c[1]);
    obj.scale.setScalar(0.9);
    holder.add(obj);
    cur = md;
    scanMat.color.set(c[1]);
  };

  const scanMat = new T.MeshBasicMaterial({ color: 0xd8f8ff, transparent: true, opacity: 0.1, blending: T.AdditiveBlending, side: T.DoubleSide, depthWrite: false });
  const scan = new T.Mesh(new T.RingGeometry(0.04, 1.5, 40), scanMat);
  scan.rotation.x = Math.PI / 2;
  scene.add(scan);
  build(subject, mode);

  const cam = { th: 0.4, ph: 1.35, d: 5.4, tth: 0.4, tph: 1.35, td: 5.4 };
  let drag = null;
  const onDown = (e) => { drag = { x: e.clientX, y: e.clientY, th: cam.tth, ph: cam.tph }; e.stopPropagation(); };
  const onMove = (e) => { if (!drag) return; cam.tth = drag.th + (e.clientX - drag.x) * 0.008; cam.tph = Math.max(0.4, Math.min(2.7, drag.ph - (e.clientY - drag.y) * 0.006)); };
  const onUp = () => { drag = null; };
  const onWheel = (e) => { cam.td = Math.max(3, Math.min(9, cam.td + (e.deltaY > 0 ? 0.4 : -0.4))); };
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('wheel', onWheel, { passive: true });
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);

  let w = 0, h = 0, raf = 0;
  const clock = new T.Clock();
  const resize = () => {
    const cw = canvas.clientWidth, ch = canvas.clientHeight;
    if (!cw || !ch || (cw === w && ch === h)) return;
    w = cw; h = ch; renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  };

  (function loop() {
    raf = requestAnimationFrame(loop);
    resize();
    if (!w || !h) return;
    const t = clock.getElapsedTime();
    cam.th += (cam.tth - cam.th) * 0.09; cam.ph += (cam.tph - cam.ph) * 0.09; cam.d += (cam.td - cam.d) * 0.09;
    const a = cam.th + t * 0.16;
    camera.position.set(cam.d * Math.sin(cam.ph) * Math.sin(a), cam.d * Math.cos(cam.ph), cam.d * Math.sin(cam.ph) * Math.cos(a));
    camera.lookAt(0, 0, 0);
    holder.rotation.x = Math.sin(t * 0.32) * 0.09;
    scan.position.y = ((t * 0.6) % 3.2) - 1.6;
    renderer.render(scene, camera);
  })();

  return {
    setMode(md) { if (md !== cur) build(subject, md); },
    setSubject(subj, md) { subject = subj; build(subj, md || cur); },
    dispose() {
      cancelAnimationFrame(raf);
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('wheel', onWheel);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      scene.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
      renderer.dispose();
    }
  };
}
