"""Canal entre os dois processos do James (C9).

Topologia: o `wake_listener` (processo 1, sempre vivo) é o SERVIDOR e o
orquestrador (processo 2) é o cliente. A escolha não é arbitrária — quem
sobrevive é quem escuta, então a queda do orquestrador aparece naturalmente
como uma desconexão, e o watchdog não precisa de outro mecanismo.

Bind na porta de loopback é também o lock de instância única: se a porta já
está tomada, outro James está rodando, e dois deles brigariam pelo microfone.

Protocolo: JSON delimitado por nova linha. Só trafega em 127.0.0.1.

Mensagens do listener para o orquestrador:
    wake        — palavra de ativação detectada; o microfone JÁ foi liberado
    shutdown    — encerrando

Mensagens do orquestrador para o listener:
    hello       — conectado e pronto
    heartbeat   — sinal de vida (o watchdog reinicia se parar de chegar)
    pause_wake  — feche o microfone e pare de escutar
    resume_wake — volte a escutar a palavra de ativação
    state       — estado atual, só para diagnóstico
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any, Callable

from james.logs import get_logger

logger = get_logger("james.state.ipc")

Message = dict[str, Any]
Handler = Callable[[Message], None]

_ENCODING = "utf-8"
_MAX_LINE_BYTES = 64 * 1024  # nenhuma mensagem legítima chega perto disso


class PortUnavailable(RuntimeError):
    """A porta já está em uso — provavelmente outro James rodando."""


def _send_line(sock: socket.socket, message: Message) -> bool:
    try:
        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode(_ENCODING)
        sock.sendall(payload)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("Falha ao enviar mensagem IPC: %s", exc)
        return False


class _LineReader:
    """Acumula bytes e entrega mensagens completas.

    TCP não preserva fronteiras de mensagem: uma linha pode chegar partida em
    duas leituras, e duas linhas podem vir na mesma. Sem este buffer, o
    protocolo funciona em teste e falha sob carga.
    """

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, chunk: bytes) -> list[Message]:
        self._buffer += chunk
        if len(self._buffer) > _MAX_LINE_BYTES:
            logger.warning("Linha IPC longa demais; descartando buffer.")
            self._buffer = b""
            return []

        messages: list[Message] = []
        while b"\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition(b"\n")
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text.decode(_ENCODING))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning("Mensagem IPC ilegível descartada: %s", exc)
                continue
            if isinstance(parsed, dict):
                messages.append(parsed)
        return messages


class IpcServer:
    """Lado do wake_listener. Atende um cliente por vez (só existe um)."""

    def __init__(self, host: str, port: int, on_message: Handler) -> None:
        self.host = host
        self.port = port
        self.on_message = on_message
        self._server: socket.socket | None = None
        self._client: socket.socket | None = None
        self._client_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = threading.Event()

    def start(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Sem SO_REUSEADDR de propósito: aqui o bind exclusivo É o lock de
        # instância única. Reusar o endereço deixaria dois James subirem.
        try:
            server.bind((self.host, self.port))
        except OSError as exc:
            server.close()
            raise PortUnavailable(
                f"Porta {self.host}:{self.port} indisponível ({exc}). "
                "Outro James já deve estar rodando."
            ) from exc
        server.listen(1)
        server.settimeout(0.5)
        self._server = server
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="ipc-server", daemon=True)
        self._thread.start()
        logger.info("IPC escutando em %s:%d", self.host, self.port)

    def _accept_loop(self) -> None:
        while not self._stop.is_set() and self._server is not None:
            try:
                client, address = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    logger.debug("Socket servidor encerrado.")
                return

            if address[0] not in ("127.0.0.1", "::1"):
                # O bind é em loopback, mas negar explicitamente deixa a
                # intenção clara e protege contra configuração errada.
                logger.warning("Conexão IPC recusada de %s", address[0])
                client.close()
                continue

            logger.info("Orquestrador conectado.")
            with self._client_lock:
                self._close_client()
                self._client = client
            self.connected.set()
            self._read_loop(client)
            self.connected.clear()
            logger.info("Orquestrador desconectado.")

    def _read_loop(self, client: socket.socket) -> None:
        reader = _LineReader()
        client.settimeout(0.5)
        while not self._stop.is_set():
            try:
                chunk = client.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            for message in reader.feed(chunk):
                self._dispatch(message)

        with self._client_lock:
            if self._client is client:
                self._close_client()

    def _dispatch(self, message: Message) -> None:
        try:
            self.on_message(message)
        except Exception:  # noqa: BLE001 — handler não pode derrubar o listener
            logger.exception("Erro ao tratar mensagem IPC: %r", message.get("type"))

    def send(self, message: Message) -> bool:
        with self._client_lock:
            if self._client is None:
                return False
            if not _send_line(self._client, message):
                self._close_client()
                return False
            return True

    def _close_client(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except OSError:
                pass
            self._client = None

    def stop(self) -> None:
        self._stop.set()
        with self._client_lock:
            self._close_client()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)


class IpcClient:
    """Lado do orquestrador. Reconecta sozinho enquanto estiver rodando."""

    def __init__(
        self,
        host: str,
        port: int,
        on_message: Handler,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = threading.Event()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ipc-client", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._connect():
                time.sleep(1.0)
                continue
            self._read_loop()
            self.connected.clear()
            if self.on_disconnect is not None and not self._stop.is_set():
                try:
                    self.on_disconnect()
                except Exception:  # noqa: BLE001
                    logger.exception("Erro no callback de desconexão.")
            time.sleep(0.5)

    def _connect(self) -> bool:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=3.0)
        except OSError as exc:
            logger.debug("Sem conexão com o wake listener: %s", exc)
            return False
        sock.settimeout(0.5)
        with self._lock:
            self._sock = sock
        self.connected.set()
        logger.info("Conectado ao wake listener.")
        return True

    def _read_loop(self) -> None:
        reader = _LineReader()
        sock = self._sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            for message in reader.feed(chunk):
                try:
                    self.on_message(message)
                except Exception:  # noqa: BLE001
                    logger.exception("Erro ao tratar mensagem IPC: %r", message.get("type"))

        with self._lock:
            if self._sock is sock:
                try:
                    sock.close()
                except OSError:
                    pass
                self._sock = None

    def send(self, message: Message) -> bool:
        with self._lock:
            if self._sock is None:
                return False
            if not _send_line(self._sock, message):
                return False
            return True

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
