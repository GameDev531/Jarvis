"""Fala em streaming: sintetiza e toca por sentença.

O que o usuário percebe como "demorou" é o tempo até a PRIMEIRA palavra, não o
tempo total. Então, em vez de esperar a resposta inteira do modelo, sintetizar
tudo e só então tocar, o Speaker:

  1. recebe os fragmentos de texto conforme o modelo os produz;
  2. assim que uma sentença fica completa, joga na fila de síntese;
  3. uma thread sintetiza e toca em ordem, enquanto o resto do texto ainda
     está chegando.

Na prática, isso corta pela metade a espera percebida em respostas longas.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable

from james.logs import get_logger
from james.voice.sentences import split_sentences, split_stream
from james.voice.tts import PiperTTS, TTSUnavailable

logger = get_logger("james.voice.speaker")

_SENTINEL = object()


class Speaker:
    def __init__(
        self,
        tts: PiperTTS,
        player,
        on_sentence: Callable[[str], None] | None = None,
    ) -> None:
        self.tts = tts
        self.player = player
        self.on_sentence = on_sentence

        self._queue: queue.Queue = queue.Queue()
        self._buffer = ""
        self._buffer_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._interrupted = threading.Event()
        self._drained = threading.Event()
        self._drained.set()
        self._spoken: list[str] = []

    # ------------------------------------------------------------- streaming

    def begin(self) -> None:
        """Inicia uma nova fala. Cancela qualquer coisa ainda tocando."""
        self.stop()
        with self._buffer_lock:
            self._buffer = ""
        self._spoken = []
        self._interrupted.clear()
        self._drained.clear()
        self._queue = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="speaker", daemon=True)
        self._worker.start()

    def feed(self, chunk: str) -> None:
        """Recebe um fragmento de texto do modelo.

        Emite para a fila apenas as sentenças já completas; o resto fica no
        buffer esperando a continuação.
        """
        if not chunk or self._interrupted.is_set():
            return
        with self._buffer_lock:
            self._buffer += chunk
            ready, self._buffer = split_stream(self._buffer)
        for sentence in ready:
            self._queue.put(sentence)

    def finish(self, timeout: float = 60.0) -> bool:
        """Descarrega o resto e espera terminar de falar.

        Devolve False se a fala foi interrompida antes do fim.
        """
        with self._buffer_lock:
            remainder = self._buffer.strip()
            self._buffer = ""
        if remainder and not self._interrupted.is_set():
            for sentence in split_sentences(remainder):
                self._queue.put(sentence)

        self._queue.put(_SENTINEL)
        self._drained.wait(timeout=timeout)
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None
        return not self._interrupted.is_set()

    # ------------------------------------------------------------ uso direto

    def say(self, text: str, timeout: float = 60.0) -> bool:
        """Fala um texto pronto, do começo ao fim. Bloqueia."""
        if not text or not text.strip():
            return True
        self.begin()
        self.feed(text)
        return self.finish(timeout=timeout)

    def stop(self) -> None:
        """Interrompe imediatamente (kill switch, barge-in)."""
        self._interrupted.set()
        self.player.stop()
        self._queue.put(_SENTINEL)
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None
        self._drained.set()

    @property
    def spoken_text(self) -> str:
        """O que efetivamente saiu pelo alto-falante.

        Diferente do texto que o modelo gerou quando a fala foi interrompida —
        e é este que deve ir para o histórico, senão o James "lembra" de ter
        dito coisas que o usuário nunca ouviu.
        """
        return " ".join(self._spoken).strip()

    # --------------------------------------------------------------- interno

    def _run(self) -> None:
        try:
            while True:
                item = self._queue.get()
                if item is _SENTINEL:
                    return
                if self._interrupted.is_set():
                    continue  # esvazia a fila sem tocar
                self._speak_one(str(item))
        finally:
            self._drained.set()

    def _speak_one(self, sentence: str) -> None:
        text = sentence.strip()
        if not text:
            return
        try:
            pcm = self.tts.synthesize(text)
        except TTSUnavailable as exc:
            logger.error("Falha ao sintetizar %r: %s", text[:40], exc)
            return
        if not pcm or self._interrupted.is_set():
            return

        if self.on_sentence is not None:
            try:
                self.on_sentence(text)
            except Exception:  # noqa: BLE001 — legenda não pode quebrar a fala
                logger.exception("Erro no callback de legenda.")

        completed = self.player.play(pcm, self.tts.sample_rate, channels=1)
        if completed:
            self._spoken.append(text)
