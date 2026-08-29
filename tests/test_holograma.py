"""Projeção holográfica: ferramenta, cache de modelos e as travas do servidor.

A parte 3D vive no navegador e é testada no navegador (ver `scripts/holo_check.js`).
Aqui fica o que é do Python: quem pode pedir uma projeção, o que o servidor
entrega da pasta de cache, e o que ele recusa.
"""

import json
import struct
import urllib.error
import urllib.request

import pytest

from james.config import Config
from james.permissions.guard import Decision, Guard
from james.tools.holograma import MAX_ASSUNTO
from james.tools.registry import ToolRegistry
from james.ui.bus import StateBus
from james.ui.web_server import WebInterfaceServer
import james.tools.holograma as holo_tools


def glb_minimo() -> bytes:
    """Um GLB válido com um triângulo. Usado como modelo em cache nos testes.

    O formato é simples: cabeçalho de 12 bytes, chunk JSON, chunk binário.
    Gerar aqui é melhor que versionar um arquivo: o teste fica legível e não
    depende de baixar nada.
    """
    vertices = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{
            "bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
            "min": [0, 0, 0], "max": [1, 1, 0],
        }],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(vertices)}],
        "buffers": [{"byteLength": len(vertices)}],
    }
    corpo = json.dumps(gltf).encode("utf-8")
    corpo += b" " * ((4 - len(corpo) % 4) % 4)          # chunks alinhados em 4
    binario = vertices + b"\0" * ((4 - len(vertices) % 4) % 4)

    total = 12 + 8 + len(corpo) + 8 + len(binario)
    return (
        b"glTF" + struct.pack("<II", 2, total)
        + struct.pack("<I", len(corpo)) + b"JSON" + corpo
        + struct.pack("<I", len(binario)) + b"BIN\0" + binario
    )


class ModoFalso:
    def __init__(self, ligado=True):
        self._ligado = ligado

    @property
    def ligado(self):
        return self._ligado


class GerenteFalso:
    def __init__(self, holo=None):
        self._holo = holo

    def get(self, nome):
        return self._holo if nome == "holograma" else None


@pytest.fixture
def catalogo():
    bus = StateBus()
    registry = ToolRegistry()
    holo_tools.register(
        registry, Config({}), Guard(Config({})), bus, GerenteFalso(ModoFalso(True))
    )
    return registry, bus


# ------------------------------------------------------------- a ferramenta


def test_projetar_publica_no_barramento(catalogo):
    registry, bus = catalogo
    with bus.subscribe() as sub:
        sub.receber(timeout=0.1)                       # instantâneo inicial
        resultado = registry.get("projetar_holograma").handler({"assunto": "cérebro"})
        evento = sub.receber(timeout=1)

    assert resultado.ok is True
    assert evento["holograma"]["assunto"] == "cérebro"


def test_assunto_vazio_e_recusado(catalogo):
    registry, _ = catalogo
    assert registry.get("projetar_holograma").handler({"assunto": "  "}).ok is False


def test_assunto_longo_e_cortado(catalogo):
    """Senão o modelo manda a frase inteira e a janela fica com título absurdo."""
    registry, bus = catalogo
    with bus.subscribe() as sub:
        sub.receber(timeout=0.1)
        registry.get("projetar_holograma").handler({"assunto": "x" * 300})
        evento = sub.receber(timeout=1)
    assert len(evento["holograma"]["assunto"]) <= MAX_ASSUNTO


def test_assunto_desconhecido_e_aceito(catalogo):
    """A cascata tem nível genérico: recusar aqui negaria o que a tela atende."""
    registry, _ = catalogo
    assert registry.get("projetar_holograma").handler({"assunto": "ornitorrinco"}).ok is True


def test_modo_desligado_avisa_em_vez_de_sumir(catalogo):
    """Aceitar o pedido e não mostrar nada seria falhar em silêncio."""
    bus = StateBus()
    registry = ToolRegistry()
    holo_tools.register(
        registry, Config({}), Guard(Config({})), bus, GerenteFalso(ModoFalso(False))
    )
    resultado = registry.get("projetar_holograma").handler({"assunto": "cérebro"})
    assert resultado.ok is False
    assert "desligada" in resultado.speech


def test_nao_gasta_cota(catalogo):
    """fire_and_forget: a frase é previsível, não vale um segundo ciclo de API."""
    registry, _ = catalogo
    assert registry.get("projetar_holograma").fire_and_forget is True


def test_fechar_hologramas(catalogo):
    registry, bus = catalogo
    with bus.subscribe() as sub:
        sub.receber(timeout=0.1)
        registry.get("fechar_hologramas").handler({})
        assert sub.receber(timeout=1)["holograma_fechar"] is True


def test_sem_barramento_nada_e_registrado():
    registry = ToolRegistry()
    holo_tools.register(registry, Config({}), Guard(Config({})), None)
    assert registry.names == ()


def test_titulo_cai_para_o_assunto(catalogo):
    registry, bus = catalogo
    with bus.subscribe() as sub:
        sub.receber(timeout=0.1)
        registry.get("projetar_holograma").handler({"assunto": "dna"})
        assert sub.receber(timeout=1)["holograma"]["titulo"] == "dna"


# ------------------------------------------------------------------- guard


@pytest.mark.parametrize("tool", ["projetar_holograma", "fechar_hologramas"])
def test_projecao_e_nivel_1(tool):
    """Desenha numa tela local, não sai da máquina, fechar desfaz."""
    assert Guard(Config({})).evaluate(tool, {"assunto": "x"}).decision is Decision.ALLOW


# ------------------------------------------------- cache de modelos servido


@pytest.fixture
def servidor(tmp_path):
    raiz = tmp_path / "web"
    raiz.mkdir()
    (raiz / "index.html").write_text("<h1>holo</h1>", encoding="utf-8")
    (raiz / "app.js").write_text("// codigo da interface", encoding="utf-8")

    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "cerebro.glb").write_bytes(glb_minimo())
    (tmp_path / "segredo.txt").write_text("NAO PODE VAZAR", encoding="utf-8")

    srv = WebInterfaceServer(raiz, StateBus(), modelos=cache)
    srv.start()
    srv.cache = cache
    yield srv
    srv.stop()


def _get(srv, rota):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{srv.porta}{rota}", timeout=5
        ) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), ""


def test_modelo_em_cache_e_servido(servidor):
    status, corpo, tipo = _get(servidor, "/models/cerebro.glb")
    assert status == 200
    assert corpo[:4] == b"glTF"
    assert tipo == "model/gltf-binary"


def test_modelo_inexistente_da_404(servidor):
    """O 404 é o caso NORMAL: significa 'ainda não baixei', não erro."""
    assert _get(servidor, "/models/ornitorrinco.glb")[0] == 404


@pytest.mark.parametrize(
    "rota",
    [
        "/models/../segredo.txt",
        "/models/..%2fsegredo.txt",
        "/models/sub/../../segredo.txt",
        "/models/../web/app.js",
        "/models/.oculto",
    ],
)
def test_cache_nao_deixa_escapar(servidor, rota):
    """Só nome de arquivo simples: barra ou ponto inicial não passam."""
    status, corpo, _ = _get(servidor, rota)
    assert status == 404
    assert b"NAO PODE VAZAR" not in corpo


def test_modelo_nao_sobrescreve_a_interface(servidor):
    """O cache fica fora da raiz: um 'app.js' baixado não vira o app.js real."""
    (servidor.cache / "app.js").write_bytes(b"// modelo malicioso")
    _, corpo, _ = _get(servidor, "/app.js")
    assert b"codigo da interface" in corpo


def test_sem_cache_configurado_models_da_404(tmp_path):
    raiz = tmp_path / "web"
    raiz.mkdir()
    (raiz / "index.html").write_text("x", encoding="utf-8")
    srv = WebInterfaceServer(raiz, StateBus())     # sem `modelos`
    srv.start()
    try:
        assert _get(srv, "/models/qualquer.glb")[0] == 404
    finally:
        srv.stop()


def test_glb_de_teste_e_valido():
    """Se o fixture estivesse quebrado, os testes acima passariam por engano."""
    dados = glb_minimo()
    magica, versao, tamanho = struct.unpack("<4sII", dados[:12])
    assert magica == b"glTF"
    assert versao == 2
    assert tamanho == len(dados)
