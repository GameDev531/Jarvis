"""Reprodução de áudio com parada imediata.

A parada importa tanto quanto a reprodução: o kill switch (Ctrl+Alt+J) e o
barge-in precisam calar o James no meio de uma frase. A escrita acontece em
blocos curtos justamente para que a checagem de parada seja frequente.
"""

from __future__ import annotations

import threading

from james.logs import get_logger

logger = get_logger("james.audio.player")

# Bloco de escrita: define a granularidade da parada. 50 ms é imperceptível
# para o ouvinte e faz o kill switch parecer instantâneo.
_WRITE_CHUNK_MS = 50


class AudioPlayer:
    """Toca PCM 16-bit. Reaproveita o stream entre chamadas de mesmo formato."""

    def __init__(self, device: int | str | None = None) -> None:
        self.device = device
        self._stream = None
        self._stream_key: tuple[int, int] | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._playing = threading.Event()

    @property
    def is_playing(self) -> bool:
        return self._playing.is_set()

    def _ensure_stream(self, sample_rate: int, channels: int):
        import sounddevice as sd

        key = (sample_rate, channels)
        if self._stream is not None and self._stream_key == key:
            return self._stream

        self._close_stream()
        stream = sd.RawOutputStream(
            samplerate=sample_rate,
            device=self.device,
            channels=channels,
            dtype="int16",
        )
        stream.start()
        self._stream = stream
        self._stream_key = key
        return stream

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        self._stream_key = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Erro ao fechar a saída de áudio: %s", exc)

    def play(self, pcm: bytes, sample_rate: int, channels: int = 1) -> bool:
        """Toca PCM até o fim. Devolve False se foi interrompido.

        Bloqueia a thread chamadora — deve ser chamada da thread de voz, nunca
        da thread da interface.
        """
        if not pcm:
            return True

        bytes_per_frame = 2 * channels
        chunk_frames = max(1, int(sample_rate * _WRITE_CHUNK_MS / 1000))
        chunk_bytes = chunk_frames * bytes_per_frame

        with self._lock:
            self._stop.clear()
            self._playing.set()
            try:
                stream = self._ensure_stream(sample_rate, channels)
                for offset in range(0, len(pcm), chunk_bytes):
                    if self._stop.is_set():
                        return False
                    block = pcm[offset : offset + chunk_bytes]
                    # A última fatia pode não fechar um frame inteiro; escrever
                    # bytes parciais desalinha o stream e gera estalo.
                    usable = len(block) - (len(block) % bytes_per_frame)
                    if usable <= 0:
                        continue
                    stream.write(block[:usable])
                return not self._stop.is_set()
            except Exception as exc:  # noqa: BLE001
                logger.error("Falha ao reproduzir áudio: %s", exc)
                self._close_stream()
                return False
            finally:
                self._playing.clear()

    def stop(self) -> None:
        """Sinaliza parada. Seguro chamar de qualquer thread."""
        self._stop.set()

    def close(self) -> None:
        self.stop()
        with self._lock:
            self._close_stream()
