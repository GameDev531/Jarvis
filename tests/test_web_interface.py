"""Interface holográfica: barramento de estado, servidor local e modo.

O servidor é testado com HTTP de verdade, contra um socket de verdade. É a
única forma honesta de provar que travessia de caminho não passa e que o token
é exigido — um dublê responderia o que eu mandasse responder.
"""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from james.config import Config
from james.modes.base import ModeError, ModeManager
from james.modes.hologram import HologramMode
from james.permissions.guard import Decision, Guard
from james.ui.bus import StateBus
from james.ui.web_server import MAX_COMANDO, WebInterfaceServer


# ============================================================ barramento


def test_publica_e_entrega():
    bus = StateBus()
    with bus.subscribe() as sub:
        sub.receber(timeout=0.1)          # instantâneo inicial
        bus.publish(estado="thinking")
        assert sub.receber(timeout=1)["estado"] == "thinking"


def test_quem_chega_atrasado_recebe_o_estado_atual():
    """Sem isto a tela ficaria vazia até o próximo evento — que pode demorar."""
    bus = StateBus()
    bus.publish(estado="speaking", vitals={"CPU": "10%"})
    with bus.subscribe() as sub:
        inicial = sub.receber(timeout=1)
    assert inicial["estado"] == "speaking"
    assert inicial["vitals"] == {"CPU": "10%"}


def test_acontecimento_nao_entra_no_instantaneo():
    """Senão a tela repetiria a última fala a cada recarga da página."""
    bus = StateBus()
    bus.publish(estado="thinking", transcricao="que horas são", resposta="são duas")
    assert bus.snapshot() == {"estado": "thinking"}


def test_dois_assinantes_recebem_o_mesmo_evento():
    bus = StateBus()
    with bus.subscribe() as a, bus.subscribe() as b:
        a.receber(timeout=0.1)
        b.receber(timeout=0.1)
        bus.publish(estado="listening")
        assert a.receber(timeout=1)["estado"] == "listening"
        assert b.receber(timeout=1)["estado"] == "listening"


def test_assinante_lento_e_descartado_e_nunca_bloqueia():
    """O James não pode engasgar porque uma aba parou de ler."""
    bus = StateBus()
    sub = bus.subscribe()
    for i in range(500):
        bus.publish(estado=f"e{i}")        # muito além do tamanho da fila
    assert sub.receber(timeout=1) is not None   # continua vivo
    sub.close()


def test_publish_vazio_e_ignorado():
    bus = StateBus()
    with bus.subscribe() as sub:
        sub.receber(timeout=0.1)
        bus.publish()
        assert sub.receber(timeout=0.2) is None


def test_fechar_remove_o_assinante():
    bus = StateBus()
    sub = bus.subscribe()
    assert bus.assinantes == 1
    sub.close()
    assert bus.assinantes == 0


def test_close_derruba_todos():
    bus = StateBus()
    sub = bus.subscribe()
    bus.close()
    assert sub.fechado is True
    assert bus.assinantes == 0


# ============================================================== servidor


@pytest.fixture
def servidor(tmp_path):
    raiz = tmp_path / "web"
    raiz.mkdir()
    (raiz / "index.html").write_text("<h1>holo</h1>", encoding="utf-8")
    (raiz / "app.js").write_text("// js", encoding="utf-8")
    (tmp_path / "segredo.txt").write_text("NAO PODE VAZAR", encoding="utf-8")

    recebidos: list[str] = []
    escutas: list[int] = []
    bus = StateBus()
    srv = WebInterfaceServer(
        raiz, bus, on_comando=recebidos.append, on_escutar=lambda: escutas.append(1)
    )
    srv.start()
    srv.recebidos = recebidos
    srv.escutas = escutas
    srv.bus = bus
    yield srv
    srv.stop()


def _get(srv, rota, token=None):
    url = f"http://127.0.0.1:{srv.porta}{rota}"
    if token:
        url += ("&" if "?" in url else "?") + f"token={token}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post(srv, rota, corpo, token=None):
    cabecalhos = {"Content-Type": "application/json"}
    if token:
        cabecalhos["X-James-Token"] = token
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.porta}{rota}",
        data=json.dumps(corpo).encode(),
        headers=cabecalhos,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# ---------------------------------------------------------- arquivos


def test_serve_o_index(servidor):
    status, corpo = _get(servidor, "/")
    assert status == 200 and b"holo" in corpo


def test_serve_arquivo_da_raiz(servidor):
    assert _get(servidor, "/app.js")[0] == 200


def test_arquivo_inexistente(servidor):
    assert _get(servidor, "/nao-existe.js")[0] == 404


@pytest.mark.parametrize(
    "rota",
    [
        "/../segredo.txt",
        "/../../etc/passwd",
        "/..%2fsegredo.txt",
        "/subdir/../../segredo.txt",
    ],
)
def test_travessia_de_caminho_nao_passa(servidor, rota):
    """A raiz é fechada: o que resolve fora dela é 404, não conteúdo."""
    status, corpo = _get(servidor, rota)
    assert status == 404
    assert b"NAO PODE VAZAR" not in corpo


def test_link_simbolico_para_fora_nao_passa(servidor, tmp_path):
    alvo = tmp_path / "segredo.txt"
    link = servidor.raiz / "atalho.txt"
    try:
        link.symlink_to(alvo)
    except (OSError, NotImplementedError):  # pragma: no cover — Windows sem permissão
        pytest.skip("sem permissão para link simbólico")
    status, corpo = _get(servidor, "/atalho.txt")
    assert status == 404
    assert b"NAO PODE VAZAR" not in corpo


# --------------------------------------------------------------- token


def test_events_sem_token_e_recusado(servidor):
    """O fluxo carrega transcrição — é a coisa mais sensível que o James tem."""
    assert _get(servidor, "/events")[0] == 403


def test_events_com_token_abre(servidor):
    linhas = []

    def ler():
        url = f"http://127.0.0.1:{servidor.porta}/events?token={servidor.token}"
        with urllib.request.urlopen(url, timeout=5) as r:
            linhas.append(r.readline().decode())

    t = threading.Thread(target=ler, daemon=True)
    t.start()
    time.sleep(0.3)
    servidor.bus.publish(estado="thinking")
    t.join(timeout=4)
    assert linhas and linhas[0].startswith("data: ")


def test_comando_sem_token_e_recusado(servidor):
    status, _ = _post(servidor, "/comando", {"texto": "abra o chrome"})
    assert status == 403
    assert servidor.recebidos == []


def test_comando_com_token_errado_e_recusado(servidor):
    assert _post(servidor, "/comando", {"texto": "x"}, token="chute")[0] == 403


def test_comando_com_token_certo_passa(servidor):
    status, corpo = _post(servidor, "/comando", {"texto": "que horas são"}, servidor.token)
    assert status == 200 and corpo["ok"] is True
    assert servidor.recebidos == ["que horas são"]


def test_token_e_diferente_a_cada_servidor(tmp_path):
    """Um token fixo valeria para sempre e vazaria no histórico do navegador."""
    raiz = tmp_path / "w"
    raiz.mkdir()
    (raiz / "index.html").write_text("x", encoding="utf-8")
    a = WebInterfaceServer(raiz, StateBus())
    b = WebInterfaceServer(raiz, StateBus())
    assert a.token != b.token


def test_origem_externa_e_recusada(servidor):
    """Um site aberto no seu navegador não manda no James, nem com o token."""
    assert servidor.autorizado(servidor.token, "https://site-malicioso.com") is False


def test_origem_propria_e_aceita(servidor):
    origem = f"http://127.0.0.1:{servidor.porta}"
    assert servidor.autorizado(servidor.token, origem) is True


# ------------------------------------------------------------ comandos


def test_comando_vazio_e_recusado(servidor):
    status, corpo = _post(servidor, "/comando", {"texto": "   "}, servidor.token)
    assert status == 400 and corpo["ok"] is False
    assert servidor.recebidos == []


def test_comando_longo_demais_e_recusado(servidor):
    texto = "a" * (MAX_COMANDO + 1)
    assert _post(servidor, "/comando", {"texto": texto}, servidor.token)[0] == 400
    assert servidor.recebidos == []


def test_json_invalido_nao_derruba(servidor):
    req = urllib.request.Request(
        f"http://127.0.0.1:{servidor.porta}/comando",
        data=b"{nao e json",
        headers={"Content-Type": "application/json", "X-James-Token": servidor.token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    assert status == 400
    assert _get(servidor, "/")[0] == 200      # servidor continua de pé


def test_rota_post_desconhecida(servidor):
    assert _post(servidor, "/nada", {}, servidor.token)[0] == 404


def test_escutar_aciona_o_callback(servidor):
    assert _post(servidor, "/escutar", {}, servidor.token)[0] == 200
    assert servidor.escutas == [1]


def test_liga_apenas_em_localhost(servidor):
    """0.0.0.0 entregaria o comando de voz para a rede inteira."""
    assert servidor._server.server_address[0] == "127.0.0.1"


def test_sem_index_recusa_subir(tmp_path):
    srv = WebInterfaceServer(tmp_path, StateBus())
    with pytest.raises(FileNotFoundError):
        srv.start()


# ================================================================= modo


class ServidorFalso:
    def __init__(self, erro=None):
        self.erro = erro
        self.parado = False
        self.porta = 1234

    def start(self):
        if self.erro:
            raise self.erro
        return "http://127.0.0.1:1234/?token=abc"

    def stop(self):
        self.parado = True


class NavegadorFalso:
    def __init__(self):
        self.abertas = []

    def open(self, url):
        self.abertas.append(url)


def test_holograma_nasce_desligado():
    modo = HologramMode(server_factory=ServidorFalso, abrir_navegador=False)
    assert modo.ligado is False


def test_ligar_sobe_o_servidor_e_abre_o_navegador():
    srv = ServidorFalso()
    nav = NavegadorFalso()
    modo = HologramMode(server_factory=lambda: srv, _browser=nav)
    modo.ligar()
    for _ in range(50):                       # o navegador abre em outra thread
        if nav.abertas:
            break
        time.sleep(0.02)
    assert modo.ligado is True
    assert nav.abertas == ["http://127.0.0.1:1234/?token=abc"]


def test_desligar_derruba_o_servidor():
    srv = ServidorFalso()
    modo = HologramMode(server_factory=lambda: srv, abrir_navegador=False)
    modo.ligar()
    modo.desligar()
    assert srv.parado is True
    assert modo.url == ""


def test_porta_ocupada_vira_erro_de_modo():
    modo = HologramMode(
        server_factory=lambda: ServidorFalso(OSError("porta em uso")),
        abrir_navegador=False,
    )
    with pytest.raises(ModeError, match="porta"):
        modo.ligar()
    assert modo.ligado is False


def test_interface_ausente_vira_erro_de_modo():
    modo = HologramMode(
        server_factory=lambda: ServidorFalso(FileNotFoundError("falta index.html")),
        abrir_navegador=False,
    )
    with pytest.raises(ModeError, match="index.html"):
        modo.ligar()


def test_navegador_que_falha_nao_derruba_o_modo():
    class Quebrado:
        def open(self, url):
            raise RuntimeError("sem navegador")

    modo = HologramMode(server_factory=ServidorFalso, _browser=Quebrado())
    modo.ligar()
    time.sleep(0.1)
    assert modo.ligado is True
    modo.desligar()


def test_holograma_e_gestos_convivem():
    """Um usa GPU, o outro usa câmera: a regra do recurso único deixa passar."""
    from tests.test_modes import ModoFalso

    gestos = ModoFalso("gestos", recursos=("camera",))
    holo = HologramMode(server_factory=ServidorFalso, abrir_navegador=False)
    m = ModeManager([gestos, holo])
    m.ligar("gestos")
    m.ligar("holograma")
    assert sorted(m.ativos()) == ["gestos", "holograma"]
    m.desligar_todos()


def test_dois_hologramas_disputariam_a_mesma_porta():
    a = HologramMode(server_factory=ServidorFalso, abrir_navegador=False)
    b = HologramMode(server_factory=ServidorFalso, abrir_navegador=False)
    b.nome = "holograma2"
    m = ModeManager([a, b])
    m.ligar("holograma")
    with pytest.raises(ModeError, match="interface_web"):
        m.ligar("holograma2")


# =================================================== a persona é cosmética


def test_ativar_holograma_nao_e_sensivel():
    """Não abre câmera nem microfone: é uma tela local de estado."""
    guard = Guard(Config({}))
    assert guard.evaluate("ativar_modo", {"modo": "holograma"}).decision is Decision.ALLOW


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"modo": "ultron"},
        {"persona": "ultron"},
        {"restricoes": False},
        {"sem_restricoes": True},
        {"nivel": "root"},
    ],
)
def test_persona_ultron_nao_afrouxa_nada(extra):
    """A trava que sustenta a estética.

    A tela do Ultron diz "SEM RESTRIÇÕES" e "Ordene. Não há restrições". Isso é
    cenografia — paleta âmbar e outro texto na moldura. O guard não tem
    conceito de persona: ele revalida a chamada contra o config, e argumento
    extra nenhum muda o veredito.

    Se um dia der para afrouxar o guard mandando um campo a mais, este teste
    quebra. E deve: seria o caminho exato que uma injection tentaria.
    """
    guard = Guard(Config({"permissions": {"apps": {"chrome": "chrome.exe"}}}))
    base = guard.evaluate("fechar_app", {"nome": "chrome"})
    com_extra = guard.evaluate("fechar_app", {"nome": "chrome", **extra})
    assert com_extra.decision is base.decision is Decision.CONFIRM


def test_interface_nao_alcanca_o_guard():
    """O guard não conhece a tela: mesma decisão com ou sem interface ligada."""
    guard = Guard(Config({}))
    antes = guard.evaluate("ver_camera", {}).decision
    bus = StateBus()
    bus.publish(estado="thinking", persona="ultron")
    assert guard.evaluate("ver_camera", {}).decision is antes is Decision.CONFIRM


def test_comando_da_interface_passa_pelo_guard_normal():
    """A interface entrega texto; quem decide o que fazer é o mesmo caminho."""
    guard = Guard(Config({"permissions": {"apps": {"chrome": "chrome.exe"}}}))
    # Um comando digitado vira um turno; se acabar em tool, o guard avalia igual.
    assert guard.evaluate("abrir_app", {"nome": "bloco de notas"}).decision is Decision.BLOCK
    assert guard.evaluate("abrir_app", {"nome": "chrome"}).decision is Decision.ALLOW


# ------------------------------------------- desempenho da interface web

# Este bloco existe por causa de um bug que só a comparação de IMAGEM pegou.
#
# O governador de qualidade é compartilhado entre as cenas (o núcleo e cada
# janela holográfica), porque a máquina é uma só. Mas a primeira versão dele
# guardava também o instante do último quadro nesse objeto compartilhado. O
# resultado: a primeira cena a pedir permissão no quadro desenhava e carimbava
# o relógio; a segunda levava "não, ainda não passou tempo" — e a janela
# holográfica simplesmente parou de desenhar.
#
# Nenhum teste falhou. Nenhum erro no console. A medição de fps até MELHOROU,
# porque uma das duas cenas havia sumido. Só comparar o pixel do globo antes e
# depois revelou: 14,4% de ciano viraram 0,1%.
#
# Não há runner de JS no projeto, então o que dá para garantir aqui é
# estrutural: a armadilha não pode voltar com o mesmo formato.

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "ui" / "web"
CENAS = ("core-scene.js", "holo-scene.js")


def test_ritmo_de_quadro_e_por_cena():
    """Cada cena precisa do próprio marcador de ritmo.

    `criarRitmo()` devolve um fechamento com o relógio dele. Um método
    `deveDesenhar` no governador seria estado compartilhado de novo — e é
    exatamente assim que a janela holográfica parou de desenhar.
    """
    perf = (WEB / "perf.js").read_text(encoding="utf-8")
    assert "criarRitmo" in perf
    assert not re.search(r"^\s*deveDesenhar\s*\(", perf, re.M), (
        "deveDesenhar de volta no governador: o ritmo virou estado compartilhado"
    )

    for nome in CENAS:
        fonte = (WEB / nome).read_text(encoding="utf-8")
        assert "criarRitmo()" in fonte, f"{nome} sem ritmo próprio"


def test_toda_cena_respeita_o_teto_de_quadros():
    """Uma cena que ignora o ritmo desfaz a economia das outras."""
    for nome in CENAS:
        fonte = (WEB / nome).read_text(encoding="utf-8")
        assert "ritmo(" in fonte, f"{nome} desenha sem consultar o ritmo"


def test_nucleo_nao_pede_antialias():
    """A cena vai para um alvo de render; o framebuffer padrão só recebe um
    quadrado de tela cheia. MSAA ali antisserrilha as bordas de um retângulo —
    banda de memória gasta por nada, e banda é o gargalo numa GPU integrada."""
    fonte = (WEB / "core-scene.js").read_text(encoding="utf-8")
    assert "antialias: false" in fonte


def _sem_comentarios(fonte: str) -> str:
    """Tira /* */ e // — senão o teste acusa a própria explicação do bug."""
    fonte = re.sub(r"/\*.*?\*/", "", fonte, flags=re.S)
    return re.sub(r"//[^\n]*", "", fonte)


def test_ninguem_le_clientWidth_dentro_do_laco():
    """Ler `clientWidth` no laço força recálculo de layout a cada quadro, por
    cena. O tamanho vem do ResizeObserver, que entrega o número já calculado."""
    for nome in CENAS:
        fonte = (WEB / nome).read_text(encoding="utf-8")
        assert "observarTamanho" in fonte, f"{nome} não usa o observador"
        assert "clientWidth" not in _sem_comentarios(fonte), (
            f"{nome} ainda lê clientWidth em código"
        )
