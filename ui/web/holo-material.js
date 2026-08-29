/* Material holográfico — o que transforma qualquer malha em projeção.
 *
 * A técnica é a clássica de ficção científica, e vale entender por que cada
 * peça existe, porque a soma é que convence:
 *
 *   1. **Fresnel.** A borda brilha mais que o meio. É o efeito real de um
 *      volume translúcido visto de raspão, e é o que dá leitura de "sólido de
 *      luz" em vez de "adesivo colorido". Sem ele, nada parece holograma.
 *   2. **Scanlines.** Faixas horizontais varrendo o objeto de baixo para cima.
 *      Sozinhas são cafonas; junto com o fresnel viram "está sendo projetado".
 *      Calculadas no espaço do MUNDO, não da tela, senão o objeto girando
 *      arrasta as listras junto e o efeito quebra.
 *   3. **Glitch no vértice.** Deslocamento minúsculo e intermitente. É o que
 *      impede o objeto de parecer parado demais — projeção real tremeria.
 *   4. **Blending aditivo.** Luz soma, não cobre. Duas partes sobrepostas
 *      ficam mais claras, como aconteceria com luz de verdade, e o fundo
 *      aparece através do objeto sem precisar ordenar transparência.
 *
 * O único cuidado de uso: chamar `update(delta)` a cada quadro. Sem isso o
 * tempo não anda e o material fica congelado — parece um bug de renderização,
 * mas é só o relógio parado.
 *
 * Escrito com a API de parâmetros do HolographicMaterial do ektogamat (MIT),
 * porque é a nomenclatura que a comunidade já conhece.
 */

const VERTEX = /* glsl */ `
  uniform float time;
  uniform float signalSpeed;
  uniform float glitchAmount;

  varying vec3 vWorld;
  varying vec3 vNormalW;
  varying vec3 vViewDir;

  // Ruído barato o suficiente para rodar em GPU integrada. Não precisa ser
  // bonito: só precisa ser irregular no tempo.
  float hash(float n) { return fract(sin(n) * 43758.5453123); }

  void main() {
    vec3 pos = position;

    // O glitch acontece em rajadas: a maior parte do tempo o objeto está
    // parado. Um tremor contínuo viraria ruído visual e cansaria.
    float burst = step(0.985, hash(floor(time * 12.0)));
    float linha = step(0.5, hash(floor(pos.y * 40.0 + time * 8.0)));
    pos.x += burst * linha * glitchAmount * (hash(floor(time * 30.0)) - 0.5);

    vec4 mundo = modelMatrix * vec4(pos, 1.0);
    vWorld = mundo.xyz;
    vNormalW = normalize(mat3(modelMatrix) * normal);
    vViewDir = normalize(cameraPosition - mundo.xyz);

    gl_Position = projectionMatrix * viewMatrix * mundo;
  }
`;

const FRAGMENT = /* glsl */ `
  uniform float time;
  uniform float fresnelAmount;
  uniform float fresnelOpacity;
  uniform float hologramBrightness;
  uniform float scanlineSize;
  uniform float signalSpeed;
  uniform vec3  hologramColor;
  uniform float hologramOpacity;
  uniform float enableBlinking;
  uniform float blinkFresnelOnly;

  varying vec3 vWorld;
  varying vec3 vNormalW;
  varying vec3 vViewDir;

  void main() {
    // Normal virada para a câmera nas faces de trás: sem isto o interior do
    // objeto (que aparece, porque é translúcido) fica com fresnel invertido.
    vec3 n = normalize(vNormalW) * (gl_FrontFacing ? 1.0 : -1.0);

    // Fresnel: 0 de frente, 1 de raspão.
    float fresnel = pow(1.0 - clamp(dot(n, normalize(vViewDir)), 0.0, 1.0), fresnelAmount);

    // Scanlines no espaço do mundo: o objeto gira, as listras ficam paradas.
    float scan = sin((vWorld.y - time * signalSpeed * 0.4) * scanlineSize);
    scan = smoothstep(0.0, 1.0, scan) * 0.5 + 0.35;

    // Piscada lenta, para a projeção nunca ficar estática demais.
    float blink = mix(1.0, 0.75 + 0.25 * sin(time * 4.0), enableBlinking);

    float corpo = scan * hologramBrightness;
    float borda = fresnel * fresnelOpacity;
    // blinkFresnelOnly mantem o corpo estavel e faz so a borda piscar: e o
    // que da energia na casca sem transformar o objeto todo num estroboscopio.
    float alpha = mix(corpo * blink + borda, corpo + borda * blink, blinkFresnelOnly);

    gl_FragColor = vec4(hologramColor * (corpo + borda), alpha * hologramOpacity);
  }
`;

export function createHolographicMaterial(T, opcoes = {}) {
  const o = {
    fresnelAmount: 1.8,
    fresnelOpacity: 1.0,
    hologramBrightness: 0.85,
    scanlineSize: 9.0,
    signalSpeed: 1.0,
    hologramColor: '#7cf6ff',
    hologramOpacity: 1.0,
    glitchAmount: 0.04,
    enableBlinking: true,
    blinkFresnelOnly: true,
    enableAdditive: true,
    side: T.DoubleSide,
    ...opcoes,
  };

  const material = new T.ShaderMaterial({
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    transparent: true,
    // Sem escrita no z-buffer: um holograma é translúcido, e escrever
    // profundidade faria as partes de trás sumirem conforme a ordem de desenho.
    depthWrite: false,
    blending: o.enableAdditive ? T.AdditiveBlending : T.NormalBlending,
    side: o.side,
    uniforms: {
      time: { value: 0 },
      fresnelAmount: { value: o.fresnelAmount },
      fresnelOpacity: { value: o.fresnelOpacity },
      hologramBrightness: { value: o.hologramBrightness },
      scanlineSize: { value: o.scanlineSize },
      signalSpeed: { value: o.signalSpeed },
      hologramColor: { value: new T.Color(o.hologramColor) },
      hologramOpacity: { value: o.hologramOpacity },
      glitchAmount: { value: o.glitchAmount },
      enableBlinking: { value: o.enableBlinking ? 1 : 0 },
      blinkFresnelOnly: { value: o.blinkFresnelOnly ? 1 : 0 },
    },
  });

  material.update = (delta) => {
    material.uniforms.time.value += Number.isFinite(delta) ? delta : 0.016;
  };
  material.setColor = (cor) => material.uniforms.hologramColor.value.set(cor);

  return material;
}

/* Paleta por persona. O material é o mesmo; só a cor muda. */
export const CORES = {
  jarvis: '#7cf6ff',
  ultron: '#ffb347',
};
