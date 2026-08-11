"""Transcrição local com whisper.cpp.

Papel no projeto (revisado): o caminho comum manda o áudio direto ao Gemini,
então o STT local NÃO está no caminho crítico. Ele existe para três coisas em
que é insubstituível:

  1. transcrever a resposta de confirmação de ações de risco — essa precisa ser
     local e determinística, nunca depender de rede nem de modelo remoto (C2);
  2. modo degradado: sem internet ou sem cota, é o único jeito de entender fala;
  3. alimentar o roteador local de comandos frequentes.

whisper.cpp é usado (em vez de faster-whisper) porque tem caminho de execução
genérico em CPU sem AVX. Sem AVX ele é lento — por isso não fica no caminho
comum.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from james.audio.wav import write_wav_file
from james.config import AudioFormat
from james.logs import get_logger

logger = get_logger("james.voice.stt")

# Linhas de ruído que o whisper.cpp emite quando não há fala reconhecível.
_NOISE_LINES = re.compile(
    r"^\s*[\[\(](?:BLANK_AUDIO|INAUDÍVEL|INAUDIBLE|MUSIC|MÚSICA|SILENCE|SILÊNCIO|"
    r"sons? de [^\]\)]*|ruído[^\]\)]*)[\]\)]\s*$",
    re.IGNORECASE,
)
_TIMESTAMP_PREFIX = re.compile(r"^\s*\[[\d:.\s\->]+\]\s*")


class STTUnavailable(RuntimeError):
    """whisper.cpp não configurado, ausente ou com falha de execução."""


class WhisperCppSTT:
    def __init__(
        self,
        binary: Path | None,
        model: Path | None,
        audio_format: AudioFormat,
        language: str = "pt",
        timeout_s: int = 60,
        threads: int = 4,
    ) -> None:
        if binary is None:
            raise STTUnavailable(
                "stt.binary não configurado. Sem whisper.cpp não há modo offline "
                "nem confirmação de ações de risco."
            )
        if model is None:
            raise STTUnavailable("stt.model não configurado.")
        self.binary = Path(binary)
        self.model = Path(model)
        if not self.binary.exists():
            raise STTUnavailable(f"Binário do whisper.cpp não encontrado: {self.binary}")
        if not self.model.exists():
            raise STTUnavailable(f"Modelo do whisper.cpp não encontrado: {self.model}")

        self.audio_format = audio_format
        self.language = language
        self.timeout_s = max(1, int(timeout_s))
        self.threads = max(1, int(threads))

    def transcribe(self, pcm: bytes) -> str | None:
        """Transcreve PCM. Devolve None se não houver fala reconhecível.

        None e "" são diferentes de propósito: quem chama (a confirmação de
        risco) precisa distinguir "não entendi" de "entendi silêncio", e ambos
        levam a negar — mas por motivos distintos no log.
        """
        if not pcm:
            return None

        started = time.monotonic()
        # delete=False + remoção manual: no Windows um arquivo aberto por este
        # processo não pode ser lido por outro processo ao mesmo tempo.
        handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        handle.close()
        wav_path = Path(handle.name)

        try:
            write_wav_file(wav_path, pcm, self.audio_format)
            text = self._run(wav_path)
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Não consegui remover o temporário %s: %s", wav_path, exc)

        elapsed = time.monotonic() - started
        audio_seconds = self.audio_format.bytes_to_ms(len(pcm)) / 1000.0
        logger.info(
            "whisper.cpp: %.1fs de áudio em %.1fs (%.1fx tempo real)",
            audio_seconds,
            elapsed,
            elapsed / audio_seconds if audio_seconds > 0 else 0.0,
        )
        return text

    def _run(self, wav_path: Path) -> str | None:
        command = [
            str(self.binary),
            "-m", str(self.model),
            "-f", str(wav_path),
            "-l", self.language,
            "-t", str(self.threads),
            "-nt",   # sem timestamps
            "-np",   # sem cabeçalho de progresso
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
                creationflags=_no_window_flag(),
            )
        except subprocess.TimeoutExpired:
            logger.error("whisper.cpp excedeu %ds e foi encerrado.", self.timeout_s)
            return None
        except OSError as exc:
            raise STTUnavailable(f"Não consegui executar o whisper.cpp: {exc}") from exc

        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[-400:]
            logger.error("whisper.cpp falhou (código %d): %s", result.returncode, detail)
            return None

        return _clean_output(result.stdout.decode("utf-8", errors="replace"))


def _clean_output(raw: str) -> str | None:
    """Extrai o texto útil, descartando marcações de ruído e timestamps."""
    lines: list[str] = []
    for line in raw.splitlines():
        line = _TIMESTAMP_PREFIX.sub("", line).strip()
        if not line or _NOISE_LINES.match(line):
            continue
        lines.append(line)
    text = " ".join(lines).strip()
    return text or None


def _no_window_flag() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0
