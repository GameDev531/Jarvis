"""Captura do microfone.

Regra de arquitetura: apenas UM processo segura o microfone por vez. O
wake_listener o mantém aberto enquanto escuta a palavra de ativação, fecha ao
detectar, e o orquestrador abre para gravar o comando. Isso evita disputa pelo
dispositivo no Windows e, de quebra, garante o half-duplex (C3).
"""

from __future__ import annotations

import queue
import threading
from typing import Iterator

from james.config import AudioFormat
from james.logs import get_logger

logger = get_logger("james.audio.capture")


class AudioDeviceError(RuntimeError):
    """Microfone indisponível, ocupado ou com formato não suportado."""


class MicrophoneStream:
    """Stream de entrada que entrega frames PCM de tamanho exato.

    Usar como context manager garante que o dispositivo é liberado mesmo se o
    pipeline levantar exceção — sem isso, o outro processo nunca conseguiria o
    microfone de volta.
    """

    def __init__(
        self,
        fmt: AudioFormat,
        device: int | str | None = None,
        max_queued_frames: int = 100,
    ) -> None:
        self.fmt = fmt
        self.device = device
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=max_queued_frames)
        self._stream = None
        self._overflow_count = 0
        self._closed = threading.Event()

    # ------------------------------------------------------------ ciclo de vida

    def __enter__(self) -> "MicrophoneStream":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def start(self) -> None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            raise AudioDeviceError(
                f"sounddevice indisponível ({exc}). Instale-o e confirme que o "
                "PortAudio está presente."
            ) from exc

        def callback(indata, frames, time_info, status) -> None:
            # Roda na thread de áudio do PortAudio: nada de trabalho pesado aqui.
            if status:
                logger.debug("Status do stream de entrada: %s", status)
            try:
                self._queue.put_nowait(bytes(indata))
            except queue.Full:
                # Descartar o frame mais antigo é melhor que travar a thread de
                # áudio; o overflow é contado para aparecer no diagnóstico.
                self._overflow_count += 1
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(bytes(indata))
                except (queue.Empty, queue.Full):
                    pass

        try:
            self._stream = sd.RawInputStream(
                samplerate=self.fmt.sample_rate,
                blocksize=self.fmt.frame_samples,
                device=self.device,
                channels=self.fmt.channels,
                dtype="int16",
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:  # sounddevice levanta tipos variados
            self._stream = None
            raise AudioDeviceError(
                f"Não consegui abrir o microfone (device={self.device!r}, "
                f"{self.fmt.sample_rate} Hz mono): {exc}"
            ) from exc

        self._closed.clear()
        logger.debug(
            "Microfone aberto: %d Hz, frames de %d ms", self.fmt.sample_rate, self.fmt.frame_ms
        )

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:  # noqa: BLE001 — fechar nunca deve propagar
                logger.warning("Erro ao fechar o microfone: %s", exc)
        self._closed.set()
        if self._overflow_count:
            logger.warning(
                "%d frames descartados por sobrecarga da fila de áudio", self._overflow_count
            )
            self._overflow_count = 0
        self._drain()

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # --------------------------------------------------------------- leitura

    @property
    def active(self) -> bool:
        return self._stream is not None

    def read(self, timeout: float = 1.0) -> bytes | None:
        """Devolve um frame ou None se nada chegou dentro do timeout."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def frames(self, timeout: float = 1.0) -> Iterator[bytes]:
        """Itera frames enquanto o stream estiver aberto."""
        while self.active:
            frame = self.read(timeout=timeout)
            if frame is None:
                continue
            yield frame
