"""Modo de gestos — a webcam só liga quando você manda.

Este arquivo tem três partes que existem separadas de propósito:

1. **O classificador** (`classificar`) é matemática pura sobre 21 pontos. Não
   sabe o que é câmera, thread ou MediaPipe. É o que permite testar "mão
   fechada vira punho" sem nenhum hardware.
2. **O debouncer** decide *quando* um gesto vira uma ação. Sem ele, uma mão
   passando na frente da câmera dispararia dezenas de comandos por segundo. Um
   gesto precisa se manter estável por vários quadros para valer, e depois
   precisa sair de cena antes de valer de novo.
3. **O modo** (`GestureMode`) abre a câmera, roda o laço e — o que mais importa
   — fecha tudo no `_desligar`. Enquanto o modo está desligado, este arquivo
   não consome um único ciclo de CPU.

Uma regra de segurança que vale a pena ler antes de mexer aqui:

    **Um gesto nunca executa ação de Nível 2.**

O motivo é simples: o guard pede confirmação justamente quando precisa ter
certeza de *quem* está mandando. Uma mão na frente da câmera não identifica
ninguém — pode ser outra pessoa, pode ser você numa videochamada, pode ser uma
foto. Então gesto só alcança o que já é Nível 1. Se o guard responder CONFIRM,
o gesto é recusado, não promovido a uma pergunta.

O modo também se desliga sozinho depois de um tempo sem ver nada
(`timeout_min`). É a defesa contra o caso mais provável de todos: você ligar,
esquecer, e a câmera ficar aberta a tarde inteira.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

from james.logs import audit, get_logger
from james.modes.base import Mode, ModeError

logger = get_logger("james.modes.gestos")

# Índices do modelo de mão do MediaPipe (21 pontos).
PULSO = 0
POLEGAR_IP, POLEGAR_PONTA = 3, 4
INDICADOR_PIP, INDICADOR_PONTA = 6, 8
MEDIO_MCP, MEDIO_PIP, MEDIO_PONTA = 9, 10, 12
ANELAR_PIP, ANELAR_PONTA = 14, 16
MINIMO_MCP, MINIMO_PIP, MINIMO_PONTA = 17, 18, 20

NUM_PONTOS = 21

# Um dedo conta como estendido quando a ponta está claramente mais longe do
# pulso que a articulação do meio. A folga de 8% evita que ruído de um pixel
# faça o dedo "piscar" entre aberto e fechado.
_FOLGA_EXTENSAO = 1.08

# Mão pequena demais no quadro é ruído: ou está longe, ou é um falso positivo.
_PALMA_MINIMA = 0.08

# Ações que um gesto pode pedir. Fora desta lista, o mapeamento é ignorado no
# carregamento — não existe gesto que chame ferramenta arbitrária.
ACOES_DE_GESTO: dict[str, str] = {
    "parar": "corta a fala e cancela o turno",
    "pausar_escuta": "alterna entre escutar e ficar em silêncio",
    "volume_mais": "aumenta o volume do sistema",
    "volume_menos": "diminui o volume do sistema",
    "mudo": "alterna o som do sistema",
    "desligar_modo": "desliga o próprio modo de gestos",
}

# Mapeamento padrão. Escolhido para que os gestos mais fáceis de fazer sem
# querer (palma aberta ao gesticular) sejam os menos destrutivos.
GESTOS_PADRAO: dict[str, str] = {
    "punho": "parar",
    "palma_aberta": "pausar_escuta",
    "polegar_cima": "volume_mais",
    "polegar_baixo": "volume_menos",
    "v": "mudo",
    "apontar": "",  # sem ação por padrão
}


# --------------------------------------------------------------- geometria


@dataclass(frozen=True)
class Mao:
    """21 pontos normalizados (0..1) no plano da imagem, na ordem do MediaPipe."""

    pontos: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if len(self.pontos) != NUM_PONTOS:
            raise ValueError(f"Esperava {NUM_PONTOS} pontos, recebi {len(self.pontos)}.")

    def ponto(self, indice: int) -> tuple[float, float]:
        return self.pontos[indice]


def _distancia(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def tamanho_da_palma(mao: Mao) -> float:
    """Pulso até a base do dedo médio: a régua de escala da mão no quadro."""
    return _distancia(mao.ponto(PULSO), mao.ponto(MEDIO_MCP))


def dedos_estendidos(mao: Mao) -> tuple[bool, bool, bool, bool, bool]:
    """(polegar, indicador, médio, anelar, mínimo).

    A comparação é por *distância até o pulso*, não por altura na imagem. Isso
    é de propósito: comparar `y` só funciona com a mão em pé, e ninguém segura
    a mão perfeitamente reta. Distância radial funciona com a mão inclinada,
    deitada ou de cabeça para baixo.

    O polegar é medido contra a base do mínimo, do outro lado da palma, porque
    ele dobra para o lado e não para dentro como os outros.
    """
    pulso = mao.ponto(PULSO)
    base_minimo = mao.ponto(MINIMO_MCP)

    polegar = (
        _distancia(mao.ponto(POLEGAR_PONTA), base_minimo)
        > _distancia(mao.ponto(POLEGAR_IP), base_minimo) * _FOLGA_EXTENSAO
    )

    def estendido(ponta: int, pip: int) -> bool:
        return (
            _distancia(mao.ponto(ponta), pulso)
            > _distancia(mao.ponto(pip), pulso) * _FOLGA_EXTENSAO
        )

    return (
        polegar,
        estendido(INDICADOR_PONTA, INDICADOR_PIP),
        estendido(MEDIO_PONTA, MEDIO_PIP),
        estendido(ANELAR_PONTA, ANELAR_PIP),
        estendido(MINIMO_PONTA, MINIMO_PIP),
    )


def classificar(mao: Mao) -> str | None:
    """Nome do gesto, ou None quando não é nenhum dos conhecidos.

    Devolver None é o caso mais comum e está certo: a maior parte do tempo a
    mão está numa posição intermediária, e um classificador que sempre chuta
    algum gesto dispara comandos que ninguém pediu.
    """
    if tamanho_da_palma(mao) < _PALMA_MINIMA:
        return None

    polegar, indicador, medio, anelar, minimo = dedos_estendidos(mao)
    abertos = sum((polegar, indicador, medio, anelar, minimo))

    if abertos == 5:
        return "palma_aberta"
    if abertos == 0:
        return "punho"

    if (polegar, indicador, medio, anelar, minimo) == (True, False, False, False, False):
        # Polegar para cima ou para baixo: aqui, sim, a altura importa — é o
        # que distingue os dois gestos. `y` cresce para baixo na imagem.
        ponta_y = mao.ponto(POLEGAR_PONTA)[1]
        pulso_y = mao.ponto(PULSO)[1]
        margem = tamanho_da_palma(mao) * 0.3
        if ponta_y < pulso_y - margem:
            return "polegar_cima"
        if ponta_y > pulso_y + margem:
            return "polegar_baixo"
        return None

    if (indicador, medio, anelar, minimo) == (True, True, False, False):
        return "v"
    if (indicador, medio, anelar, minimo) == (True, False, False, False):
        return "apontar"
    return None


# --------------------------------------------------------------- debounce


@dataclass
class Debouncer:
    """Transforma uma sequência de classificações em disparos espaçados.

    Duas travas, porque uma só não basta:

    - `estabilidade`: o mesmo gesto precisa aparecer N quadros seguidos. Mata o
      ruído de um quadro isolado mal classificado.
    - `descanso`: depois de disparar, o mesmo gesto só volta a valer quando a
      mão sair dele *e* passar o tempo de descanso. É o que impede que segurar
      a mão parada por dois segundos aumente o volume trinta vezes.
    """

    estabilidade: int = 4
    descanso_s: float = 1.5
    relogio: Callable[[], float] = time.monotonic

    _atual: str | None = field(default=None, init=False)
    _contagem: int = field(default=0, init=False)
    _ultimo_disparo: str | None = field(default=None, init=False)
    _momento_disparo: float = field(default=0.0, init=False)

    def alimentar(self, gesto: str | None) -> str | None:
        """Devolve o gesto quando ele deve virar ação; None nos outros quadros."""
        if gesto != self._atual:
            self._atual = gesto
            self._contagem = 1
        else:
            self._contagem += 1

        if gesto is None:
            # Mão saiu de cena. O gesto anterior NÃO é esquecido aqui: se fosse,
            # bastaria tirar a mão do quadro por um quadro para furar o descanso,
            # e "sair do gesto E esperar" viraria "sair do gesto OU esperar".
            # Quem garante o "saiu" é a contagem, que zerou junto com o gesto.
            return None

        if self._contagem != max(1, self.estabilidade):
            # `!=` e não `>=`: dispara exatamente no quadro em que completou,
            # e não a cada quadro enquanto a mão continuar parada.
            return None

        agora = self.relogio()
        if gesto == self._ultimo_disparo and agora - self._momento_disparo < self.descanso_s:
            return None

        self._ultimo_disparo = gesto
        self._momento_disparo = agora
        return gesto

    def reset(self) -> None:
        self._atual = None
        self._contagem = 0
        self._ultimo_disparo = None
        self._momento_disparo = 0.0


def carregar_mapeamento(bruto: dict | None) -> dict[str, str]:
    """Gesto -> ação, descartando o que não está na lista de ações permitidas."""
    mapeamento = dict(GESTOS_PADRAO)
    for gesto, acao in dict(bruto or {}).items():
        nome = str(gesto or "").strip().lower()
        alvo = str(acao or "").strip().lower()
        if not nome:
            continue
        if alvo and alvo not in ACOES_DE_GESTO:
            logger.warning("Ação de gesto desconhecida ignorada: %r", alvo)
            continue
        mapeamento[nome] = alvo
    return {gesto: acao for gesto, acao in mapeamento.items() if acao}


# ------------------------------------------------------------ captura real


class CameraIndisponivel(RuntimeError):
    pass


class OpenCVCamera:
    """Câmera de verdade. Só é construída quando o modo liga."""

    def __init__(self, indice: int = 0, largura: int = 640, altura: int = 480) -> None:
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise CameraIndisponivel(
                "O modo de gestos precisa do OpenCV: pip install opencv-python"
            ) from exc

        self._cv2 = cv2
        self._capture = cv2.VideoCapture(indice)
        if not self._capture.isOpened():
            self._capture.release()
            raise CameraIndisponivel("Não consegui abrir a câmera. Ela está em uso?")
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, largura)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, altura)

    def ler(self):
        ok, quadro = self._capture.read()
        if not ok or quadro is None:
            return None
        return self._cv2.cvtColor(quadro, self._cv2.COLOR_BGR2RGB)

    def liberar(self) -> None:
        self._capture.release()


class MediaPipeHands:
    """Rastreamento de mão. Carregado só quando o modo liga."""

    def __init__(self, modelo: str, max_maos: int = 1, confianca: float = 0.5) -> None:
        try:
            import mediapipe as mp
            import numpy as np
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise CameraIndisponivel(
                "O modo de gestos precisa do MediaPipe: pip install mediapipe"
            ) from exc

        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        self._mp = mp
        self._np = np
        opcoes = mp_vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=modelo),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=max_maos,
            min_hand_detection_confidence=confianca,
            min_tracking_confidence=confianca,
        )
        self._landmarker = mp_vision.HandLandmarker.create_from_options(opcoes)

    def detectar(self, quadro, momento_ms: int) -> list[Mao]:
        imagem = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=self._np.ascontiguousarray(quadro),
        )
        resultado = self._landmarker.detect_for_video(imagem, momento_ms)
        maos: list[Mao] = []
        for pontos in getattr(resultado, "hand_landmarks", []) or []:
            if len(pontos) != NUM_PONTOS:
                continue
            maos.append(Mao(tuple((p.x, p.y) for p in pontos)))
        return maos

    def fechar(self) -> None:
        close = getattr(self._landmarker, "close", None)
        if close is not None:
            close()


# ------------------------------------------------------------------ o modo


class GestureMode(Mode):
    nome = "gestos"
    descricao = "Controla o James por gestos de mão pela webcam."
    recursos = ("camera",)
    # Ligar abre a câmera: o guard pergunta antes, sempre.
    sensivel = True

    def __init__(
        self,
        *,
        on_acao: Callable[[str, str], None],
        fps: float = 6.0,
        estabilidade: int = 4,
        descanso_s: float = 1.5,
        timeout_min: float = 10.0,
        mapeamento: dict[str, str] | None = None,
        camera_factory: Callable[[], object] | None = None,
        detector_factory: Callable[[], object] | None = None,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._on_acao = on_acao
        self._intervalo = 1.0 / max(0.5, float(fps))
        self._timeout_s = max(0.0, float(timeout_min)) * 60.0
        self._mapeamento = dict(mapeamento or GESTOS_PADRAO)
        self._camera_factory = camera_factory
        self._detector_factory = detector_factory
        self._relogio = relogio
        self._debouncer = Debouncer(
            estabilidade=estabilidade, descanso_s=descanso_s, relogio=relogio
        )

        self._camera = None
        self._detector = None
        self._thread: threading.Thread | None = None
        self._parar = threading.Event()
        self._ultimo_gesto_em = 0.0
        self.gestos_vistos = 0

    # ------------------------------------------------------------ ciclo

    def _ligar(self) -> str:
        if self._camera_factory is None or self._detector_factory is None:
            raise ModeError(
                "O modo de gestos não está configurado nesta execução."
            )
        try:
            self._camera = self._camera_factory()
            self._detector = self._detector_factory()
        except CameraIndisponivel as exc:
            self._soltar_hardware()
            raise ModeError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            self._soltar_hardware()
            raise ModeError(f"Não consegui iniciar o rastreamento: {exc}") from exc

        self._debouncer.reset()
        self._parar.clear()
        self._ultimo_gesto_em = self._relogio()
        self.gestos_vistos = 0
        self._thread = threading.Thread(
            target=self._laco, name="james-gestos", daemon=True
        )
        self._thread.start()
        return (
            "Webcam ligada, modo de gestos ativo. "
            "Punho para parar, palma aberta para pausar a escuta."
        )

    def _desligar(self) -> str:
        self._parar.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            # O laço checa `_parar` a cada quadro; o teto é generoso porque
            # `read()` de uma câmera travada pode demorar.
            thread.join(timeout=3.0)
        self._soltar_hardware()
        return "Webcam desligada."

    def _soltar_hardware(self) -> None:
        """A luz da câmera só apaga aqui — então nada pode escapar deste método."""
        camera, self._camera = self._camera, None
        detector, self._detector = self._detector, None
        for recurso, metodo in ((camera, "liberar"), (detector, "fechar")):
            if recurso is None:
                continue
            try:
                getattr(recurso, metodo)()
            except Exception as exc:  # noqa: BLE001
                logger.error("Falha ao liberar %s: %s", type(recurso).__name__, exc)

    # -------------------------------------------------------------- laço

    def _laco(self) -> None:
        inicio = self._relogio()
        while not self._parar.is_set():
            ciclo = self._relogio()
            try:
                self._passo(int((ciclo - inicio) * 1000))
            except Exception as exc:  # noqa: BLE001 — um quadro ruim não mata o modo
                logger.error("Erro no rastreamento de gestos: %s", exc)

            if self._expirou(ciclo):
                logger.info("Modo de gestos ocioso: desligando sozinho.")
                audit("modo_gestos_timeout")
                threading.Thread(target=self.desligar, daemon=True).start()
                return

            restante = self._intervalo - (self._relogio() - ciclo)
            if restante > 0:
                # `wait` e não `sleep`: desligar não espera o próximo quadro.
                self._parar.wait(restante)

    def _expirou(self, agora: float) -> bool:
        return self._timeout_s > 0 and (agora - self._ultimo_gesto_em) >= self._timeout_s

    def _passo(self, momento_ms: int) -> None:
        camera, detector = self._camera, self._detector
        if camera is None or detector is None:
            return
        quadro = camera.ler()
        if quadro is None:
            return

        maos: Sequence[Mao] = detector.detectar(quadro, momento_ms)
        gesto = classificar(maos[0]) if maos else None
        disparo = self._debouncer.alimentar(gesto)
        if disparo is None:
            return

        self._ultimo_gesto_em = self._relogio()
        acao = self._mapeamento.get(disparo, "")
        if not acao:
            return

        self.gestos_vistos += 1
        audit("gesto", gesto=disparo, acao=acao)
        logger.info("Gesto '%s' -> ação '%s'.", disparo, acao)
        try:
            self._on_acao(disparo, acao)
        except Exception as exc:  # noqa: BLE001
            logger.error("Ação do gesto '%s' falhou: %s", disparo, exc)
