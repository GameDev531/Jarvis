/* A cascata: como uma palavra vira um objeto na tela.
 *
 *   "mostra um cérebro"
 *      ↓
 *   1. CATÁLOGO CURADO   gerador escrito à mão      0 ms · offline · sem licença
 *      ↓ não cobre
 *   2. CACHE LOCAL       GLB já baixado antes       disco · offline
 *      ↓ não tem
 *   3. REMOTO            Poly Pizza → baixa → cacheia   1x na vida
 *      ↓ falhou / sem rede
 *   4. GENÉRICO          primitiva pela palavra     nunca falha
 *
 * O catálogo vem antes do cache de propósito: se alguém escreveu um gerador
 * para "cérebro", é porque ele é melhor que uma malha qualquer baixada — mais
 * limpa para o shader e sem crédito a carregar. O cache existe para o que o
 * catálogo NÃO cobre, então na prática os dois quase não competem.
 *
 * O nível 4 é o que garante que a tela nunca fica vazia. Pedir "mostra um
 * ornitorrinco" sem internet devolve uma forma abstrata em vez de um erro —
 * e um assistente que responde com forma abstrata é melhor que um que trava.
 *
 * ## GLB remoto é dado não confiável
 *
 * Carregar um modelo baixado é rodar um parser em cima de arquivo de terceiro.
 * Um glTF pode declarar milhões de vértices, referenciar URIs externas, ou vir
 * com buffers absurdos. Daí os tetos em `LIMITES`: se o modelo passar de
 * qualquer um deles, ele é descartado e a cascata cai para o nível seguinte.
 * Recusar e desenhar outra coisa é sempre melhor que travar a aba.
 */

import { animarCatalogo, criarDoCatalogo, resolverAssunto } from './holo-catalog.js';

export const LIMITES = {
  bytes: 8 * 1024 * 1024,     // 8 MB: acima disso não é low-poly, é fotogrametria
  vertices: 250000,           // o suficiente para qualquer coisa limpa
  objetos: 400,               // malhas dentro de um GLB
  segundos: 15,               // teto de espera do download
};

const norm = (s) => (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').trim();

/** Nome de arquivo seguro para o cache. Nunca vira caminho. */
export function slug(texto) {
  return norm(texto).replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);
}

/* -------------------------------------------------------- nível 4: genérico */

/* Formas abstratas para o que ninguém previu. A escolha é determinística pela
   palavra (hash), não aleatória: pedir "ornitorrinco" duas vezes tem que dar a
   mesma coisa, senão o holograma parece instável em vez de desconhecido. */
function hash(texto) {
  let h = 2166136261;
  for (let i = 0; i < texto.length; i++) {
    h ^= texto.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h);
}

export function criarGenerico(T, texto, material) {
  const h = hash(norm(texto) || 'x');
  const grupo = new T.Group();
  const forma = h % 5;
  let geometria;
  if (forma === 0) geometria = new T.TorusKnotGeometry(0.62, 0.19, 120, 16, 2 + (h % 4), 3 + ((h >> 3) % 5));
  else if (forma === 1) geometria = new T.IcosahedronGeometry(0.85, 1 + ((h >> 2) % 2));
  else if (forma === 2) geometria = new T.TorusGeometry(0.7, 0.24, 12, 40);
  else if (forma === 3) geometria = new T.OctahedronGeometry(0.9, 1);
  else geometria = new T.CylinderGeometry(0.3 + (h % 6) / 14, 0.72, 1.3, 7 + (h % 8), 3);

  grupo.add(new T.Mesh(geometria, material));
  grupo.userData.fonte = 'generico';
  grupo.userData.assunto = norm(texto);
  return grupo;
}

/* --------------------------------------------------- níveis 2 e 3: modelos */

function medir(raiz) {
  let vertices = 0;
  let objetos = 0;
  raiz.traverse((o) => {
    if (!o.isMesh) return;
    objetos += 1;
    const pos = o.geometry?.attributes?.position;
    if (pos) vertices += pos.count;
  });
  return { vertices, objetos };
}

/** Troca todo material da árvore pelo holográfico e normaliza a escala. */
function preparar(T, raiz, material) {
  raiz.traverse((o) => {
    if (!o.isMesh) return;
    // O material original é descartado inteiro: textura, cor e brilho não
    // sobrevivem a um holograma de qualquer jeito, e mantê-los só gastaria
    // memória de GPU com imagens que nunca serão desenhadas.
    if (o.material) {
      const antigos = Array.isArray(o.material) ? o.material : [o.material];
      for (const m of antigos) {
        for (const chave of Object.keys(m)) {
          const v = m[chave];
          if (v && v.isTexture) v.dispose();
        }
        m.dispose();
      }
    }
    o.material = material;
    if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
  });

  // Enquadra: qualquer modelo cabe numa esfera de raio ~1, centrado.
  const caixa = new T.Box3().setFromObject(raiz);
  const tamanho = caixa.getSize(new T.Vector3());
  const centro = caixa.getCenter(new T.Vector3());
  const maior = Math.max(tamanho.x, tamanho.y, tamanho.z) || 1;
  raiz.position.sub(centro);

  const grupo = new T.Group();
  grupo.add(raiz);
  grupo.scale.setScalar(1.7 / maior);
  return grupo;
}

export class ModelLoadError extends Error {}

/** Carrega um GLB de uma URL, aplicando os tetos. Levanta ModelLoadError. */
export async function carregarGLB(T, GLTFLoader, url, material, limites = LIMITES) {
  const controle = new AbortController();
  const relogio = setTimeout(() => controle.abort(), limites.segundos * 1000);
  let buffer;
  try {
    const resposta = await fetch(url, { signal: controle.signal });
    if (!resposta.ok) throw new ModelLoadError(`HTTP ${resposta.status}`);

    // Confere o tamanho ANTES de baixar tudo, quando o servidor informa.
    const tamanho = Number(resposta.headers.get('Content-Length') || 0);
    if (tamanho > limites.bytes) {
      throw new ModelLoadError(`modelo grande demais (${(tamanho / 1048576).toFixed(1)} MB)`);
    }
    buffer = await resposta.arrayBuffer();
  } catch (erro) {
    throw erro instanceof ModelLoadError
      ? erro
      : new ModelLoadError(erro.name === 'AbortError' ? 'tempo esgotado' : String(erro.message || erro));
  } finally {
    clearTimeout(relogio);
  }

  // E de novo depois: um servidor pode mentir no Content-Length, ou omiti-lo.
  if (buffer.byteLength > limites.bytes) {
    throw new ModelLoadError(`modelo grande demais (${(buffer.byteLength / 1048576).toFixed(1)} MB)`);
  }

  const gltf = await new Promise((resolve, reject) => {
    // Sem `resourcePath`: o loader não deve sair buscando .bin nem textura
    // solta na rede. GLB auto-contido, ou nada.
    new GLTFLoader().parse(buffer, '', resolve, (e) => reject(new ModelLoadError(String(e?.message || e))));
  });

  const { vertices, objetos } = medir(gltf.scene);
  if (vertices > limites.vertices) {
    throw new ModelLoadError(`malha densa demais (${vertices} vértices)`);
  }
  if (objetos > limites.objetos) {
    throw new ModelLoadError(`objetos demais (${objetos})`);
  }

  const grupo = preparar(T, gltf.scene, material);
  grupo.userData.fonte = 'modelo';
  grupo.userData.vertices = vertices;
  return grupo;
}

/* ----------------------------------------------------------- a cascata */

/**
 * Resolve um assunto em um objeto 3D pronto, com o material já aplicado.
 * Nunca levanta: no pior caso devolve o genérico.
 *
 * `buscarRemoto(assunto)` deve devolver `{url, autor, licenca}` ou null.
 * Enquanto a Poly Pizza não estiver plugada, ele simplesmente não é passado.
 */
export async function resolver(T, { GLTFLoader, assunto, material, cache, buscarRemoto, aoRegistrar }) {
  const registrar = (nivel, detalhe) => aoRegistrar && aoRegistrar(nivel, detalhe);

  // 1. catálogo curado
  const curado = criarDoCatalogo(T, assunto, material);
  if (curado) {
    registrar('catalogo', resolverAssunto(assunto));
    return curado;
  }

  const chave = slug(assunto);

  // 2. cache local
  if (chave && cache) {
    try {
      const grupo = await carregarGLB(T, GLTFLoader, `${cache}/${chave}.glb`, material);
      registrar('cache', chave);
      return grupo;
    } catch (erro) {
      // 404 é o caso normal (ainda não baixamos): não vale nem logar.
      if (!String(erro.message).includes('404')) {
        registrar('cache_falhou', String(erro.message));
      }
    }
  }

  // 3. remoto
  if (chave && buscarRemoto) {
    try {
      const achado = await buscarRemoto(assunto);
      if (achado?.url) {
        const grupo = await carregarGLB(T, GLTFLoader, achado.url, material);
        grupo.userData.autor = achado.autor || '';
        grupo.userData.licenca = achado.licenca || '';
        registrar('remoto', achado.autor ? `${chave} · ${achado.autor}` : chave);
        return grupo;
      }
    } catch (erro) {
      registrar('remoto_falhou', String(erro.message));
    }
  }

  // 4. genérico — sempre funciona
  registrar('generico', chave);
  return criarGenerico(T, assunto, material);
}

export { animarCatalogo };
