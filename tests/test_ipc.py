"""Canal entre os dois processos — sockets reais em loopback."""

import socket
import time

import pytest

from james.state.ipc import IpcClient, IpcServer, PortUnavailable, _LineReader


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, timeout=5.0, interval=0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def port():
    return free_port()


# ------------------------------------------------------- enquadramento

def test_linha_partida_em_dois_pedacos_e_remontada():
    """TCP não preserva fronteira de mensagem — sem buffer, isto se perde."""
    reader = _LineReader()
    assert reader.feed(b'{"type": "wa') == []
    assert reader.feed(b'ke"}\n') == [{"type": "wake"}]


def test_duas_mensagens_no_mesmo_pacote():
    reader = _LineReader()
    mensagens = reader.feed(b'{"type":"a"}\n{"type":"b"}\n')
    assert [m["type"] for m in mensagens] == ["a", "b"]


def test_json_invalido_e_descartado_sem_derrubar():
    reader = _LineReader()
    assert reader.feed(b"nao e json\n") == []
    assert reader.feed(b'{"type":"ok"}\n') == [{"type": "ok"}]


def test_linha_gigante_nao_estoura_a_memoria():
    reader = _LineReader()
    assert reader.feed(b"x" * (128 * 1024)) == []
    assert reader.feed(b'{"type":"ok"}\n') == [{"type": "ok"}]


def test_acento_sobrevive_ao_transporte():
    reader = _LineReader()
    mensagens = reader.feed('{"texto":"confirmação"}\n'.encode("utf-8"))
    assert mensagens[0]["texto"] == "confirmação"


# ----------------------------------------------------------- servidor

def test_porta_ocupada_e_erro_explicito(port):
    """O bind é o lock de instância única: dois James não podem subir."""
    primeiro = IpcServer("127.0.0.1", port, lambda m: None)
    primeiro.start()
    try:
        segundo = IpcServer("127.0.0.1", port, lambda m: None)
        with pytest.raises(PortUnavailable):
            segundo.start()
    finally:
        primeiro.stop()


def test_mensagens_trafegam_nos_dois_sentidos(port):
    do_cliente: list[dict] = []
    do_servidor: list[dict] = []

    servidor = IpcServer("127.0.0.1", port, do_cliente.append)
    servidor.start()
    cliente = IpcClient("127.0.0.1", port, do_servidor.append)
    cliente.start()

    try:
        assert wait_for(lambda: cliente.connected.is_set() and servidor.connected.is_set())

        assert cliente.send({"type": "heartbeat"})
        assert wait_for(lambda: any(m["type"] == "heartbeat" for m in do_cliente))

        assert servidor.send({"type": "wake"})
        assert wait_for(lambda: any(m["type"] == "wake" for m in do_servidor))
    finally:
        cliente.stop()
        servidor.stop()


def test_envio_sem_cliente_conectado_falha_sem_excecao(port):
    servidor = IpcServer("127.0.0.1", port, lambda m: None)
    servidor.start()
    try:
        assert servidor.send({"type": "wake"}) is False
    finally:
        servidor.stop()


def test_queda_do_cliente_e_percebida_pelo_servidor(port):
    """É assim que o watchdog descobre que o orquestrador morreu."""
    servidor = IpcServer("127.0.0.1", port, lambda m: None)
    servidor.start()
    cliente = IpcClient("127.0.0.1", port, lambda m: None)
    cliente.start()
    try:
        assert wait_for(servidor.connected.is_set)
        cliente.stop()
        assert wait_for(lambda: not servidor.connected.is_set())
    finally:
        servidor.stop()


def test_cliente_reconecta_quando_o_servidor_volta(port):
    cliente = IpcClient("127.0.0.1", port, lambda m: None)
    cliente.start()
    try:
        # Servidor ainda não existe: o cliente fica tentando, sem quebrar.
        time.sleep(0.3)
        assert not cliente.connected.is_set()

        servidor = IpcServer("127.0.0.1", port, lambda m: None)
        servidor.start()
        try:
            assert wait_for(cliente.connected.is_set, timeout=6.0)
        finally:
            servidor.stop()
    finally:
        cliente.stop()


def test_handler_que_levanta_excecao_nao_derruba_o_canal(port):
    recebidas: list[dict] = []

    def handler(message):
        recebidas.append(message)
        raise RuntimeError("erro proposital no handler")

    servidor = IpcServer("127.0.0.1", port, handler)
    servidor.start()
    cliente = IpcClient("127.0.0.1", port, lambda m: None)
    cliente.start()
    try:
        assert wait_for(cliente.connected.is_set)
        cliente.send({"type": "a"})
        cliente.send({"type": "b"})
        assert wait_for(lambda: len(recebidas) == 2)
    finally:
        cliente.stop()
        servidor.stop()
