"""Empacotamento WAV — só stdlib, sem dependência de áudio.

Usado para entregar o comando gravado ao Gemini (que precisa de um container
reconhecível) e ao whisper.cpp (que lê arquivo).
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

from james.config import AudioFormat


def pcm_to_wav(pcm: bytes, fmt: AudioFormat) -> bytes:
    """Envolve PCM cru num container WAV em memória."""
    if not pcm:
        raise ValueError("PCM vazio: nada para empacotar")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(fmt.channels)
        handle.setsampwidth(fmt.sample_width)
        handle.setframerate(fmt.sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def write_wav_file(path: str | Path, pcm: bytes, fmt: AudioFormat) -> Path:
    """Grava PCM como .wav no disco (whisper.cpp precisa de arquivo)."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(pcm_to_wav(pcm, fmt))
    return destination


def wav_duration_seconds(wav_bytes: bytes) -> float:
    """Duração de um WAV em memória. Usado para medir tempo real do TTS."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
    if rate <= 0:
        raise ValueError("WAV com taxa de amostragem inválida")
    return frames / float(rate)


def read_wav(wav_bytes: bytes) -> tuple[bytes, int, int, int]:
    """Devolve (pcm, sample_rate, channels, sample_width) de um WAV."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
        return (
            handle.readframes(handle.getnframes()),
            handle.getframerate(),
            handle.getnchannels(),
            handle.getsampwidth(),
        )
