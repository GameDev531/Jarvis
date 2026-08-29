/* Catálogo curado — geometria feita à mão para os assuntos mais pedidos.
 *
 * Por que gerar em vez de baixar, sendo que baixar parece mais fácil:
 *
 * Um holograma joga fora textura, cor e material — o shader lê silhueta e
 * topologia. Então uma malha fotogrametrada de 60 MB fica PIOR que uma malha
 * limpa de 200 KB: a triangulação irregular suja as scanlines e as normais
 * ruidosas mancham o fresnel. Geometria procedural é limpa por construção.
 *
 * E ela é grátis nos três sentidos que importam aqui: sem rede, sem chave,
 * sem licença. Um cérebro gerado por seno não tem autor a creditar.
 *
 * Cada gerador devolve um `THREE.Group` centrado na origem e cabendo mais ou
 * menos numa esfera de raio 1 — a cena se encarrega de enquadrar. Quem precisa
 * de linha usa `linhas()`, que não recebe o material holográfico: linha não
 * tem normal, então fresnel não se aplica.
 */

const TAU = Math.PI * 2;

/* ------------------------------------------------------------- utilitários */

function malha(T, geometria, material) {
  return new T.Mesh(geometria, material);
}

function linhas(T, pontos, cor, opacidade = 0.55) {
  const g = new T.BufferGeometry().setFromPoints(pontos);
  return new T.Line(g, new T.LineBasicMaterial({
    color: cor, transparent: true, opacity: opacidade,
    blending: T.AdditiveBlending, depthWrite: false,
  }));
}

/** Esfera deformada por uma função radial. É a base de metade do catálogo. */
function esferaDeformada(T, segmentos, raio) {
  const g = new T.SphereGeometry(1, segmentos, Math.round(segmentos * 0.6));
  const pos = g.attributes.position;
  const v = new T.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i).normalize();
    const r = raio(v.x, v.y, v.z);
    pos.setXYZ(i, v.x * r, v.y * r, v.z * r);
  }
  pos.needsUpdate = true;
  g.computeVertexNormals();
  return g;
}

/** Malha a partir de uma função (u,v) -> [x,y,z], com u e v em 0..1.
 *
 * Existe porque função radial sobre esfera não consegue produzir
 * descontinuidade — e formas como o coração são feitas de descontinuidade
 * (a fenda no topo, a ponta embaixo). Aqui a própria curva carrega isso. */
function superficieParametrica(T, segU, segV, fn) {
  const posicoes = [], indices = [];
  for (let i = 0; i <= segU; i++) {
    for (let j = 0; j <= segV; j++) {
      const [x, y, z] = fn(i / segU, j / segV);
      posicoes.push(x, y, z);
    }
  }
  for (let i = 0; i < segU; i++) {
    for (let j = 0; j < segV; j++) {
      const a = i * (segV + 1) + j;
      const b = a + segV + 1;
      indices.push(a, b, a + 1, b, b + 1, a + 1);
    }
  }
  const g = new T.BufferGeometry();
  g.setAttribute('position', new T.Float32BufferAttribute(posicoes, 3));
  g.setIndex(indices);
  g.computeVertexNormals();
  return g;
}

/* ------------------------------------------------------------- geradores */

const GERADORES = {
  /* Sulcos por senos cruzados + fissura longitudinal. Não é anatomia, é a
     leitura de "cérebro" em silhueta — que é tudo que um holograma precisa. */
  cerebro(T, mat, cor) {
    const g = new T.Group();
    const sulcos = (x, y, z) =>
      1
      + 0.055 * Math.sin(x * 9 + z * 5)
      + 0.05 * Math.sin(y * 11 - x * 4)
      + 0.04 * Math.sin(z * 13 + y * 6)
      - 0.10 * Math.exp(-Math.pow(x * 7, 2));   // a fissura entre hemisférios

    const hemisferio = (sinal) => {
      const m = malha(T, esferaDeformada(T, 48, sulcos), mat);
      m.scale.set(0.52, 0.62, 0.72);
      m.position.x = sinal * 0.30;
      return m;
    };
    g.add(hemisferio(1), hemisferio(-1));

    // Tronco encefálico: dá "para baixo" ao objeto e evita a leitura de "noz".
    const tronco = malha(T, new T.CylinderGeometry(0.10, 0.16, 0.5, 14, 2), mat);
    tronco.position.set(0, -0.62, 0.08);
    tronco.rotation.x = 0.3;
    g.add(tronco);
    return g;
  },

  /* Coração pela curva clássica, inflada na profundidade.
   *
   * A primeira tentativa foi deformar uma esfera por função radial, e saiu uma
   * gota: a fenda entre os lobos e a ponta de baixo são descontinuidades, e
   * função radial suave não produz descontinuidade. A curva paramétrica já tem
   * as duas embutidas — é o caminho certo para esta forma. */
  coracao(T, mat, cor) {
    const g = superficieParametrica(T, 90, 26, (u, v) => {
      const t = u * TAU;
      const s = v * 2 - 1;
      const hx = (16 * Math.pow(Math.sin(t), 3)) / 17;
      const hy = (13 * Math.cos(t) - 5 * Math.cos(2 * t)
        - 2 * Math.cos(3 * t) - Math.cos(4 * t)) / 17;
      // Expoente baixo mantém o contorno cheio quase até a borda e só então
      // fecha: é o que dá "inflado" em vez de "lente".
      const inflar = Math.pow(Math.max(0, 1 - s * s), 0.34);
      return [hx * inflar, hy * inflar, s * 0.46];
    });
    const grupo = new T.Group();
    const m = malha(T, g, mat);
    m.scale.setScalar(0.95);
    m.rotation.z = 0.12;
    grupo.add(m);
    return grupo;
  },

  /* Dupla hélice com pares de base. Provavelmente o assunto que mais ganha em
     ser procedural: uma hélice matemática é perfeita, uma escaneada é torta. */
  dna(T, mat, cor) {
    const g = new T.Group();
    const voltas = 3.2, altura = 2.0, passos = 150, raio = 0.34;
    const fita = (fase) => {
      const pontos = [];
      for (let i = 0; i <= passos; i++) {
        const t = i / passos, a = t * TAU * voltas + fase;
        pontos.push(new T.Vector3(Math.cos(a) * raio, (t - 0.5) * altura, Math.sin(a) * raio));
      }
      return malha(T, new T.TubeGeometry(new T.CatmullRomCurve3(pontos), 120, 0.035, 6, false), mat);
    };
    g.add(fita(0), fita(Math.PI));

    for (let i = 0; i <= 22; i++) {
      const t = i / 22, a = t * TAU * voltas;
      const y = (t - 0.5) * altura;
      const base = malha(T, new T.CylinderGeometry(0.018, 0.018, raio * 2, 6), mat);
      base.position.y = y;
      base.rotation.z = Math.PI / 2;
      base.rotation.y = -a;
      g.add(base);
    }
    return g;
  },

  terra(T, mat, cor) {
    const g = new T.Group();
    g.add(malha(T, new T.SphereGeometry(0.72, 40, 28), mat));

    // Grade de meridianos e paralelos: é o que faz ler "planeta" e não "bola".
    for (let i = 1; i < 8; i++) {
      const lat = -Math.PI / 2 + (i / 8) * Math.PI;
      const r = Math.cos(lat) * 0.73, y = Math.sin(lat) * 0.73;
      const pts = [];
      for (let k = 0; k <= 64; k++) {
        const a = (k / 64) * TAU;
        pts.push(new T.Vector3(Math.cos(a) * r, y, Math.sin(a) * r));
      }
      g.add(linhas(T, pts, cor, 0.35));
    }
    for (let m = 0; m < 12; m++) {
      const a = (m / 12) * Math.PI, pts = [];
      for (let k = 0; k <= 48; k++) {
        const p = -Math.PI / 2 + (k / 48) * Math.PI;
        pts.push(new T.Vector3(
          Math.cos(p) * Math.cos(a) * 0.73, Math.sin(p) * 0.73, Math.cos(p) * Math.sin(a) * 0.73,
        ));
      }
      g.add(linhas(T, pts, cor, 0.28));
    }

    const orbita = malha(T, new T.TorusGeometry(1.0, 0.006, 4, 90), mat);
    orbita.rotation.x = Math.PI / 2 - 0.35;
    g.add(orbita);
    return g;
  },

  foguete(T, mat, cor) {
    const g = new T.Group();
    const corpo = malha(T, new T.CylinderGeometry(0.24, 0.26, 1.25, 20, 3), mat);
    g.add(corpo);
    const bico = malha(T, new T.ConeGeometry(0.24, 0.55, 20), mat);
    bico.position.y = 0.9;
    g.add(bico);
    const bocal = malha(T, new T.CylinderGeometry(0.26, 0.17, 0.22, 16), mat);
    bocal.position.y = -0.73;
    g.add(bocal);
    for (let i = 0; i < 4; i++) {
      const aleta = malha(T, new T.BoxGeometry(0.03, 0.42, 0.30), mat);
      const a = (i / 4) * TAU;
      aleta.position.set(Math.cos(a) * 0.3, -0.48, Math.sin(a) * 0.3);
      aleta.rotation.y = -a;
      g.add(aleta);
    }
    return g;
  },

  atomo(T, mat, cor) {
    const g = new T.Group();
    const nucleo = malha(T, new T.IcosahedronGeometry(0.22, 1), mat);
    g.add(nucleo);
    const inclinacoes = [[0, 0, 0], [Math.PI / 3, 0, Math.PI / 3], [-Math.PI / 3, 0, -Math.PI / 3]];
    inclinacoes.forEach((rot, i) => {
      const orbita = malha(T, new T.TorusGeometry(0.78, 0.012, 5, 80), mat);
      orbita.rotation.set(rot[0], rot[1], rot[2]);
      g.add(orbita);
      const eletron = malha(T, new T.SphereGeometry(0.055, 10, 8), mat);
      eletron.userData.orbita = { raio: 0.78, rot, fase: i * 2.1, velocidade: 1.2 + i * 0.4 };
      g.add(eletron);
    });
    return g;
  },

  molecula(T, mat, cor) {
    const g = new T.Group();
    const atomos = [
      [0, 0, 0, 0.26], [0.72, 0.34, 0, 0.17], [-0.72, 0.34, 0, 0.17],
      [0, -0.62, 0.42, 0.17], [0, -0.30, -0.78, 0.15],
    ];
    for (const [x, y, z, r] of atomos) {
      const a = malha(T, new T.IcosahedronGeometry(r, 1), mat);
      a.position.set(x, y, z);
      g.add(a);
    }
    for (let i = 1; i < atomos.length; i++) {
      const [x, y, z] = atomos[i];
      const fim = new T.Vector3(x, y, z);
      const meio = fim.clone().multiplyScalar(0.5);
      const ligacao = malha(T, new T.CylinderGeometry(0.03, 0.03, fim.length(), 8), mat);
      ligacao.position.copy(meio);
      ligacao.quiver = null;
      ligacao.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), fim.clone().normalize());
      g.add(ligacao);
    }
    return g;
  },

  reator(T, mat, cor) {
    const g = new T.Group();
    [[0.62, 0.05], [0.46, 0.035], [0.30, 0.025]].forEach(([r, t], i) => {
      const anel = malha(T, new T.TorusGeometry(r, t, 8, 60), mat);
      anel.userData.giro = (i % 2 ? -1 : 1) * (0.4 + i * 0.25);
      g.add(anel);
    });
    g.add(malha(T, new T.SphereGeometry(0.17, 20, 16), mat));
    for (let i = 0; i < 10; i++) {
      const a = (i / 10) * TAU;
      const bobina = malha(T, new T.BoxGeometry(0.05, 0.05, 0.20), mat);
      bobina.position.set(Math.cos(a) * 0.46, Math.sin(a) * 0.46, 0);
      bobina.rotation.z = a;
      g.add(bobina);
    }
    return g;
  },

  satelite(T, mat, cor) {
    const g = new T.Group();
    g.add(malha(T, new T.BoxGeometry(0.58, 0.52, 0.52), mat));
    for (const lado of [-1, 1]) {
      // Painel com espessura de verdade: uma caixa de 2 cm some quando vista
      // de lado, e metade das voltas da órbita mostra exatamente esse ângulo.
      const painel = malha(T, new T.BoxGeometry(0.78, 0.06, 0.46), mat);
      painel.position.x = lado * 0.78;
      painel.rotation.x = 0.25;
      g.add(painel);
      const haste = malha(T, new T.CylinderGeometry(0.035, 0.035, 0.32, 8), mat);
      haste.rotation.z = Math.PI / 2;
      haste.position.x = lado * 0.4;
      g.add(haste);
    }
    const antena = malha(T, new T.SphereGeometry(0.3, 20, 14, 0, TAU, 0, Math.PI / 2.3), mat);
    antena.position.y = -0.45;
    antena.rotation.x = Math.PI;
    g.add(antena);
    return g;
  },

  drone(T, mat, cor) {
    const g = new T.Group();
    g.add(malha(T, new T.CapsuleGeometry(0.26, 0.28, 8, 18), mat));
    for (let i = 0; i < 4; i++) {
      const a = (i / 4) * TAU + Math.PI / 4;
      const x = Math.cos(a) * 0.52, z = Math.sin(a) * 0.52;
      const braco = malha(T, new T.CylinderGeometry(0.045, 0.045, 0.52, 8), mat);
      braco.position.set(x / 2, 0, z / 2);
      braco.quaternion.setFromUnitVectors(
        new T.Vector3(0, 1, 0), new T.Vector3(x, 0, z).normalize(),
      );
      g.add(braco);
      const rotor = malha(T, new T.TorusGeometry(0.22, 0.035, 8, 30), mat);
      rotor.position.set(x, 0.06, z);
      rotor.rotation.x = Math.PI / 2;
      rotor.userData.giro = 6;
      g.add(rotor);
    }
    return g;
  },

  pulmao(T, mat, cor) {
    const g = new T.Group();
    for (const lado of [-1, 1]) {
      const lobo = malha(T, esferaDeformada(T, 34, (x, y) =>
        1 - 0.30 * Math.max(0, -y) + 0.06 * Math.sin(y * 8)), mat);
      lobo.scale.set(0.34, 0.60, 0.30);
      lobo.position.set(lado * 0.34, -0.12, 0);
      lobo.rotation.z = lado * 0.13;
      g.add(lobo);
    }
    const traqueia = malha(T, new T.CylinderGeometry(0.075, 0.075, 0.52, 12), mat);
    traqueia.position.y = 0.55;
    g.add(traqueia);
    for (const lado of [-1, 1]) {
      const bronquio = malha(T, new T.CylinderGeometry(0.045, 0.045, 0.36, 8), mat);
      bronquio.position.set(lado * 0.16, 0.24, 0);
      bronquio.rotation.z = lado * 0.85;
      g.add(bronquio);
    }
    return g;
  },

  cristal(T, mat, cor) {
    const g = new T.Group();
    const nucleo = malha(T, new T.OctahedronGeometry(0.78, 0), mat);
    nucleo.scale.y = 1.45;
    g.add(nucleo);
    for (let i = 0; i < 5; i++) {
      const a = (i / 5) * TAU;
      const lasca = malha(T, new T.OctahedronGeometry(0.22, 0), mat);
      lasca.position.set(Math.cos(a) * 0.6, (i % 2 ? 0.3 : -0.3), Math.sin(a) * 0.6);
      lasca.scale.y = 1.6;
      lasca.rotation.set(a, a * 0.5, 0.3);
      g.add(lasca);
    }
    return g;
  },

  galaxia(T, mat, cor) {
    const g = new T.Group();
    const N = 2600, pos = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      const braco = i % 3, t = Math.pow(Math.random(), 0.55);
      const a = t * 5.2 + (braco / 3) * TAU + (Math.random() - 0.5) * 0.45;
      const r = t * 1.05;
      pos[i * 3] = Math.cos(a) * r + (Math.random() - 0.5) * 0.06;
      pos[i * 3 + 1] = (Math.random() - 0.5) * 0.10 * (1 - t * 0.7);
      pos[i * 3 + 2] = Math.sin(a) * r + (Math.random() - 0.5) * 0.06;
    }
    const geo = new T.BufferGeometry();
    geo.setAttribute('position', new T.BufferAttribute(pos, 3));
    g.add(new T.Points(geo, new T.PointsMaterial({
      color: cor, size: 0.022, transparent: true, opacity: 0.85,
      blending: T.AdditiveBlending, depthWrite: false,
    })));
    g.add(malha(T, new T.SphereGeometry(0.13, 18, 14), mat));
    return g;
  },

  cidade(T, mat, cor) {
    const g = new T.Group();
    for (let i = 0; i < 34; i++) {
      const x = (Math.random() - 0.5) * 1.7, z = (Math.random() - 0.5) * 1.7;
      const h = 0.18 + Math.pow(Math.random(), 2) * 1.0;
      const predio = malha(T, new T.BoxGeometry(0.13, h, 0.13), mat);
      predio.position.set(x, h / 2 - 0.45, z);
      g.add(predio);
    }
    const chao = [];
    for (let i = -4; i <= 4; i++) {
      chao.push(new T.Vector3(-1, -0.45, i * 0.25), new T.Vector3(1, -0.45, i * 0.25));
      chao.push(new T.Vector3(i * 0.25, -0.45, -1), new T.Vector3(i * 0.25, -0.45, 1));
    }
    const geo = new T.BufferGeometry().setFromPoints(chao);
    g.add(new T.LineSegments(geo, new T.LineBasicMaterial({
      color: cor, transparent: true, opacity: 0.3,
      blending: T.AdditiveBlending, depthWrite: false,
    })));
    return g;
  },
};

/* Sinônimos: o usuário fala, não digita um identificador. As chaves são
   normalizadas (minúsculo, sem acento) antes de comparar. */
const SINONIMOS = {
  cerebro: ['cerebro', 'mente', 'neuro', 'neuronio', 'encefalo'],
  coracao: ['coracao', 'cardio', 'cardiaco'],
  dna: ['dna', 'adn', 'genoma', 'gene', 'helice', 'dupla helice'],
  terra: ['terra', 'planeta', 'mundo', 'globo', 'mapa mundi'],
  foguete: ['foguete', 'nave', 'espaconave', 'missil', 'lancador'],
  atomo: ['atomo', 'atomico', 'eletron', 'nuclear'],
  molecula: ['molecula', 'molecular', 'composto', 'quimica', 'agua'],
  reator: ['reator', 'arc reactor', 'motor', 'turbina', 'gerador', 'nucleo'],
  satelite: ['satelite', 'orbita', 'sonda', 'gps'],
  drone: ['drone', 'quadricoptero', 'helicoptero'],
  pulmao: ['pulmao', 'pulmoes', 'respiratorio', 'traqueia'],
  cristal: ['cristal', 'diamante', 'gema', 'joia', 'mineral'],
  galaxia: ['galaxia', 'universo', 'espaco', 'via lactea', 'estrelas', 'cosmos'],
  cidade: ['cidade', 'urbano', 'predios', 'metropole', 'skyline'],
};

const norm = (s) => (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').trim();

/** Nome canônico do assunto, ou null se o catálogo não cobre. */
export function resolverAssunto(texto) {
  const alvo = norm(texto);
  if (!alvo) return null;
  if (GERADORES[alvo]) return alvo;
  for (const [canonico, termos] of Object.entries(SINONIMOS)) {
    if (termos.some((t) => alvo === t || alvo.includes(t))) return canonico;
  }
  return null;
}

export function temNoCatalogo(texto) {
  return resolverAssunto(texto) !== null;
}

export const ASSUNTOS = Object.keys(GERADORES);

/** Monta o objeto. Devolve null quando o assunto não está no catálogo. */
export function criarDoCatalogo(T, texto, material, cor) {
  const nome = resolverAssunto(texto);
  if (!nome) return null;
  const grupo = GERADORES[nome](T, material, cor);
  grupo.userData.assunto = nome;
  grupo.userData.fonte = 'catalogo';
  return grupo;
}

/** Animações específicas: elétrons orbitando, anéis girando, rotores. */
export function animarCatalogo(grupo, t, delta) {
  grupo.traverse((o) => {
    if (o.userData.giro) o.rotation.z += o.userData.giro * delta;
    const orb = o.userData.orbita;
    if (orb) {
      const a = t * orb.velocidade + orb.fase;
      o.position.set(Math.cos(a) * orb.raio, 0, Math.sin(a) * orb.raio);
      o.position.applyEuler({ x: orb.rot[0], y: orb.rot[1], z: orb.rot[2], order: 'XYZ' });
    }
  });
}
