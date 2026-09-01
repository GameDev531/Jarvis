"""O fluxo de um run — e a decisão de o que pode ser descartado.

Este arquivo existe por causa de um conflito que só apareceu ao encaixar o
AG-UI no `StateBus`, e que está escrito no topo do próprio barramento:

    "Assinante lento é descartado, nunca espera. (...) o evento mais antigo é
     jogado fora em vez de bloquear a thread do orquestrador."

Descartar o mais antigo é a política CERTA para "mostre o estado atual". Perder
um `estado=PENSANDO` velho não custa nada — o próximo corrige, e o James nunca
pode engasgar porque uma aba parou de ler.

É a política ERRADA para `TEXT_MESSAGE_CONTENT`. O cliente concatena os `delta`
na ordem; um descartado no meio faz a frase chegar **com um pedaço faltando**.
Não é atraso, é corrupção — e silenciosa, porque a tela mostra uma frase que
parece completa.

## A saída: classificar por NATUREZA, não por assinante

    DESCARTÁVEL   estado e atividade. O próximo evento corrige o anterior,
                  então perder um é perder nada.
    ESSENCIAL     ciclo de vida, deltas de mensagem, chamadas de ferramenta.
                  Cada um é único e insubstituível.

Quando a fila enche e o que vai cair é essencial, o run **morre com
`RUN_ERROR`** em vez de continuar entregando texto mutilado. Falhar alto é
melhor que mentir baixo: a pessoa vê "a conexão não acompanhou" e recarrega, em
vez de ler uma resposta pela metade achando que o James respondeu aquilo.

O orquestrador continua sem esperar por ninguém. O que muda é o que acontece
com quem não acompanhou — antes, uma resposta corrompida; agora, um erro
honesto.
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from typing import Any, Iterator

from james.agui import eventos as ev
from james.agui.eventos import TipoEvento
from james.agui.sequencia import OrdemInvalida, Sequencia
from james.logs import get_logger

logger = get_logger("james.agui.fluxo")

# Cabe uma rajada inteira de um turno com folga. Se encher, o consumidor parou.
TAMANHO_DA_FILA = 256

# Perder um destes não perde informação: o próximo evento do mesmo tipo carrega
# o estado inteiro de novo, ou o delta seguinte reconstrói o que faltou.
DESCARTAVEIS = frozenset({
    TipoEvento.STATE_SNAPSHOT.value,
    TipoEvento.STATE_DELTA.value,
    TipoEvento.ACTIVITY_SNAPSHOT.value,
    TipoEvento.ACTIVITY_DELTA.value,
    TipoEvento.MESSAGES_SNAPSHOT.value,
    TipoEvento.CUSTOM.value,
})


def novo_id(prefixo: str) -> str:
    return f"{prefixo}-{uuid.uuid4().hex[:12]}"


class FluxoDeRun:
    """Um run: produz eventos de um lado, entrega ordenados do outro.

    `emitir` é chamado pela thread do orquestrador e NUNCA bloqueia. `ler` é
    chamado pela thread que escreve o SSE.
    """

    def __init__(self, thread_id: str, run_id: str | None = None) -> None:
        self.thread_id = thread_id
        self.run_id = run_id or novo_id("run")
        self.sequencia = Sequencia(self.thread_id, self.run_id)
        self._fila: queue.Queue = queue.Queue(maxsize=TAMANHO_DA_FILA)
        self._lock = threading.Lock()
        self._encerrado = threading.Event()
        self.descartados = 0

    # ------------------------------------------------------------- produção

    def emitir(self, evento: dict[str, Any]) -> bool:
        """Valida a ordem e enfileira. `False` = o evento não entrou.

        Nunca bloqueia: a thread do orquestrador não pode parar porque uma aba
        parou de ler.
        """
        with self._lock:
            if self._encerrado.is_set():
                return False
            try:
                self.sequencia.validar(evento)
            except OrdemInvalida as exc:
                # Erro de programação nosso, não do consumidor. O run morre
                # aqui em vez de entregar uma sequência que o cliente não
                # consegue montar.
                logger.error("Ordem inválida no run %s: %s", self.run_id, exc)
                self._encerrar_com_erro(f"Sequência inválida: {exc}", "ordem_invalida")
                return False

            if self._enfileirar(evento):
                if evento.get("type") in (
                    TipoEvento.RUN_FINISHED.value, TipoEvento.RUN_ERROR.value
                ):
                    self._encerrado.set()
                return True
            return False

    def _enfileirar(self, evento: dict[str, Any]) -> bool:
        try:
            self._fila.put_nowait(evento)
            return True
        except queue.Full:
            pass

        tipo = evento.get("type")
        if tipo in DESCARTAVEIS:
            # Abre espaço jogando fora o mais antigo — que também é
            # descartável ou já foi lido. O estado se corrige sozinho.
            self.descartados += 1
            try:
                self._fila.get_nowait()
                self._fila.put_nowait(evento)
                return True
            except (queue.Empty, queue.Full):
                return False

        # Essencial e sem espaço: o run não tem como continuar íntegro.
        logger.error(
            "Fila cheia no run %s com evento essencial (%s). Encerrando o run.",
            self.run_id, tipo,
        )
        self._encerrar_com_erro(
            "A conexão não acompanhou o ritmo da resposta.", "fila_cheia"
        )
        return False

    def _encerrar_com_erro(self, mensagem: str, codigo: str) -> None:
        """Último evento do run. Escrito direto na fila, sem validar.

        Sem o atalho, um `RUN_ERROR` causado por fila cheia não teria espaço na
        fila cheia — e o cliente ficaria esperando para sempre um fim que nunca
        chega. Abrir espaço para a má notícia é o mínimo.
        """
        if self._encerrado.is_set():
            return
        self._encerrado.set()
        self.sequencia.terminou = True
        erro = ev.run_error(mensagem, codigo)
        try:
            self._fila.put_nowait(erro)
        except queue.Full:
            try:
                self._fila.get_nowait()
                self._fila.put_nowait(erro)
            except (queue.Empty, queue.Full):
                logger.error("Não consegui nem entregar o RUN_ERROR do run %s.", self.run_id)

    # -------------------------------------------------------------- consumo

    def ler(self, timeout: float = 0.5) -> dict[str, Any] | None:
        try:
            return self._fila.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def terminou(self) -> bool:
        return self._encerrado.is_set() and self._fila.empty()

    def sse(self, timeout_total: float = 300.0) -> Iterator[str]:
        """O run inteiro como linhas SSE, prontas para escrever no socket."""
        import time

        limite = time.monotonic() + timeout_total
        while not self.terminou:
            if time.monotonic() > limite:
                self._encerrar_com_erro("Tempo esgotado.", "timeout")
            evento = self.ler(timeout=0.5)
            if evento is None:
                # Comentário SSE: mantém a conexão viva sem inventar evento.
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"

    # ------------------------------------------------------- conveniências

    def iniciar(self, parent_run_id: str | None = None) -> bool:
        return self.emitir(ev.run_started(self.thread_id, self.run_id, parent_run_id))

    def concluir(self, resultado: Any = None) -> bool:
        return self.emitir(ev.run_finished(self.thread_id, self.run_id, resultado))

    def falhar(self, mensagem: str, codigo: str | None = None) -> bool:
        return self.emitir(ev.run_error(mensagem, codigo))

    def mensagem(self, texto: str, message_id: str | None = None) -> str:
        """Uma mensagem completa de uma vez. Devolve o `messageId`.

        Para texto que já está pronto — o caminho de voz sintetiza por sentença
        e só depois publica. Streaming de verdade usa os três eventos à mão.
        """
        mid = message_id or novo_id("msg")
        self.emitir(ev.text_message_start(mid))
        if texto:
            self.emitir(ev.text_message_content(mid, texto))
        self.emitir(ev.text_message_end(mid))
        return mid
