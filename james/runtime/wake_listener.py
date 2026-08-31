"""Processo 1 — escuta a palavra de ativação e supervisiona o orquestrador.

É o processo que fica vivo o tempo todo, então é deliberadamente magro: sem Qt,
sem modelo de linguagem, sem síntese de voz. Só microfone, Porcupine, um socket
de loopback e a supervisão do processo 2.

Duas responsabilidades além de escutar:

  - Lock de instância única: o bind da porta de loopback já resolve isso. Dois
    James rodando brigariam pelo microfone, e o segundo falha no bind.

  - Watchdog (A2): o orquestrador manda um sinal de vida periódico. Se ele
    morrer ou travar, este processo o reinicia com espera progressiva. É a
    versão simples do isolamento de sessão que o LiveKit daria de graça.

Regra do microfone: só um processo o segura por vez. Ao detectar a palavra de
ativação, este processo FECHA o microfone antes de avisar o orquestrador.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Iterator

from james.audio.capture import AudioDeviceError, MicrophoneStream
from james.config import AudioFormat, Config, ConfigError, get_secret, load_config
from james.logs import audit, get_logger, setup_logging
from james.runtime.wake_engines import (
    HotkeyEngine,
    WakeWordUnavailable,
    build_wake_engine,
)
from james.state.ipc import IpcServer, PortUnavailable

logger = get_logger("james.wake")

_ORCHESTRATOR_MODULE = "james.runtime.orchestrator"


class _FrameRechunker:
    """Reagrupa o áudio no tamanho exato que o Porcupine exige.

    O microfone entrega blocos do tamanho que o driver quiser; o Porcupine
    aceita apenas frames de `frame_length` amostras. Sem este acumulador, um
    bloco de tamanho diferente derruba a detecção com erro de tamanho inválido.
    """

    def __init__(self, frame_bytes: int) -> None:
        if frame_bytes <= 0:
            raise ValueError("frame_bytes deve ser positivo")
        self.frame_bytes = frame_bytes
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        self._buffer.extend(chunk)
        while len(self._buffer) >= self.frame_bytes:
            frame = bytes(self._buffer[: self.frame_bytes])
            del self._buffer[: self.frame_bytes]
            yield frame

    def reset(self) -> None:
        self._buffer.clear()


class WakeListener:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.porcupine = build_wake_engine(config, get_secret)
        # O Porcupine dita o formato: 16 kHz mono e frames de 512 amostras.
        self.audio_format = AudioFormat(
            sample_rate=self.porcupine.sample_rate,
            channels=1,
            sample_width=2,
            frame_ms=int(round(self.porcupine.frame_length * 1000 / self.porcupine.sample_rate)),
        )

        # O Porcupine exige um número EXATO de amostras por chamada, e derivar
        # esse número de volta a partir de `frame_ms` passa por milissegundos
        # inteiros — um intermediário com perda. Hoje fecha (512 amostras a
        # 16 kHz dão 32 ms redondos), mas a 22050 Hz daria 507 e o Porcupine
        # rejeitaria todos os frames, sem que nada aqui percebesse.
        #
        # Guardar o valor exato tira o arredondamento do caminho.
        self.frame_bytes = self.porcupine.frame_length * 2   # 16-bit mono
        if self.frame_bytes != self.audio_format.frame_bytes:
            logger.warning(
                "Frame do Porcupine (%d amostras) não cai num número redondo de "
                "milissegundos; usando o valor exato.",
                self.porcupine.frame_length,
            )

        self.server = IpcServer(
            host=str(config.get("ipc.host", "127.0.0.1")),
            port=int(config.get("ipc.port", 47821)),
            on_message=self._on_message,
        )

        self._listening = threading.Event()
        self._listening.set()
        self._running = threading.Event()
        self._child: subprocess.Popen | None = None
        self._hotkey = None
        self._last_heartbeat = time.monotonic()
        self._restarts = 0

    # ---------------------------------------------------------------- IPC

    def _on_message(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "heartbeat":
            self._last_heartbeat = time.monotonic()
        elif kind == "resume_wake":
            self._listening.set()
            logger.debug("Escuta retomada.")
        elif kind == "pause_wake":
            self._listening.clear()
            logger.debug("Escuta pausada.")
        elif kind == "bye":
            logger.info("Orquestrador encerrou por conta própria.")

    # ------------------------------------------------------ processo filho

    def _spawn_orchestrator(self) -> None:
        if self._child is not None and self._child.poll() is None:
            return
        logger.info("Iniciando o orquestrador.")
        try:
            self._child = subprocess.Popen(
                [sys.executable, "-m", _ORCHESTRATOR_MODULE],
                cwd=str(self.config.root),
            )
        except OSError as exc:
            logger.error("Não consegui iniciar o orquestrador: %s", exc)
            self._child = None
            return
        self._last_heartbeat = time.monotonic()

    def _backoff_seconds(self) -> float:
        steps = list(self.config.get("ipc.restart_backoff_s", [2, 4, 8, 16, 30]) or [5])
        index = min(self._restarts, len(steps) - 1)
        return float(steps[index])

    def _watchdog_loop(self) -> None:
        timeout = float(self.config.get("ipc.heartbeat_timeout_s", 30))
        while self._running.is_set():
            time.sleep(1.0)
            if self._child is None:
                continue

            exit_code = self._child.poll()
            if exit_code is not None:
                wait = self._backoff_seconds()
                logger.error(
                    "Orquestrador terminou (código %s). Reiniciando em %.0fs.", exit_code, wait
                )
                audit("orquestrador_caiu", codigo=exit_code, espera_s=wait)
                self._restarts += 1
                self._child = None
                time.sleep(wait)
                if self._running.is_set():
                    self._spawn_orchestrator()
                continue

            # Processo vivo mas mudo: travou. Matar e deixar renascer é melhor
            # que ficar com um James que nunca responde.
            silent_for = time.monotonic() - self._last_heartbeat
            if silent_for > timeout:
                logger.error(
                    "Sem sinal de vida há %.0fs — reiniciando o orquestrador.", silent_for
                )
                audit("orquestrador_travado", silencio_s=round(silent_for, 1))
                self._restarts += 1
                self._terminate_child()
                self._listening.set()   # não deixar a escuta pausada para sempre
                self._spawn_orchestrator()

    def _terminate_child(self) -> None:
        child, self._child = self._child, None
        if child is None:
            return
        try:
            child.terminate()
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Orquestrador não encerrou; forçando.")
            child.kill()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                logger.error("Não consegui encerrar o orquestrador.")
        except OSError as exc:
            logger.warning("Erro ao encerrar o orquestrador: %s", exc)

    # ------------------------------------------------------------ escuta

    def _start_hotkey(self):
        """No modo atalho, a tecla faz o papel da palavra de ativação.

        Reaproveita o mesmo `GlobalHotkey` do kill switch — mecanismo já
        provado, e uma dependência a menos do que registrar outro.
        """
        if not self.usa_atalho:
            return None
        from james.hotkey.killswitch import GlobalHotkey, HotkeyError

        try:
            atalho = GlobalHotkey(self.porcupine.atalho, self._pedir_escuta)
        except HotkeyError as exc:
            logger.error(
                "Não consegui registrar %s: %s. Sem palavra de ativação e sem "
                "atalho, o James não tem como ser chamado.",
                self.porcupine.atalho, exc,
            )
            return None
        if not atalho.start():
            return None
        logger.info("Aperte %s para falar com o James.", self.porcupine.atalho)
        return atalho

    def _pedir_escuta(self) -> None:
        """Chamado pela thread do atalho, de fora do laço principal."""
        if not self._listening.is_set():
            logger.debug("Atalho ignorado: o James já está ouvindo ou falando.")
            return
        audit("atalho_de_voz")
        self._listening.clear()
        if not self.server.send({"type": "wake"}):
            logger.warning("Orquestrador não está conectado; ignorando o atalho.")
            self._listening.set()

    @property
    def usa_atalho(self) -> bool:
        return isinstance(self.porcupine, HotkeyEngine)

    def _listen_loop(self) -> None:
        if self.usa_atalho:
            # O ponto do modo atalho: o microfone NUNCA abre sozinho. Sem laço,
            # sem inferência, sem CPU — a thread só espera o encerramento.
            logger.info("Modo atalho: aperte %s para falar.", self.porcupine.atalho)
            while self._running.is_set():
                self._running.wait(timeout=1.0)
            return

        import numpy as np

        # `self.frame_bytes`, não `audio_format.frame_bytes`: ver o comentário
        # no __init__ sobre o arredondamento em milissegundos.
        rechunker = _FrameRechunker(self.frame_bytes)
        device = self.config.get("audio.input_device")

        while self._running.is_set():
            if not self._listening.is_set():
                # Microfone fechado enquanto o orquestrador o usa ou fala.
                rechunker.reset()
                self._listening.wait(timeout=0.5)
                continue

            try:
                with MicrophoneStream(self.audio_format, device=device) as mic:
                    logger.info("Escutando a palavra de ativação.")
                    while self._running.is_set() and self._listening.is_set():
                        chunk = mic.read(timeout=0.5)
                        if chunk is None:
                            continue
                        for frame in rechunker.feed(chunk):
                            samples = np.frombuffer(frame, dtype=np.int16)
                            if self.porcupine.process(samples) < 0:
                                continue
                            logger.info("Palavra de ativação detectada.")
                            audit("wake_word")
                            # Fecha o microfone ANTES de avisar: o orquestrador
                            # precisa dele livre para gravar o comando.
                            self._listening.clear()
                            rechunker.reset()
                            break
                        if not self._listening.is_set():
                            break
            except AudioDeviceError as exc:
                logger.error("Microfone indisponível: %s. Nova tentativa em 5s.", exc)
                time.sleep(5.0)
                continue

            if not self._listening.is_set() and self._running.is_set():
                # O microfone já foi liberado pelo `with` acima.
                if not self.server.send({"type": "wake"}):
                    logger.warning("Orquestrador não está conectado; ignorando ativação.")
                    self._listening.set()

    # ------------------------------------------------------------ ciclo de vida

    def run(self) -> int:
        try:
            self.server.start()
        except PortUnavailable as exc:
            logger.error("%s", exc)
            return 1

        self._running.set()
        self._spawn_orchestrator()

        watchdog = threading.Thread(target=self._watchdog_loop, name="watchdog", daemon=True)
        watchdog.start()

        self._hotkey = self._start_hotkey()

        try:
            self._listen_loop()
        except KeyboardInterrupt:
            logger.info("Interrompido pelo usuário.")
        finally:
            self.stop()
        return 0

    def stop(self) -> None:
        self._running.clear()
        self._listening.set()
        self.server.send({"type": "shutdown"})
        self._terminate_child()
        if self._hotkey is not None:
            self._hotkey.stop()
        self.server.stop()
        try:
            self.porcupine.delete()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Erro ao liberar o Porcupine: %s", exc)
        logger.info("Wake listener encerrado.")


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuração inválida: {exc}", file=sys.stderr)
        return 2

    setup_logging(
        log_dir=config.get("logs.dir", "logs"),
        level=str(config.get("logs.level", "INFO")),
        audit_file=config.get("logs.audit_file", "logs/audit.jsonl"),
        process_name="wake",
    )

    try:
        listener = WakeListener(config)
    except WakeWordUnavailable as exc:
        logger.error("%s", exc)
        return 3

    return listener.run()


if __name__ == "__main__":
    raise SystemExit(main())
