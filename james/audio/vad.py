"""Detecção de fim de fala (VAD) e montagem do comando gravado.

A máquina de estados é separada da captura do microfone de propósito: ela
consome frames e devolve decisões, o que a torna testável sem hardware.

Regras (todas configuráveis em audio.vad):
  - só começa a gravar após N frames de fala consecutivos (ignora estalo);
  - guarda um preroll do áudio ANTERIOR ao início detectado, senão a primeira
    sílaba é cortada;
  - encerra após um período contínuo de silêncio;
  - teto duro de duração, para o James nunca gravar indefinidamente;
  - descarta o que for curto demais para ser um comando.
"""

from __future__ import annotations

from collections import deque
from enum import Enum
from typing import Protocol

from james.config import AudioFormat


class VoiceDetector(Protocol):
    """Interface mínima do webrtcvad — permite injetar um duplo nos testes."""

    def is_speech(self, frame: bytes, sample_rate: int) -> bool: ...


class RecorderState(Enum):
    WAITING = "waiting"      # aguardando o usuário começar a falar
    RECORDING = "recording"  # capturando o comando
    DONE = "done"            # comando completo
    TIMEOUT = "timeout"      # ninguém falou a tempo


class VadSettings:
    def __init__(self, fmt: AudioFormat, raw: dict | None = None) -> None:
        raw = raw or {}
        self.aggressiveness = _clamp(int(raw.get("aggressiveness", 2)), 0, 3)
        self.start_frames = max(1, int(raw.get("start_frames", 3)))
        self.end_silence_frames = fmt.ms_to_frames(float(raw.get("end_silence_ms", 700)))
        self.preroll_frames = fmt.ms_to_frames(float(raw.get("preroll_ms", 300)))
        self.min_command_frames = fmt.ms_to_frames(float(raw.get("min_command_ms", 400)))
        self.max_command_frames = fmt.ms_to_frames(float(raw.get("max_command_ms", 15000)))
        self.max_wait_frames = fmt.ms_to_frames(float(raw.get("max_wait_speech_ms", 6000)))
        if self.max_command_frames <= self.min_command_frames:
            raise ValueError(
                "audio.vad: max_command_ms precisa ser maior que min_command_ms"
            )


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def build_detector(aggressiveness: int) -> VoiceDetector:
    """Cria o webrtcvad real. Importado tarde para não pesar em quem não usa."""
    import webrtcvad

    return webrtcvad.Vad(aggressiveness)


class SpeechRecorder:
    """Acumula frames até ter um comando completo."""

    def __init__(
        self,
        fmt: AudioFormat,
        settings: VadSettings,
        detector: VoiceDetector | None = None,
    ) -> None:
        self.fmt = fmt
        self.settings = settings
        self.detector = detector or build_detector(settings.aggressiveness)
        self.state = RecorderState.WAITING
        self._preroll: deque[bytes] = deque(maxlen=max(1, settings.preroll_frames))
        self._frames: list[bytes] = []
        self._speech_run = 0
        self._silence_run = 0
        self._waited = 0

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    def process(self, frame: bytes) -> RecorderState:
        """Consome um frame e devolve o estado resultante.

        O frame precisa ter exatamente `fmt.frame_bytes` — o webrtcvad rejeita
        qualquer outro tamanho, e um frame parcial no fim do stream é o jeito
        mais comum de isso acontecer.
        """
        if len(frame) != self.fmt.frame_bytes:
            raise ValueError(
                f"Frame com {len(frame)} bytes; esperado {self.fmt.frame_bytes} "
                f"({self.fmt.frame_ms} ms a {self.fmt.sample_rate} Hz)"
            )
        if self.state in (RecorderState.DONE, RecorderState.TIMEOUT):
            return self.state

        is_speech = self.detector.is_speech(frame, self.fmt.sample_rate)

        if self.state is RecorderState.WAITING:
            self._preroll.append(frame)
            self._waited += 1
            if is_speech:
                self._speech_run += 1
                if self._speech_run >= self.settings.start_frames:
                    # O preroll já contém os frames de fala que dispararam o
                    # início, então a primeira sílaba entra inteira.
                    self._frames = list(self._preroll)
                    self._preroll.clear()
                    self._silence_run = 0
                    self.state = RecorderState.RECORDING
            else:
                self._speech_run = 0
                if self._waited >= self.settings.max_wait_frames:
                    self.state = RecorderState.TIMEOUT
            return self.state

        # RECORDING
        self._frames.append(frame)
        if is_speech:
            self._silence_run = 0
        else:
            self._silence_run += 1
            if self._silence_run >= self.settings.end_silence_frames:
                self.state = RecorderState.DONE
                return self.state

        if len(self._frames) >= self.settings.max_command_frames:
            self.state = RecorderState.DONE
        return self.state

    def audio(self) -> bytes | None:
        """PCM do comando, sem a cauda de silêncio. None se curto demais.

        A cauda é removida porque enviá-la ao STT/LLM só adiciona latência e
        tokens — mas mantemos uma margem para não cortar a consoante final.
        """
        if not self._frames:
            return None

        keep_margin = self.fmt.ms_to_frames(150)
        useful = len(self._frames) - max(0, self._silence_run - keep_margin)
        frames = self._frames[: max(1, useful)]

        if len(frames) < self.settings.min_command_frames:
            return None
        return b"".join(frames)

    def reset(self) -> None:
        self.state = RecorderState.WAITING
        self._preroll.clear()
        self._frames.clear()
        self._speech_run = 0
        self._silence_run = 0
        self._waited = 0
