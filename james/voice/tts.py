"""Síntese de voz com Piper (local, sem cota, sem serviço pago).

Dois backends, escolhidos em runtime:
  1. binário `piper.exe` via subprocess — estável entre versões, é o caminho
     recomendado no Windows fraco (config: tts.binary);
  2. pacote Python `piper-tts`, quando importável.

A API do pacote Python mudou entre versões, então a detecção é por capacidade
(quais métodos existem no objeto), não por número de versão.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from james.logs import get_logger

logger = get_logger("james.voice.tts")

_PREWARM_TEXT = "Sistemas online."
# Teto por sentença. Piper é rápido; passar disso significa que algo travou.
_SYNTH_TIMEOUT_S = 60


class TTSUnavailable(RuntimeError):
    """Nenhum backend do Piper utilizável com a configuração atual."""


class PiperTTS:
    """Sintetiza texto em PCM 16-bit mono."""

    def __init__(
        self,
        voice_path: Path,
        binary: Path | None = None,
        length_scale: float = 1.0,
    ) -> None:
        if voice_path is None:
            raise TTSUnavailable("tts.voice_path não configurado.")
        self.voice_path = Path(voice_path)
        if not self.voice_path.exists():
            raise TTSUnavailable(f"Voz do Piper não encontrada: {self.voice_path}")

        self.binary = Path(binary) if binary else None
        self.length_scale = float(length_scale)
        self.sample_rate = self._read_sample_rate()
        self._voice = None            # instância do pacote Python, se usado
        self._backend: str | None = None
        self._lock = threading.Lock()
        self._resolve_backend()

    # ------------------------------------------------------------- descoberta

    def _read_sample_rate(self) -> int:
        """A taxa vem do JSON que acompanha o .onnx (normalmente 22050 Hz).

        Errar isso não gera exceção: gera voz em velocidade errada, que é bem
        mais difícil de diagnosticar. Por isso lemos do arquivo em vez de fixar.
        """
        config_path = Path(str(self.voice_path) + ".json")
        if not config_path.exists():
            config_path = self.voice_path.with_suffix(".onnx.json")
        if not config_path.exists():
            logger.warning(
                "Config da voz não encontrada ao lado de %s; assumindo 22050 Hz.",
                self.voice_path,
            )
            return 22050
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            rate = int(data.get("audio", {}).get("sample_rate", 22050))
            if rate <= 0:
                raise ValueError(f"sample_rate inválido: {rate}")
            return rate
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Config da voz ilegível (%s); assumindo 22050 Hz.", exc)
            return 22050

    def _resolve_backend(self) -> None:
        if self.binary is not None:
            if not self.binary.exists():
                raise TTSUnavailable(f"Binário do Piper não encontrado: {self.binary}")
            self._backend = "binary"
            logger.info("Piper: binário %s (%d Hz)", self.binary, self.sample_rate)
            return

        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise TTSUnavailable(
                "Nem tts.binary configurado nem o pacote 'piper-tts' instalado. "
                "Baixe o piper.exe e aponte tts.binary no config.yaml."
            ) from exc

        try:
            self._voice = PiperVoice.load(str(self.voice_path))
        except Exception as exc:  # noqa: BLE001 — carga pode falhar por CPU/modelo
            raise TTSUnavailable(
                f"Falha ao carregar a voz pelo pacote Python: {exc}. "
                "Use o binário piper.exe (tts.binary) como alternativa."
            ) from exc

        if not any(
            hasattr(self._voice, name)
            for name in ("synthesize", "synthesize_stream_raw")
        ):
            raise TTSUnavailable(
                "Versão do pacote 'piper-tts' sem método de síntese reconhecido. "
                "Use o binário piper.exe (tts.binary)."
            )

        config_rate = getattr(getattr(self._voice, "config", None), "sample_rate", None)
        if isinstance(config_rate, int) and config_rate > 0:
            self.sample_rate = config_rate
        self._backend = "python"
        logger.info("Piper: pacote Python (%d Hz)", self.sample_rate)

    @property
    def backend(self) -> str:
        return self._backend or "indisponível"

    # ---------------------------------------------------------------- síntese

    def synthesize(self, text: str) -> bytes:
        """Devolve PCM 16-bit mono na taxa da voz. Vazio se o texto for vazio."""
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return b""
        # O Piper trata quebra de linha como separador de utterance; deixar uma
        # passar produziria dois áudios concatenados sem pausa correta.
        cleaned = cleaned.replace("\n", " ")

        with self._lock:
            if self._backend == "binary":
                return self._synthesize_binary(cleaned)
            return self._synthesize_python(cleaned)

    def _synthesize_binary(self, text: str) -> bytes:
        command = [
            str(self.binary),
            "--model", str(self.voice_path),
            "--output_raw",
            "--length_scale", str(self.length_scale),
        ]
        try:
            result = subprocess.run(
                command,
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=_SYNTH_TIMEOUT_S,
                check=False,
                creationflags=_no_window_flag(),
            )
        except subprocess.TimeoutExpired as exc:
            raise TTSUnavailable(
                f"Piper travou (mais de {_SYNTH_TIMEOUT_S}s para uma sentença)."
            ) from exc
        except OSError as exc:
            raise TTSUnavailable(f"Não consegui executar o Piper: {exc}") from exc

        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[-400:]
            raise TTSUnavailable(f"Piper falhou (código {result.returncode}): {detail}")
        if not result.stdout:
            raise TTSUnavailable("Piper não produziu áudio.")
        return result.stdout

    def _synthesize_python(self, text: str) -> bytes:
        voice = self._voice
        try:
            if hasattr(voice, "synthesize_stream_raw"):
                return b"".join(voice.synthesize_stream_raw(text))

            chunks: list[bytes] = []
            for chunk in voice.synthesize(text):
                # Versões novas devolvem AudioChunk; as antigas, bytes.
                data = getattr(chunk, "audio_int16_bytes", None)
                if data is None:
                    data = chunk if isinstance(chunk, (bytes, bytearray)) else None
                if data is None:
                    raise TTSUnavailable(
                        f"Formato de áudio não reconhecido: {type(chunk).__name__}"
                    )
                chunks.append(bytes(data))
            return b"".join(chunks)
        except TTSUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TTSUnavailable(f"Falha na síntese: {exc}") from exc

    def prewarm(self) -> float:
        """Sintetiza uma frase curta no boot e devolve quanto demorou.

        Sem isso, a primeira ativação do dia paga o custo de carregar o modelo
        justamente quando o usuário está esperando resposta.
        """
        started = time.monotonic()
        try:
            self.synthesize(_PREWARM_TEXT)
        except TTSUnavailable as exc:
            logger.error("Prewarm do Piper falhou: %s", exc)
            return -1.0
        elapsed = time.monotonic() - started
        logger.info("Piper aquecido em %.2fs (%s)", elapsed, self.backend)
        return elapsed


def _no_window_flag() -> int:
    """Evita que uma janela de console pisque na tela a cada síntese."""
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0
