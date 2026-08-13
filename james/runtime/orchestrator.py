"""Processo 2 — máquina de estados do James.

Fluxo de um turno, com os papéis separados (ver james/llm/roles.py):

    wake (do processo 1, que já liberou o microfone)
      -> LISTENING   grava o comando com VAD
      -> PERCEPÇÃO   Gemini transcreve o áudio                 [1 req. barata]
      -> roteador local casa o comando?  -> executa            [0 req.]
      -> RACIOCÍNIO  OpenRouter decide e responde              [1 req.]
           +-- texto      -> SPEAKING, Piper por sentença em streaming
           +-- tool call  -> guard determinístico
                  |-- Nível 1 -> executa; se fire-and-forget, frase pronta
                  +-- Nível 2 -> CONFIRMING, transcrição LOCAL + lista fixa
      -> retoma a escuta da palavra de ativação

Ter a transcrição ANTES do raciocínio é o que faz o roteador local valer a
pena: comandos frequentes são resolvidos sem gastar a requisição cara.

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

from james.llm.router import LocalRouter
from james.logs import audit, get_logger, setup_logging
from james.memory import MemoryStore
from james.memory.fact_store import FactStore
from james.modes import GestureActions, build_manager as build_modes
from james.skills import SkillRegistry
from james.permissions.confirm import Confirmation, ConfirmationMatcher
from james.permissions.guard import Decision, Guard
from james.security.pin import PinStore
from james.security.sanitizer import sanitize_external
from james.state.ipc import IpcClient
from james.state.runtime_state import RuntimeState
from james.system_prompt import build_system_prompt, first_run_instruction, greeting_instruction
from james.tools import build_registry
from james.ui import UiState, build_interface

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
        self.conversation = Conversation(
            max_turns=int(config.get("behavior.history_turns", 12))
        )
        self.runtime_state = RuntimeState(config.root / "state" / "runtime_state.json")
        self.memory = MemoryStore(
            directory=config.resolve_path("memory.dir", "memories"),
            max_chars=int(config.get("memory.max_chars", 4000)),
        )

        self.facts = FactStore(
            config.resolve_path("memory.fatos_db", "state/fatos.db"),
            max_fatos=int(config.get("memory.max_fatos", 5000)),
        )
        self.skills = SkillRegistry(config.resolve_path("skills.dir", "skills"))

        # Modos e ferramentas se referenciam em círculo: as ferramentas de modo
        # precisam do gerente, e um gesto precisa executar ferramentas. O nó se
        # desfaz com `_on_gesto`, que só resolve `self.gesture_actions` na hora
        # em que um gesto acontece — muito depois de ambos existirem.
        self.modes = build_modes(config, on_acao=self._on_gesto)
        self.registry = build_registry(
            config,
            self.guard,
            self.memory,
            facts=self.facts,
            skills=self.skills,
            modes=self.modes,
        )
        self.gesture_actions = GestureActions(
            self.guard,
            self.registry,
            on_parar=self.cancel,
            on_pausar=self._toggle_pause,
            on_desligar_modo=lambda: self.modes.desligar("gestos"),
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
        self._greeted = False

        self.llm = LLMClient(
            config=config,
            system_prompt=build_system_prompt(config, self.memory.snapshot()),
            tools=self.registry.schemas(),
        )
        self.registry.attach_llm(self.llm)
        # A sequência confirma passos de risco e relata progresso pelos
        # mesmos caminhos que um turno normal usa.
        self.registry.attach_callbacks(
            confirm=self._ask_confirmation,
            progress=self._progresso_do_plano,
            agent_progress=self._progresso_do_agente,
        )

        self.pin_store = PinStore(config.root / "state" / "pin.json")

        self.interface = None
        self.tray = None
        self.screen_grabber = None
        self.confirm_dialog = None
        self.hotkey: GlobalHotkey | None = None
        self.ipc: IpcClient | None = None

        self._events: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._running = threading.Event()
        self._cancelled = threading.Event()
        self._paused = False
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
            # Sem STT o James ainda conversa (a percepção acontece na nuvem),
            # mas perde o modo offline e a confirmação de risco.
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

        from james.ui.tray import Tray

        app = QApplication.instance() or QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)   # fechar a janela não encerra o James

        # Criado aqui, na thread da interface: é dela que o Qt permite capturar
        # a tela, e o objeto precisa pertencer a essa thread para o sinal ser
        # entregue no lugar certo.
        from james.ui.capture import ScreenGrabber
        from james.ui.confirm_dialog import ConfirmDialogBridge

        app_name = str(self.config.get("persona.nome", "James"))
        self.screen_grabber = ScreenGrabber()
        self.registry.attach_screen_grabber(self.screen_grabber)
        self.confirm_dialog = ConfirmDialogBridge(app_name=app_name)
        self.interface = build_interface(self.config, app_name)
        if self.interface is not None:
            self.interface.cancel_requested.connect(self.cancel)
            self.interface.toggle_listening.connect(self._toggle_pause)

        self.tray = Tray(app_name=app_name)
        self.tray.toggle_listening.connect(self._toggle_pause)
        self.tray.cancel_requested.connect(self.cancel)
        self.tray.quit_requested.connect(app.quit)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self._start_audio_output()
        self._start_hotkey()
        self._start_ipc()

        self._running.set()
        self._worker = threading.Thread(target=self._loop, name="pipeline", daemon=True)
        self._worker.start()
        threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True).start()

        self._publish_quota()
        app.aboutToQuit.connect(self.shutdown)
        logger.info("James pronto. Diga a palavra de ativação.")
        return app.exec()

    def _on_tray_activated(self, reason) -> None:
        """Clique no ícone mostra a janela (no modo janela)."""
        from PySide6.QtWidgets import QSystemTrayIcon

        if reason != QSystemTrayIcon.Trigger or self.interface is None:
            return
        if hasattr(self.interface, "showNormal"):
            self.interface.showNormal()
            self.interface.raise_()

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
                str(self.config.get("killswitch.hotkey", "ctrl+alt+j")), self.panic
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
        """Sinal de vida para o watchdog do processo 1."""
        interval = max(1.0, float(self.config.get("ipc.heartbeat_s", 5)))
        while self._running.is_set():
            if self.ipc is not None:
                self.ipc.send({"type": "heartbeat"})
            time.sleep(interval)

    def _on_gesto(self, gesto: str, acao: str) -> None:
        """Chamado pela thread do modo de gestos, nunca pelo laço principal."""
        self.gesture_actions.executar(gesto, acao)

    def shutdown(self) -> None:
        logger.info("Encerrando o James.")
        self._running.clear()
        self._events.put(_SHUTDOWN)
        # Antes de qualquer outra coisa: soltar câmera e afins. Se o
        # encerramento falhar mais adiante, o hardware já está livre.
        self.modes.desligar_todos()
        if self.speaker is not None:
            self.speaker.stop()
        if self.player is not None:
            self.player.close()
        if self.hotkey is not None:
            self.hotkey.stop()
        if self.ipc is not None:
            self.ipc.send({"type": "bye"})
            self.ipc.stop()
        self.facts.close()
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

    def panic(self) -> None:
        """O que a tecla de pânico faz: cancelar E soltar todo o hardware.

        É mais forte que `cancel` de propósito. Quem aperta Ctrl+Alt+J com a
        câmera ligada quer a luz apagando, não só o James calando a boca. O
        gesto de "parar" continua chamando só `cancel` — senão um punho
        acidental desligaria o próprio modo de gestos, e a única forma de
        voltar seria pela voz.
        """
        self.cancel()
        desligados = self.modes.desligar_todos()
        if desligados:
            logger.info("Kill switch desligou os modos: %s", ", ".join(desligados))

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self.tray is not None:
            self.tray.set_listening(not self._paused)
        if self.interface is not None:
            self.interface.set_listening(not self._paused)
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
                    self._publish_quota()
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

        transcript = self._perceive(pcm)
        if not transcript:
            logger.info("Nada inteligível no comando.")
            self._say("Não consegui entender, senhor.")
            return

        logger.info("Comando: %s", transcript)
        audit("comando", texto=transcript)
        self._show_transcript("voce", transcript)
        self.conversation.add_user_text(transcript)

        # Roteador local: comandos frequentes não gastam a requisição cara.
        match = self.router.match(transcript)
        if match is not None:
            logger.info("Atendido localmente: %s", match.tool)
            audit("roteador_local", tool=match.tool, args=match.args)
            self._execute_one(
                ToolCall(name=match.tool, args=match.args), spoke_already=False
            )
            return

        try:
            self._reason_turn(transcript)
        except NoProviderAvailable as exc:
            logger.warning("Sem provedor de raciocínio: %s", exc)
            self._degraded_turn()

    def _perceive(self, pcm: bytes) -> str | None:
        """PERCEPÇÃO — áudio para texto, com queda para o STT local.

        A nuvem é preferida porque é muito mais rápida numa CPU modesta; o
        whisper.cpp entra quando ela não está disponível, que é exatamente o
        caso em que o James precisa continuar funcionando sozinho.
        """
        wav = pcm_to_wav(pcm, self.audio_format)
        transcript = self.llm.transcribe(wav)
        if transcript:
            return transcript

        if self.stt is None:
            return None
        logger.info("Percepção na nuvem indisponível; usando whisper.cpp local.")
        return self.stt.transcribe(pcm)

    def _reason_turn(self, transcript: str) -> None:
        """RACIOCÍNIO — decide o que fazer e responde."""
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

        response = self.llm.reason(
            self.conversation,
            text=transcript,
            instruction=self._turn_instruction(),
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
        if model_text:
            self._show_transcript("james", model_text)

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
            response = self.llm.reason(
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
        if response.text:
            self._show_transcript("james", response.text)

    # ------------------------------------------------------------ confirmação

    def _ask_confirmation(self, question: str) -> bool:
        """Nível 2 — decisão local e determinística, sem LLM (C2).

        Dois caminhos, e ambos negam por padrão:

        - por VOZ: grava, transcreve LOCALMENTE (nunca na nuvem — esta é a
          última barreira antes de algo irreversível e não pode depender de
          rede) e casa contra listas fixas de palavras;
        - por DIÁLOGO: uma janela com Confirmar/Cancelar, e o campo de PIN
          quando ele está configurado.

        O diálogo existe porque a voz depende do whisper.cpp: sem ele, TODA
        ação de Nível 2 seria recusada. Ele também é mais privado — digitar um
        PIN não avisa a sala inteira.
        """
        config = self.config.section("permissions.confirm")
        mode = str(config.get("mode", "auto")).lower()
        pin_required = self.pin_store is not None and self.pin_store.configured

        self._set_ui(UiState.CONFIRMING)

        # Com PIN configurado, a prova é digitada: a voz não tem como fornecê-la.
        if pin_required or mode == "dialogo" or self.stt is None:
            if self.stt is None and mode != "dialogo" and not pin_required:
                logger.info("Sem transcrição local; confirmando pela janela.")
            return self._confirm_by_dialog(question, pin_required)

        decision = self._confirm_by_voice(question, config)
        if decision is Confirmation.YES:
            return True
        if decision is Confirmation.NO:
            return False

        # Voz inconclusiva: a janela é a última chance, e ela também nega por
        # padrão (foco em Cancelar, timeout nega).
        logger.info("Confirmação por voz inconclusiva; abrindo a janela.")
        return self._confirm_by_dialog(question, pin_required)

    def _confirm_by_voice(self, question: str, config: dict) -> Confirmation:
        attempts = max(1, int(config.get("max_attempts", 2)))
        timeout_s = float(config.get("timeout_s", 10))

        for attempt in range(attempts):
            prompt = question if attempt == 0 else "Não entendi. Confirma ou cancela?"
            self._say(prompt)
            if self._cancelled.is_set():
                return Confirmation.NO

            pcm = self._record(max_wait_ms=int(timeout_s * 1000))
            if pcm is None:
                audit("confirmacao_voz", resultado="sem_resposta", tentativa=attempt + 1)
                continue

            transcript = self.stt.transcribe(pcm)
            decision = self.confirm_matcher.classify(transcript)
            audit(
                "confirmacao_voz",
                resultado=decision.value,
                transcricao=transcript,
                tentativa=attempt + 1,
            )
            if transcript:
                self._show_transcript("voce", transcript)
            if decision in (Confirmation.YES, Confirmation.NO):
                return decision

        return Confirmation.AMBIGUOUS

    def _confirm_by_dialog(self, question: str, require_pin: bool) -> bool:
        if self.confirm_dialog is None:
            logger.error("Sem interface para confirmar; negando por segurança.")
            audit("confirmacao_impossivel", motivo="sem_dialogo")
            self._say("Não consigo confirmar isso agora, senhor, então não vou executar.")
            return False

        self._say(question)
        aprovado, pin = self.confirm_dialog.ask(question, require_pin=require_pin)

        if not aprovado:
            audit("confirmacao_dialogo", resultado="negado")
            return False
        if require_pin and not self.pin_store.verify(pin):
            # Não diz se o PIN errou por tamanho ou por valor.
            audit("confirmacao_dialogo", resultado="pin_incorreto")
            self._say("PIN incorreto, senhor.")
            return False

        audit("confirmacao_dialogo", resultado="aprovado", com_pin=require_pin)
        return True

    # ------------------------------------------------------------ degradado

    def _degraded_turn(self) -> None:
        """Sem raciocínio disponível: sobra o roteador local (A5).

        Chegar aqui significa que o roteador já não casou o comando lá em cima,
        então não há o que executar — resta ser honesto sobre a limitação.
        """
        self._announce_service_state()
        self._set_ui(UiState.ERROR)
        self._say(
            "Estou com capacidade limitada no momento, senhor. "
            "Só consigo executar comandos diretos."
        )

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

    # ------------------------------------------------------------- interface

    def _set_ui(self, state: UiState) -> None:
        degraded = self.llm.state.degraded
        if self.interface is not None:
            if state is UiState.HIDDEN:
                self.interface.request_hide.emit()
            else:
                self.interface.request_state.emit(state.value, degraded)
        if self.tray is not None:
            self.tray.set_state(state, degraded)

    def _progresso_do_plano(self, indice: int, total: int, rotulo: str) -> None:
        """Mostra em que passo a sequência está, sem falar em voz alta.

        Narrar cada passo tornaria uma sequência de cinco itens insuportável de
        ouvir; a janela mostra, a voz só resume no fim.
        """
        self._set_ui(UiState.EXECUTING)
        self._show_caption(f"Passo {indice} de {total}: {rotulo}")

    def _progresso_do_agente(self, perfil: str, etapa: str) -> None:
        """Mostra o especialista trabalhando, sem narrar em voz alta."""
        self._set_ui(UiState.EXECUTING)
        self._show_caption(f"Especialista {perfil}: {etapa}")

    def _show_caption(self, text: str) -> None:
        if self.interface is not None:
            self.interface.request_caption.emit(text)

    def _show_transcript(self, who: str, text: str) -> None:
        if self.interface is not None:
            self.interface.request_transcript.emit(who, text)

    def _publish_quota(self) -> None:
        if self.interface is not None:
            self.interface.request_quota.emit(self.llm.quota_summary())

    def _say(self, text: str) -> None:
        """Fala uma frase pronta do próprio James (não vinda do modelo)."""
        if not text:
            return
        self._set_ui(UiState.SPEAKING)
        self._show_caption(text)
        self._show_transcript("james", text)
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

        A saudação viaja JUNTO com o comando, na mesma requisição, em vez de
        virar uma chamada só para cumprimentar. Assim ela não custa requisição
        nem atraso — que era exatamente o motivo de a pesquisa original querer
        gerá-la "em paralelo" com a escuta.
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
