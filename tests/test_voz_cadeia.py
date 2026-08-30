"""Cadeia de vozes: nuvem primeiro, local como reserva.

A premissa do desenho, e o motivo de 10.000 caracteres por mês chegarem a
render: **a ElevenLabs nunca vê a conversa.** O OpenRouter pensa, decide e
escreve; a ElevenLabs recebe a frase pronta e mais nada. Raciocínio, histórico,
resultado de busca e descrição de imagem ficam fora da cota de voz.

Isso é uma propriedade do código, não uma intenção — e por isso tem teste.
"""

from __future__ import annotations

from datetime import date

import pytest

from james.voice.budget import CharacterBudget
from james.voice.chain import VoiceChain, _Motor
from james.voice.tts import TTSUnavailable


class VozFalsa:
    def __init__(self, sample_rate=22050, erro=None, marca=b"\x01\x02"):
        self.sample_rate = sample_rate
        self.erro = erro
        self.marca = marca
        self.recebeu: list[str] = []

    def synthesize(self, texto):
        self.recebeu.append(texto)
        if self.erro:
            raise self.erro
        return self.marca * max(1, len(texto))


class Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ============================================================== orçamento


def test_conta_caracteres_e_nao_requisicoes():
    """Uma frase de 500 custa o mesmo que cinco de 100: o que pesa é o texto."""
    o = CharacterBudget(limite_mensal=1000)
    o.consumir("a" * 500)
    assert o.usado == 500
    for _ in range(5):
        o.consumir("b" * 100)
    assert o.usado == 1000
    assert o.esgotado is True


def test_pergunta_antes_de_gastar():
    """`cabe` evita sintetizar meia frase na nuvem e a outra metade local —
    o que soaria como duas pessoas terminando a mesma frase."""
    o = CharacterBudget(limite_mensal=100)
    o.consumir("x" * 90)
    assert o.cabe("cabe") is True          # 4 <= 10
    assert o.cabe("nao cabe de jeito nenhum") is False


def test_texto_vazio_nao_custa():
    o = CharacterBudget(limite_mensal=100)
    assert o.consumir("") == 0
    assert o.usado == 0


def test_ciclo_vira_e_libera_a_cota():
    dias = [date(2026, 8, 20)]
    o = CharacterBudget(limite_mensal=100, hoje=lambda: dias[0])
    o.consumir("x" * 100)
    assert o.esgotado is True

    dias[0] = date(2026, 9, 1)
    assert o.restante == 100
    assert o.esgotado is False


def test_dia_da_virada_personalizado():
    """A ElevenLabs zera na data da assinatura, não no dia 1º."""
    dias = [date(2026, 8, 20)]
    o = CharacterBudget(limite_mensal=100, dia_da_virada=15, hoje=lambda: dias[0])
    o.consumir("x" * 100)

    dias[0] = date(2026, 9, 10)     # ainda no ciclo que começou em 15/ago
    assert o.esgotado is True
    dias[0] = date(2026, 9, 15)     # virou
    assert o.restante == 100


def test_persiste_entre_execucoes(tmp_path):
    """Reiniciar o James não pode devolver cota que já foi gasta."""
    caminho = tmp_path / "voz.json"
    CharacterBudget(limite_mensal=500, state_path=caminho).consumir("x" * 300)
    assert CharacterBudget(limite_mensal=500, state_path=caminho).usado == 300


def test_contador_corrompido_recomeça(tmp_path):
    """Gastar a mais uma vez é melhor que o James nunca mais falar."""
    caminho = tmp_path / "voz.json"
    caminho.write_text("{lixo", encoding="utf-8")
    assert CharacterBudget(limite_mensal=500, state_path=caminho).usado == 0


# ================================================================ cadeia


def test_usa_o_primeiro_motor_disponivel():
    nuvem, local = VozFalsa(16000), VozFalsa(22050)
    cadeia = VoiceChain([_Motor("elevenlabs", nuvem), _Motor("piper", local)])
    cadeia.synthesize("olá")
    assert nuvem.recebeu == ["olá"]
    assert local.recebeu == []


def test_cai_para_o_local_quando_a_nuvem_falha():
    nuvem = VozFalsa(erro=TTSUnavailable("sem rede"))
    local = VozFalsa(22050)
    cadeia = VoiceChain([_Motor("elevenlabs", nuvem), _Motor("piper", local)])
    assert cadeia.synthesize("olá")
    assert local.recebeu == ["olá"]


def test_cai_para_o_local_quando_a_cota_acaba():
    """O caso que mais vai acontecer: 10.000 caracteres não duram o mês."""
    orcamento = CharacterBudget(limite_mensal=10)
    nuvem, local = VozFalsa(16000), VozFalsa(22050)
    cadeia = VoiceChain([
        _Motor("elevenlabs", nuvem, orcamento), _Motor("piper", local)
    ])

    cadeia.synthesize("curta")            # 5 caracteres, cabe
    cadeia.synthesize("frase bem mais longa que a cota")

    assert nuvem.recebeu == ["curta"]
    assert local.recebeu == ["frase bem mais longa que a cota"]


def test_so_cobra_depois_que_o_audio_chega():
    """Falha de rede não pode consumir cota que a ElevenLabs não cobrou."""
    orcamento = CharacterBudget(limite_mensal=1000)
    nuvem = VozFalsa(erro=TTSUnavailable("timeout"))
    cadeia = VoiceChain([
        _Motor("elevenlabs", nuvem, orcamento), _Motor("piper", VozFalsa())
    ])
    cadeia.synthesize("uma frase que não foi sintetizada")
    assert orcamento.usado == 0


def test_motor_que_falha_fica_de_castigo():
    """Sem castigo, cada frase pagaria o timeout da nuvem antes de cair."""
    relogio = Relogio()
    nuvem = VozFalsa(erro=TTSUnavailable("caiu"))
    local = VozFalsa()
    cadeia = VoiceChain(
        [_Motor("elevenlabs", nuvem), _Motor("piper", local)], clock=relogio
    )
    cadeia.synthesize("primeira")
    cadeia.synthesize("segunda")
    assert len(nuvem.recebeu) == 1        # não tentou de novo
    assert len(local.recebeu) == 2


def test_castigo_expira():
    relogio = Relogio()
    nuvem = VozFalsa(erro=TTSUnavailable("instável"))
    cadeia = VoiceChain(
        [_Motor("elevenlabs", nuvem), _Motor("piper", VozFalsa())], clock=relogio
    )
    cadeia.synthesize("primeira")
    relogio.t += 200.0
    nuvem.erro = None
    cadeia.synthesize("segunda")
    assert len(nuvem.recebeu) == 2


def test_sample_rate_acompanha_quem_falou():
    """ElevenLabs entrega 16 kHz e o Piper 22050. Errar a taxa acelera ou
    arrasta a voz — e a troca acontece no meio de uma resposta."""
    nuvem = VozFalsa(sample_rate=16000, erro=TTSUnavailable("cota"))
    local = VozFalsa(sample_rate=22050)
    cadeia = VoiceChain([_Motor("elevenlabs", nuvem), _Motor("piper", local)])
    cadeia.synthesize("frase")
    assert cadeia.sample_rate == 22050


def test_todos_falharam_levanta():
    cadeia = VoiceChain([
        _Motor("elevenlabs", VozFalsa(erro=TTSUnavailable("a"))),
        _Motor("piper", VozFalsa(erro=TTSUnavailable("b"))),
    ])
    with pytest.raises(TTSUnavailable):
        cadeia.synthesize("nada vai funcionar")


def test_cadeia_vazia_nao_quebra_na_construcao():
    cadeia = VoiceChain([])
    assert cadeia.disponivel is False
    assert cadeia.synthesize("") == b""


def test_texto_vazio_nao_aciona_motor():
    nuvem = VozFalsa()
    assert VoiceChain([_Motor("elevenlabs", nuvem)]).synthesize("   ") == b""
    assert nuvem.recebeu == []


def test_aquece_so_o_motor_local():
    """A nuvem não tem modelo para carregar, e aquecê-la gastaria cota."""
    class ComPrewarm(VozFalsa):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.aqueceu = False

        def prewarm(self):
            self.aqueceu = True
            return 0.5

    nuvem, local = ComPrewarm(16000), ComPrewarm(22050)
    VoiceChain([
        _Motor("elevenlabs", nuvem, CharacterBudget(limite_mensal=1000)),
        _Motor("piper", local),
    ]).prewarm()
    assert nuvem.aqueceu is False
    assert local.aqueceu is True


# ============================================ a economia que sustenta tudo


def test_a_voz_recebe_so_a_frase_final():
    """A premissa do projeto, verificada.

    O que chega ao motor de voz é exatamente o texto que sai pelo alto-falante.
    Se um dia alguém passar o histórico ou o resultado de uma busca para cá, a
    cota do mês evapora numa conversa — e este teste quebra antes disso.
    """
    orcamento = CharacterBudget(limite_mensal=10_000)
    nuvem = VozFalsa(16000)
    cadeia = VoiceChain([_Motor("elevenlabs", nuvem, orcamento)])

    resposta = "São duas da tarde, senhor."
    cadeia.synthesize(resposta)

    assert nuvem.recebeu == [resposta]
    assert orcamento.usado == len(resposta)


def test_espacos_extras_nao_custam_cota():
    """O modelo às vezes devolve texto com quebras e espaços duplos."""
    orcamento = CharacterBudget(limite_mensal=1000)
    cadeia = VoiceChain([_Motor("elevenlabs", VozFalsa(), orcamento)])
    cadeia.synthesize("  olá   \n  senhor  ")
    assert orcamento.usado == len("olá senhor")


# ================================== o provedor, contra um servidor de verdade

import json as _json
import struct
import threading as _threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _FakeElevenLabs:
    """Servidor local que imita a API. Melhor que um dublê: exercita httpx,
    cabeçalhos, parâmetros de query e o corpo JSON de verdade."""

    def __init__(self, status=200, corpo=None):
        self.status = status
        self.corpo = corpo
        self.pedidos = []
        app = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                tamanho = int(self.headers.get("Content-Length") or 0)
                app.pedidos.append({
                    "caminho": self.path,
                    "chave": self.headers.get("xi-api-key"),
                    "corpo": _json.loads(self.rfile.read(tamanho) or b"{}"),
                })
                dados = app.corpo if app.corpo is not None else struct.pack("<4h", 1, 2, 3, 4)
                self.send_response(app.status)
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)

        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.porta = self.servidor.server_address[1]
        _threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def parar(self):
        self.servidor.shutdown()
        self.servidor.server_close()


@pytest.fixture
def api():
    servidor = _FakeElevenLabs()
    yield servidor
    servidor.parar()


def _provedor(api, monkeypatch, **kwargs):
    import james.voice.elevenlabs_tts as mod

    monkeypatch.setattr(mod, "_BASE", f"http://127.0.0.1:{api.porta}/v1")
    return mod.ElevenLabsTTS(api_key="chave-de-teste", **kwargs)


def test_pede_pcm_16k_e_manda_a_chave(api, monkeypatch):
    """`pcm_16000` é o que evita decodificar MP3 — e funciona no plano grátis."""
    tts = _provedor(api, monkeypatch)
    assert tts.synthesize("olá senhor")

    pedido = api.pedidos[0]
    assert "output_format=pcm_16000" in pedido["caminho"]
    assert pedido["chave"] == "chave-de-teste"
    assert pedido["corpo"]["text"] == "olá senhor"
    assert pedido["corpo"]["model_id"] == "eleven_flash_v2_5"


def test_sample_rate_bate_com_o_pipeline(api, monkeypatch):
    """16 kHz é a taxa do microfone, do VAD e do reprodutor."""
    assert _provedor(api, monkeypatch).sample_rate == 16000


def test_chave_recusada_vira_TTSUnavailable(api, monkeypatch):
    api.status = 401
    with pytest.raises(TTSUnavailable, match="recusada"):
        _provedor(api, monkeypatch).synthesize("olá")


def test_cota_estourada_vira_TTSUnavailable(api, monkeypatch):
    """429 tem que virar fallback para o Piper, não derrubar o turno."""
    api.status = 429
    with pytest.raises(TTSUnavailable, match="[Cc]ota"):
        _provedor(api, monkeypatch).synthesize("olá")


def test_audio_vazio_e_recusado(api, monkeypatch):
    api.corpo = b""
    with pytest.raises(TTSUnavailable, match="vazio"):
        _provedor(api, monkeypatch).synthesize("olá")


def test_byte_impar_e_descartado(api, monkeypatch):
    """PCM 16-bit tem número par de bytes. Meia amostra vira estalo alto."""
    api.corpo = struct.pack("<4h", 1, 2, 3, 4) + b"\x00"
    pcm = _provedor(api, monkeypatch).synthesize("olá")
    assert len(pcm) % 2 == 0


def test_texto_gigante_e_cortado(api, monkeypatch):
    """Um despejo de 10.000 caracteres custaria a cota do mês numa tacada."""
    import james.voice.elevenlabs_tts as mod

    _provedor(api, monkeypatch).synthesize("a" * 5000)
    assert len(api.pedidos[0]["corpo"]["text"]) <= mod.MAX_CARACTERES


def test_texto_vazio_nao_chama_a_api(api, monkeypatch):
    assert _provedor(api, monkeypatch).synthesize("   ") == b""
    assert api.pedidos == []


def test_sem_chave_nao_constroi():
    from james.voice.elevenlabs_tts import ElevenLabsTTS

    with pytest.raises(TTSUnavailable, match="ausente"):
        ElevenLabsTTS(api_key="")


def test_prewarm_nao_gasta_cota(api, monkeypatch):
    """Aquecer a nuvem sintetizaria caracteres à toa."""
    assert _provedor(api, monkeypatch).prewarm() == 0.0
    assert api.pedidos == []
