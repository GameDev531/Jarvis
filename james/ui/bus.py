"""Barramento de estado — o que o James está fazendo, para quem quiser ver.

A janela Qt recebe o estado por sinais do Qt, que só funcionam dentro do
processo. A interface holográfica roda no navegador, fora do processo. Em vez
de espalhar `if tem_web:` por todo o orquestrador, existe este barramento: o
orquestrador publica uma vez, e quem estiver ouvindo recebe.

Duas decisões que importam:

**Instantâneo para quem chega atrasado.** O navegador conecta muito depois do
James ter subido. Sem um instantâneo, a tela ficaria vazia até o próximo
evento — que pode demorar minutos. Todo assinante novo recebe o estado atual
antes do primeiro evento.

**Assinante lento é descartado, nunca espera.** Cada assinante tem uma fila
limitada. Se o navegador travar ou a aba for congelada pelo sistema, a fila
enche — e aí o evento mais antigo é jogado fora em vez de bloquear a thread do
orquestrador. O James nunca pode engasgar porque uma aba parou de ler: a
interface é um espectador, não um sócio.
"""

from __future__ import annotations

import queue
import threading
from typing import Any

from james.logs import get_logger

logger = get_logger("james.ui.bus")

# Fila por assinante. Cabe bem mais que uma rajada normal de um turno; se
# encher, é porque o consumidor parou de ler.
_TAMANHO_DA_FILA = 64

# Acontecimentos, não estado: guardá-los faria a tela repetir a última fala a
# cada recarga. Virou constante quando o adaptador AG-UI passou a precisar da
# MESMA fronteira — duas listas iguais em arquivos diferentes é uma lista
# desatualizada esperando acontecer.
CHAVES_EFEMERAS = frozenset({"log", "transcricao", "resposta", "turno"})


class Subscriber:
    """Uma ligação viva com o barramento. Feche quando terminar."""

    def __init__(self, bus: "StateBus") -> None:
        self._bus = bus
        self._fila: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=_TAMANHO_DA_FILA)
        self._fechado = threading.Event()

    def entregar(self, evento: dict[str, Any]) -> None:
        try:
            self._fila.put_nowait(evento)
        except queue.Full:
            # Descarta o mais antigo e tenta de novo: numa tela de estado, o
            # evento recente vale mais que o antigo.
            try:
                self._fila.get_nowait()
                self._fila.put_nowait(evento)
            except (queue.Empty, queue.Full):  # pragma: no cover — corrida rara
                pass

    def receber(self, timeout: float = 1.0) -> dict[str, Any] | None:
        """Próximo evento, ou None quando o tempo acaba (para mandar keep-alive)."""
        try:
            return self._fila.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def fechado(self) -> bool:
        return self._fechado.is_set()

    def close(self) -> None:
        self._fechado.set()
        self._bus.unsubscribe(self)

    def __enter__(self) -> "Subscriber":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class StateBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._assinantes: list[Subscriber] = []
        self._estado: dict[str, Any] = {"estado": "hidden"}

    # ------------------------------------------------------------ publicação

    def publish(self, **dados: Any) -> None:
        """Publica um evento e funde no instantâneo o que for estado durável."""
        if not dados:
            return
        with self._lock:
            # `log`, `transcricao` e `resposta` são acontecimentos, não estado:
            # guardá-los faria a tela repetir a última fala a cada recarga.
            for chave, valor in dados.items():
                if chave not in CHAVES_EFEMERAS:
                    self._estado[chave] = valor
            assinantes = list(self._assinantes)

        for assinante in assinantes:
            assinante.entregar(dict(dados))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._estado)

    # ------------------------------------------------------------- assinatura

    def subscribe(self) -> Subscriber:
        assinante = Subscriber(self)
        with self._lock:
            self._assinantes.append(assinante)
        # Estado atual primeiro: quem chegou agora precisa saber onde estamos.
        assinante.entregar(self.snapshot())
        return assinante

    def unsubscribe(self, assinante: Subscriber) -> None:
        with self._lock:
            if assinante in self._assinantes:
                self._assinantes.remove(assinante)

    @property
    def assinantes(self) -> int:
        with self._lock:
            return len(self._assinantes)

    def close(self) -> None:
        with self._lock:
            assinantes = list(self._assinantes)
            self._assinantes.clear()
        for assinante in assinantes:
            assinante._fechado.set()
