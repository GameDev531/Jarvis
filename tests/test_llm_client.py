"""Cliente único de LLM: fallback, cota e separação instrução/comando."""


import pytest

from james.config import Config
from james.llm.base import LLMResponse, NoProviderAvailable, ProviderError, QuotaExceeded
from james.llm.client import LLMClient, ServiceState
from james.llm.history import Conversation, ToolCall
from james.llm.rate_limiter import RateLimiter


class FakeProvider:
    def __init__(self, name="fake", fail_with=None, reply="tudo certo"):
        self.name = name
        self.fail_with = fail_with
        self.reply = reply
        self.calls: list[dict] = []

    def generate(self, conversation, audio_wav=None, text=None, instruction=None,
                 tools=None, on_text=None):
        self.calls.append(
            {
                "audio": audio_wav,
                "text": text,
                "instruction": instruction,
                "tools": tools,
            }
        )
        if self.fail_with is not None:
            raise self.fail_with
        if on_text is not None:
            on_text(self.reply)
        return LLMResponse(text=self.reply, provider=self.name, model=f"{self.name}-1")


@pytest.fixture
def client(tmp_path):
    config = Config(
        {
            "llm": {
                "rate_limit": {"requests_per_minute": 100, "requests_per_day": 100},
                "openrouter": {"enabled": False},
            }
        }
    )
    limiter = RateLimiter(100, 100, state_path=tmp_path / "uso.json")
    instance = LLMClient(
        config=config,
        system_prompt="prompt",
        tools=[],
        rate_limiter=limiter,
        transcribe_fallback=lambda: "comando transcrito localmente",
    )
    instance.primary = None
    instance.fallback = None
    return instance


def test_provedor_principal_responde(client):
    client.primary = FakeProvider("gemini")
    resposta = client.respond(Conversation(), audio_wav=b"audio")
    assert resposta.text == "tudo certo"
    assert client.state is ServiceState.OK


def test_cota_do_principal_cai_para_o_fallback(client):
    client.primary = FakeProvider("gemini", fail_with=QuotaExceeded("429"))
    client.fallback = FakeProvider("openrouter", reply="resposta do reserva")

    resposta = client.respond(Conversation(), audio_wav=b"audio")
    assert resposta.text == "resposta do reserva"


def test_fallback_recebe_a_transcricao_local_do_audio(client):
    """O reserva só entende texto — é aqui que o whisper.cpp se justifica."""
    client.primary = FakeProvider("gemini", fail_with=QuotaExceeded("429"))
    fallback = FakeProvider("openrouter")
    client.fallback = fallback

    client.respond(Conversation(), audio_wav=b"audio")
    assert fallback.calls[0]["text"] == "comando transcrito localmente"


def test_instrucao_do_turno_nao_substitui_o_comando(client):
    """Regressão: a saudação ocupava o lugar do comando no caminho de reserva.

    A instrução ("cumprimente o usuário") viaja separada do que a pessoa disse.
    Se ela ocupasse o campo de texto, o reserva receberia só a orientação e o
    comando se perderia.
    """
    client.primary = FakeProvider("gemini", fail_with=QuotaExceeded("429"))
    fallback = FakeProvider("openrouter")
    client.fallback = fallback

    client.respond(
        Conversation(), audio_wav=b"audio", instruction="Cumprimente o usuário."
    )

    chamada = fallback.calls[0]
    assert chamada["text"] == "comando transcrito localmente"
    assert chamada["instruction"] == "Cumprimente o usuário."


def test_instrucao_chega_ao_principal_junto_com_o_audio(client):
    primary = FakeProvider("gemini")
    client.primary = primary

    client.respond(Conversation(), audio_wav=b"audio", instruction="Cumprimente.")

    chamada = primary.calls[0]
    assert chamada["audio"] == b"audio"
    assert chamada["instruction"] == "Cumprimente."


def test_sem_transcricao_o_fallback_nao_e_usado(tmp_path):
    config = Config({"llm": {"openrouter": {"enabled": False}}})
    instance = LLMClient(
        config=config,
        system_prompt="",
        tools=[],
        rate_limiter=RateLimiter(100, 100, state_path=tmp_path / "u.json"),
        transcribe_fallback=None,
    )
    instance.primary = FakeProvider("gemini", fail_with=QuotaExceeded("429"))
    instance.fallback = FakeProvider("openrouter")

    with pytest.raises(NoProviderAvailable):
        instance.respond(Conversation(), audio_wav=b"audio")


def test_erro_de_rede_devolve_a_cota(client, tmp_path):
    """Uma requisição que nunca saiu não pode consumir cota; um 429 sim."""
    limiter = RateLimiter(100, 100, state_path=tmp_path / "rede.json")
    client.rate_limiter = limiter
    client.primary = FakeProvider("gemini", fail_with=ProviderError("conexão caiu"))

    antes = limiter.daily_remaining
    with pytest.raises(NoProviderAvailable):
        client.respond(Conversation(), audio_wav=b"audio")
    assert limiter.daily_remaining == antes


def test_429_nao_devolve_a_cota(client, tmp_path):
    limiter = RateLimiter(100, 100, state_path=tmp_path / "cota.json")
    client.rate_limiter = limiter
    client.primary = FakeProvider("gemini", fail_with=QuotaExceeded("429"))

    antes = limiter.daily_remaining
    with pytest.raises(NoProviderAvailable):
        client.respond(Conversation(), audio_wav=b"audio")
    assert limiter.daily_remaining == antes - 1


def test_cota_diaria_esgotada_vira_estado_degradado(client, tmp_path):
    limiter = RateLimiter(100, 1, state_path=tmp_path / "esgotada.json")
    client.rate_limiter = limiter
    client.primary = FakeProvider("gemini")

    client.respond(Conversation(), audio_wav=b"audio")
    with pytest.raises(NoProviderAvailable):
        client.respond(Conversation(), audio_wav=b"audio")

    assert client.state is ServiceState.QUOTA
    assert client.state.degraded is True


def test_tool_call_pendente_e_fechada_antes_da_requisicao(client):
    """C10: sem o fechamento sintético, a próxima requisição quebra."""
    client.primary = FakeProvider("gemini")
    conversa = Conversation()
    conversa.add_user_text("abre o chrome")
    conversa.add_model_response("Abrindo.", [ToolCall(name="abrir_app")])

    client.respond(conversa, text="e agora?")
    assert conversa.pending_tool_calls() == []


def test_sem_provedor_nenhum_o_estado_ja_nasce_degradado(tmp_path):
    instance = LLMClient(
        config=Config({"llm": {"openrouter": {"enabled": False}}}),
        system_prompt="",
        tools=[],
        rate_limiter=RateLimiter(10, 10, state_path=tmp_path / "x.json"),
    )
    assert instance.available is False
    assert instance.state.degraded is True


def test_fragmentos_de_texto_chegam_ao_callback(client):
    client.primary = FakeProvider("gemini", reply="Boa noite, senhor.")
    recebidos: list[str] = []
    client.respond(Conversation(), audio_wav=b"audio", on_text=recebidos.append)
    assert recebidos == ["Boa noite, senhor."]
