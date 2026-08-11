"""Processo 2 — máquina de estados do James.

Fluxo de um turno:

    wake (do processo 1, que já liberou o microfone)
      -> LISTENING   grava o comando com VAD
      -> THINKING    manda o ÁUDIO direto ao Gemini (1 requisição)
           |
           +-- texto     -> SPEAKING, sintetizando por sentença em streaming
           +-- tool call -> guard determinístico
                              |-- Nível 1 -> executa
                              |     e, se for fire-and-forget, fala a frase
                              |     pronta sem voltar à API (1 requisição)
                              +-- Nível 2 -> CONFIRMING, com transcrição LOCAL
                                    e casamento por lista fixa de palavras
      -> retoma a escuta da palavra de ativação

Threads: a interface Qt vive na thread principal; este pipeline roda numa thread
de trabalho e conversa com a interface só por sinais.
"""

from __future__ import annotations

import queue
import sys
import threading
import time

from james.audio.capture import AudioDeviceError, MicrophoneStream
from james.audio.vad import RecorderState, SpeechRecorder, VadSettings
from james.audio.wav import pcm_to_wav
from james.config import Config, ConfigError, load_config
from james.hotkey.killswitch import GlobalHotkey, HotkeyError
from james.llm.base import NoProviderAvailable
from james.llm.client import LLMClient, ServiceState
from james.llm.history import Conversation, ToolCall
from james.llm.rate_limiter import RateLimiter
from james.llm.router import LocalRouter
from james.logs import audit, get_logger, setup_logging
from james.permissions.confirm import Confirmation, ConfirmationMatcher
from james.permissions.guard import Decision, Guard
from james.security.sanitizer import sanitize_external
from james.state.ipc import IpcClient
from james.state.runtime_state import RuntimeState
from james.system_prompt import build_system_prompt, first_run_instruction, greeting_instruction
from james.tools import build_registry
from james.ui.states import UiState

logger = get_logger("james.orchestrator")

_WAKE = "wake"
_CANCEL = "cancel"
_SHUTDOWN = "shutdown"

# Teto de tempo de parede para uma gravação, mesmo que o VAD nunca conclua
# (microfone mudo, driver travado).
_RECORD_WALL_CLOCK_S = 30.0


class Orchestrator:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.audio_format = config.audio

        self.guard = Guard(config)
        self.registry = build_registry(config, self.guard)
        self.conversation = Conversation(
            max_turns=int(config.get("behavior.history_turns", 12))
        )
        self.runtime_state = RuntimeState(config.root / "state" / "runtime_state.json")

        self.rate_limiter = RateLimiter(
            requests_per_minute=int(config.get("llm.rate_limit.requests_per_minute", 10)),
            requests_per_day=int(config.get("llm.rate_limit.requests_per_day", 240)),
            state_path=config.root / "state" / "usage_counters.json",
        )
        self.router = LocalRouter(
            self.guard.apps, enabled=bool(config.get("llm.local_router.enabled", True))
        )
        confirm_config = config.section("permissions.confirm")
        self.confirm_matcher = ConfirmationMatcher(
            yes_words=list(confirm_config.get("yes_words", [])),
            no_words=list(confirm_config.get("no_words", [])),
        )

        self.stt = self._build_stt()
        self.tts = self._build_tts()
        self.player = None
        self.speaker = None

        # Precisa existir antes do LLMClient: ele guarda uma referência a este
        # atributo para o fallback em texto.
        self._last_transcript: str | None = None
        self._greeted = False

        self.llm = LLMClient(
            config=config,
            system_prompt=build_system_prompt(config),
            tools=self.registry.schemas(),
            rate_limiter=self.rate_limiter,
            transcribe_fallback=lambda: self._last_transcript,
        )

        self.overlay = None
        self.tray = None
        self.hotkey: GlobalHotkey | None = None
        self.ipc: IpcClient | None = None

        self._events: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._running = threading.Event()
        self._cancelled = threading.Event()
        self._paused = False
        self._transcription_thread: threading.Thread | None = None
        self._announced_state: ServiceState | None = None

    # ------------------------------------------------------------ construção

    def _build_stt(self):
        from james.voice.stt import STTUnavailable, WhisperCppSTT

        try:
            return WhisperCppSTT(
                binary=self.config.resolve_path("stt.binary"),
                model=self.config.resolve_path("stt.model"),
                audio_format=self.audio_format,
                language=str(self.config.get("stt.language", "pt")),
                timeout_s=int(self.config.get("stt.timeout_s", 60)),
                threads=int(self.config.get("stt.threads", 4)),
            )
        except STTUnavailable as exc:
            # Sem STT o James ainda conversa (o áudio vai direto ao Gemini), mas
            # perde o modo offline e a confirmação de risco. Isso precisa ser
            # ruidoso, não silencioso.
            logger.warning("Transcrição local indisponível: %s", exc)
            return None

    def _build_tts(self):
        from james.voice.tts import PiperTTS, TTSUnavailable

        try:
            return PiperTTS(
                voice_path=self.config.resolve_path("tts.voice_path"),
                binary=self.config.resolve_path("tts.binary"),
                length_scale=float(self.config.get("tts.length_scale", 1.0)),
            )
        except TTSUnavailable as exc:
            logger.error("Piper indisponível: %s — o James ficará mudo.", exc)
            return None

    # ------------------------------------------------------------ ciclo de vida

    def run(self) -> int:
        """Sobe a interface Qt e o pipeline. Bloqueia até o James encerrar."""
        from PySide6.QtWidgets import QApplication

        from james.ui.overlay import Overlay
        from james.ui.tray import Tray

        app = QApplication.instance() or QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)   # o overlay some, o James continua

        if bool(self.config.get("overlay.enabled", True)):
            self.overlay = Overlay(
                size=int(self.config.get("overlay.size", 260)),
                position=str(self.config.get("overlay.position", "bottom-right")),
                margin=int(self.config.get("overlay.margin", 40)),
                fps=int(self.config.get("overlay.fps", 30)),
            )

        self.tray = Tray(app_name=str(self.config.get("persona.nome", "James")))
        self.tray.toggle_listening.connect(self._toggle_pause)
        self.tray.cancel_requested.connect(self.cancel)
        self.tray.quit_requested.connect(app.quit)
        self.tray.show()

        self._start_audio_output()
        self._start_hotkey()
        self._start_ipc()

        self._running.set()
        self._worker = threading.Thread(target=self._loop, name="pipeline", daemon=True)
        self._worker.start()
        threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True).start()

        app.aboutToQuit.connect(self.shutdown)
        logger.info("James pronto. Diga a palavra de ativação.")
        return app.exec()

    def _start_audio_output(self) -> None:
        from james.audio.player import AudioPlayer
        from james.voice.speaker import Speaker

        self.player = AudioPlayer(device=self.config.get("audio.output_device"))
        if self.tts is not None:
            self.speaker = Speaker(
                tts=self.tts, player=self.player, on_sentence=self._show_caption
            )
            # Prewarm: sem isto, a primeira ativação do dia paga a carga do
            # modelo justamente quando o usuário está esperando.
            threading.Thread(target=self.tts.prewarm, name="prewarm", daemon=True).start()

    def _start_hotkey(self) -> None:
        if not bool(self.config.get("killswitch.enabled", True)):
            return
        try:
            self.hotkey = GlobalHotkey(
                str(self.config.get("killswitch.hotkey", "ctrl+alt+j")), self.cancel
            )
        except HotkeyError as exc:
            logger.error("Kill switch não configurado: %s", exc)
            self.hotkey = None
            return
        if not self.hotkey.start():
            self.hotkey = None

    def _start_ipc(self) -> None:
        self.ipc = IpcClient(
            host=str(self.config.get("ipc.host", "127.0.0.1")),
            port=int(self.config.get("ipc.port", 47821)),
            on_message=self._on_ipc_message,
        )
        self.ipc.start()

    def _heartbeat_loop(self) -> None:
        """Sinal de vida para o watchdog do processo 1.

        Iniciado só depois de `_running`, senão a condição de parada leria o
        estado de antes da subida e o laço nunca terminaria.
        """
        interval = max(1.0, float(self.config.get("ipc.heartbeat_s", 5)))
        while self._running.is_set():
            if self.ipc is not None:
                self.ipc.send({"type": "heartbeat"})
            time.sleep(interval)

    def shutdown(self) -> None:
        logger.info("Encerrando o James.")
        self._running.clear()
        self._events.put(_SHUTDOWN)
        if self.speaker is not None:
            self.speaker.stop()
        if self.player is not None:
            self.player.close()
        if self.hotkey is not None:
            self.hotkey.stop()
        if self.ipc is not None:
            self.ipc.send({"type": "bye"})
            self.ipc.stop()
        if self._worker is not None:
            self._worker.join(timeout=3.0)

    # ---------------------------------------------------------------- eventos

    def _on_ipc_message(self, message: dict) -> None:
        kind = message.get("type")
        if kind == _WAKE:
            self._events.put(_WAKE)
        elif kind == _SHUTDOWN:
            self._events.put(_SHUTDOWN)

    def cancel(self) -> None:
        """Kill switch: para tudo agora, de qualquer thread."""
        logger.info("Cancelamento solicitado.")
        audit("kill_switch")
        self._cancelled.set()
        if self.speaker is not None:
            self.speaker.stop()
        if self.player is not None:
            self.player.stop()
        self._events.put(_CANCEL)

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self.tray is not None:
            self.tray.set_listening(not self._paused)
        self._send_ipc("pause_wake" if self._paused else "resume_wake")
        logger.info("Escuta %s.", "pausada" if self._paused else "retomada")

    def _send_ipc(self, kind: str) -> None:
        if self.ipc is not None:
            self.ipc.send({"type": kind})

    # ------------------------------------------------------------ laço principal

    def _loop(self) -> None:
        while self._running.is_set():
            try:
                event = self._events.get(timeout=0.5)
            except queue.Empty:
                continue

            if event == _SHUTDOWN:
                return
            if event == _CANCEL:
                self._set_ui(UiState.HIDDEN)
                self._resume_wake()
                self._cancelled.clear()
                continue
            if event == _WAKE:
                self._cancelled.clear()
                try:
                    self._handle_wake()
                except Exception:  # noqa: BLE001 — um turno ruim não derruba o James
                    logger.exception("Erro inesperado durante o turno.")
                    self._say("Tive um problema interno, senhor.")
                finally:
                    self._set_ui(UiState.HIDDEN)
                    self._resume_wake()

    # ----------------------------------------------------------------- turno

    def _handle_wake(self) -> None:
        if self._paused:
            return

        audit("wake_detectado")
        self._set_ui(UiState.LISTENING)
        pcm = self._record()
        if pcm is None or self._cancelled.is_set():
            logger.info("Nenhum comando capturado.")
            return

        # Com barge-in ligado, a escuta volta já — a palavra de ativação durante
        # a fala vira interrupção. Desligado (padrão), fica half-duplex: o
        # microfone só reabre depois que o James terminar de falar (C3).
        if bool(self.config.get("behavior.barge_in", False)):
            self._resume_wake()

        self._set_ui(UiState.THINKING)
        history_index = self.conversation.add_user_audio()
        self._start_background_transcription(pcm, history_index)

        try:
            self._llm_turn(pcm)
        except NoProviderAvailable as exc:
            logger.warning("Sem provedor de LLM: %s", exc)
            self._degraded_turn(pcm)

    def _llm_turn(self, pcm: bytes) -> None:
        wav = pcm_to_wav(pcm, self.audio_format)
        speaker = self.speaker
        if speaker is not None:
            speaker.begin()

        started_speaking = threading.Event()

        def on_text(chunk: str) -> None:
            if not started_speaking.is_set() and chunk.strip():
                started_speaking.set()
                self._set_ui(UiState.SPEAKING)
            if speaker is not None:
                speaker.feed(chunk)

        response = self.llm.respond(
            self.conversation,
            audio_wav=wav,
            text=self._turn_instruction(),
            on_text=on_text,
        )
        self._announce_service_state()

        spoken_ok = True
        if speaker is not None:
            spoken_ok = speaker.finish()

        # Se a fala foi interrompida, o histórico registra o que o usuário
        # realmente ouviu — não o que o modelo chegou a gerar.
        model_text = response.text if spoken_ok else (speaker.spoken_text if speaker else "")
        self.conversation.add_model_response(model_text, response.tool_calls)

        if response.tool_calls:
            self._run_tool_calls(response.tool_calls, spoke_already=started_speaking.is_set())

    # -------------------------------------------------------------- tools

    def _run_tool_calls(self, calls: list[ToolCall], spoke_already: bool) -> None:
        """Executa as chamadas pedidas pelo modelo, todas passando pelo guard."""
        max_iterations = max(1, int(self.config.get("behavior.max_tool_iterations", 5)))
        needs_second_round = False

        for index, call in enumerate(calls):
            if self._cancelled.is_set():
                return
            if index >= max_iterations:
                logger.warning("Teto de %d tools por turno atingido.", max_iterations)
                self._say("São ações demais de uma vez, senhor. Vamos por partes.")
                return

            if self._execute_one(call, spoke_already):
                needs_second_round = True
            spoke_already = True

        if needs_second_round:
            self._second_round()

    def _execute_one(self, call: ToolCall, spoke_already: bool) -> bool:
        """Avalia e executa uma chamada. Devolve True se exige volta à API."""
        verdict = self.guard.evaluate(call.name, call.args)
        audit(
            "guard",
            tool=call.name,
            decisao=verdict.decision.value,
            motivo=verdict.reason,
            args=call.args,
        )

        if verdict.decision is Decision.BLOCK:
            logger.info("Bloqueado: %s (%s)", call.name, verdict.reason)
            self._say(verdict.spoken)
            self.conversation.add_tool_result(
                call.name, {"status": "bloqueado", "motivo": verdict.reason}, call.call_id
            )
            return False

        if verdict.decision is Decision.CONFIRM:
            if not self._ask_confirmation(verdict.spoken):
                self._say("Cancelado, senhor.")
                self.conversation.add_tool_result(
                    call.name, {"status": "cancelado_pelo_usuario"}, call.call_id
                )
                return False

        self._set_ui(UiState.EXECUTING)
        result = self.registry.execute(call.name, verdict.args)
        tool = self.registry.get(call.name)

        # O que falar: `speech` carrega informação que o modelo não tem e é
        # sempre dita; `ack` é só a confirmação genérica, usada quando ninguém
        # mais disse nada.
        if result.speech:
            self._say(result.speech)
        elif not spoke_already and result.ack:
            self._say(result.ack)

        payload = result.data if result.data is not None else {"status": "ok"}
        if result.external_content:
            payload = sanitize_external(
                payload,
                origin=call.name,
                max_chars=int(self.config.get("security.max_external_chars", 4000)),
            )
        self.conversation.add_tool_result(call.name, payload, call.call_id)

        # Fire-and-forget: resultado previsível, frase já dita, não volta à API.
        return bool(tool is not None and not tool.fire_and_forget and result.ok)

    def _second_round(self) -> None:
        """Volta à API para interpretar um resultado imprevisível."""
        speaker = self.speaker
        if speaker is not None:
            speaker.begin()
        try:
            response = self.llm.respond(
                self.conversation,
                text="Relate o resultado ao usuário em uma ou duas frases.",
                on_text=speaker.feed if speaker is not None else None,
            )
        except NoProviderAvailable:
            if speaker is not None:
                speaker.stop()
            self._say("Consegui os dados, mas não estou conseguindo resumir agora.")
            return

        self._set_ui(UiState.SPEAKING)
        if speaker is not None:
            speaker.finish()
        self.conversation.add_model_response(response.text, response.tool_calls)

    # ------------------------------------------------------------ confirmação

    def _ask_confirmation(self, question: str) -> bool:
        """Nível 2 — decisão local e determinística, sem LLM (C2).

        Toda saída que não for um "sim" explícito nega: resposta ambígua,
        silêncio, timeout e falha de transcrição levam ao mesmo lugar.
        """
        if self.stt is None:
            # Sem transcrição local não há como confirmar com segurança, e
            # aceitar sem confirmar seria exatamente o buraco que o Nível 2
            # existe para fechar.
            logger.error("Confirmação impossível: transcrição local indisponível.")
            self._say(
                "Não consigo confirmar isso por voz agora, senhor, então não vou executar."
            )
            audit("confirmacao_impossivel", motivo="stt_indisponivel")
            return False

        config = self.config.section("permissions.confirm")
        attempts = max(1, int(config.get("max_attempts", 2)))
        timeout_s = float(config.get("timeout_s", 10))

        self._set_ui(UiState.CONFIRMING)
        for attempt in range(attempts):
            prompt = question if attempt == 0 else "Não entendi. Confirma ou cancela?"
            self._say(prompt)
            if self._cancelled.is_set():
                return False

            pcm = self._record(max_wait_ms=int(timeout_s * 1000))
            if pcm is None:
                audit("confirmacao", resultado="sem_resposta", tentativa=attempt + 1)
                continue

            transcript = self.stt.transcribe(pcm)
            decision = self.confirm_matcher.classify(transcript)
            audit(
                "confirmacao",
                resultado=decision.value,
                transcricao=transcript,
                tentativa=attempt + 1,
            )

            if decision is Confirmation.YES:
                return True
            if decision is Confirmation.NO:
                return False

        logger.info("Confirmação não obtida após %d tentativa(s); negando.", attempts)
        return False

    # ------------------------------------------------------------ degradado

    def _degraded_turn(self, pcm: bytes) -> None:
        """Sem LLM: sobra o roteador local sobre a transcrição local (A5)."""
        self._announce_service_state()
        transcript = self._last_transcript
        if transcript is None and self.stt is not None:
            transcript = self.stt.transcribe(pcm)

        match = self.router.match(transcript)
        if match is None:
            self._set_ui(UiState.ERROR)
            self._say(
                "Estou com capacidade limitada no momento, senhor. "
                "Só consigo executar comandos diretos."
            )
            return

        logger.info("Comando atendido localmente: %s", match.tool)
        audit("roteador_local", tool=match.tool, args=match.args, modo="degradado")
        self._execute_one(ToolCall(name=match.tool, args=match.args), spoke_already=False)

    def _announce_service_state(self) -> None:
        """Avisa a mudança de estado uma única vez, não a cada turno."""
        state = self.llm.state
        if state is self._announced_state:
            return
        self._announced_state = state
        if self.tray is not None:
            self.tray.set_state(UiState.HIDDEN, degraded=state.degraded)
        if state is ServiceState.QUOTA:
            self._say("Atingi meu limite de uso por hoje, senhor. Sigo com o essencial.")
        elif state is ServiceState.OFFLINE:
            self._say("Estou sem conexão, senhor. Sigo com comandos diretos.")

    # -------------------------------------------------------------- captura

    def _record(self, max_wait_ms: int | None = None) -> bytes | None:
        """Grava um comando. None = nada utilizável foi capturado."""
        raw = dict(self.config.section("audio.vad"))
        if max_wait_ms is not None:
            raw["max_wait_speech_ms"] = max_wait_ms
        try:
            settings = VadSettings(self.audio_format, raw)
            recorder = SpeechRecorder(self.audio_format, settings)
        except (ValueError, ImportError) as exc:
            logger.error("VAD indisponível: %s", exc)
            return None

        deadline = time.monotonic() + _RECORD_WALL_CLOCK_S
        try:
            with MicrophoneStream(
                self.audio_format, device=self.config.get("audio.input_device")
            ) as mic:
                while True:
                    if self._cancelled.is_set():
                        return None
                    if time.monotonic() > deadline:
                        logger.warning("Gravação excedeu o tempo de parede; abortando.")
                        return None
                    frame = mic.read(timeout=0.5)
                    if frame is None:
                        continue
                    state = recorder.process(frame)
                    if state is RecorderState.DONE:
                        break
                    if state is RecorderState.TIMEOUT:
                        return None
        except AudioDeviceError as exc:
            logger.error("Microfone indisponível: %s", exc)
            self._say("Não consegui acessar o microfone, senhor.")
            return None

        return recorder.audio()

    def _start_background_transcription(self, pcm: bytes, history_index: int) -> None:
        """Transcreve em segundo plano, enquanto o James já está respondendo.

        Serve para dois fins sem custar latência: preencher o histórico com o
        que o usuário realmente disse (senão o modelo perde o contexto de
        perguntas de acompanhamento) e ter o texto pronto caso o fallback
        precise dele.
        """
        self._last_transcript = None
        if self.stt is None:
            return
        if self._transcription_thread is not None and self._transcription_thread.is_alive():
            # Numa CPU sem AVX o whisper é lento; empilhar transcrições só
            # tomaria núcleos do pipeline que importa.
            logger.debug("Transcrição anterior ainda em curso; pulando esta.")
            return

        def work() -> None:
            try:
                text = self.stt.transcribe(pcm)
            except Exception:  # noqa: BLE001 — auxiliar, nunca crítica
                logger.exception("Transcrição em segundo plano falhou.")
                return
            if text:
                self._last_transcript = text
                self.conversation.set_user_transcript(history_index, text)

        self._transcription_thread = threading.Thread(
            target=work, name="transcricao", daemon=True
        )
        self._transcription_thread.start()

    # ------------------------------------------------------------- interface

    def _set_ui(self, state: UiState) -> None:
        degraded = self.llm.state.degraded
        if self.overlay is not None:
            if state is UiState.HIDDEN:
                self.overlay.request_hide.emit()
            else:
                self.overlay.request_state.emit(state.value, degraded)
        if self.tray is not None:
            self.tray.set_state(state, degraded)

    def _show_caption(self, text: str) -> None:
        if self.overlay is not None:
            self.overlay.request_caption.emit(text)

    def _say(self, text: str) -> None:
        """Fala uma frase pronta do próprio James (não vinda do modelo)."""
        if not text:
            return
        self._set_ui(UiState.SPEAKING)
        self._show_caption(text)
        if self.speaker is None:
            logger.info("[sem voz] %s", text)
            return
        self.speaker.say(text)

    def _resume_wake(self) -> None:
        """Devolve o microfone ao processo 1, respeitando o half-duplex."""
        if self._paused:
            return
        tail_ms = int(self.config.get("behavior.tts_tail_ms", 300))
        if tail_ms > 0:
            # Margem para a cauda do áudio terminar de sair pelo alto-falante
            # antes de o microfone reabrir — senão o James se escuta.
            time.sleep(tail_ms / 1000.0)
        self._send_ipc("resume_wake")

    # ------------------------------------------------------------- saudação

    def _turn_instruction(self) -> str | None:
        """Instrução extra a anexar ao primeiro turno, se houver.

        A saudação viaja JUNTO com o áudio do comando, na mesma requisição, em
        vez de virar uma chamada só para cumprimentar. Assim ela não custa
        requisição nem atraso — que era exatamente o motivo de a pesquisa
        original querer gerá-la "em paralelo" com a escuta.
        """
        if self._greeted:
            return None
        self._greeted = True

        if not self.runtime_state.first_run_done():
            self.runtime_state.mark_first_run_done()
            audit("primeira_execucao")
            return first_run_instruction()
        return greeting_instruction()


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
        max_bytes=int(config.get("logs.max_bytes", 5 * 1024 * 1024)),
        backup_count=int(config.get("logs.backup_count", 3)),
        process_name="orchestrator",
    )
    for warning in config.validate():
        logger.warning(warning)

    orchestrator = Orchestrator(config)
    try:
        return orchestrator.run()
    except KeyboardInterrupt:
        orchestrator.shutdown()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
