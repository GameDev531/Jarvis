"""Cliente único de LLM: papéis, cadeias de fallback e cotas independentes."""

import pytest

from james.config import Config
from james.llm.base import LLMResponse, NoProviderAvailable, ProviderError, QuotaExceeded
from james.llm.client import LLMClient, ServiceState
from james.llm.history import Conversation, ToolCall
from james.llm.rate_limiter import RateLimiter
from james.llm.roles import Role


class FakeProvider:
    def __init__(
        self,
        name="fake",
        fail_with=None,
        reply="tudo certo",
        accepts_audio=True,
        accepts_image=True,
    ):
        self.name = name
        self.fail_with = fail_with
        self.reply = reply
        self.accepts_audio = accepts_audio
        self.accepts_image = accepts_image
        self.calls: list[dict] = []

    def generate(self, conversation, audio_wav=None, text=None, instruction=None,
                 tools=None, on_text=None):
        self.calls.append(
            {"kind": "text", "audio": audio_wav, "text": text, "instruction": instruction}
        )
        if self.fail_with is not None:
            raise self.fail_with
        if on_text is not None:
            on_text(self.reply)
        return LLMResponse(text=self.reply, provider=self.name, model=f"{self.name}-1")

    def generate_with_image(self, conversation, image, mime_type="image/png",
                            text="", instruction=None, tools=None, on_text=None):
        self.calls.append({"kind": "image", "image": image, "mime": mime_type, "text": text})
        if self.fail_with is not None:
            raise self.fail_with
        return LLMResponse(text=self.reply, provider=self.name, model=f"{self.name}-v")


class TextOnlyProvider:
    """Modela o OpenRouter: só texto, sem sequer expor o método de imagem."""

    accepts_audio = False
    accepts_image = False

    def __init__(self, name="openrouter", reply="resposta"):
        self.name = name
        self.reply = reply
        self.calls: list[dict] = []

    def generate(self, conversation, audio_wav=None, text=None, instruction=None,
                 tools=None, on_text=None):
        self.calls.append({"kind": "text", "text": text})
        return LLMResponse(text=self.reply, provider=self.name, model=self.name)


def build_client(tmp_path, providers: dict, chains: dict, limits=None):
    """Cliente com provedores injetados — sem tocar em rede nem em chaves."""
    client = LLMClient(
        config=Config({"llm": {"openrouter": {"enabled": False}}}),
        system_prompt="prompt",
        tools=[],
        state_dir=tmp_path,
    )
    client.providers = dict(providers)
    client.limiters = {
        name: RateLimiter(
            *(limits or {}).get(name, (100, 100)), state_path=tmp_path / f"u_{name}.json"
        )
        for name in providers
    }
    client.chains = dict(chains)
    return client


@pytest.fixture
def gemini():
    return FakeProvider("gemini", reply="a transcrição literal")


@pytest.fixture
def openrouter():
    return FakeProvider("openrouter", reply="resposta pensada", accepts_audio=False)


# ------------------------------------------------------------- percepção

def test_percepcao_usa_o_provedor_de_audio(tmp_path, gemini, openrouter):
    client = build_client(
        tmp_path,
        {"gemini": gemini, "openrouter": openrouter},
        {Role.PERCEPTION: ["gemini"], Role.REASONING: ["openrouter"], Role.VISION: ["gemini"]},
    )
    assert client.transcribe(b"audio") == "a transcrição literal"
    assert gemini.calls[0]["audio"] == b"audio"
    assert openrouter.calls == []


def test_percepcao_devolve_none_quando_nao_ha_fala(tmp_path):
    mudo = FakeProvider("gemini", reply="(sem fala)")
    client = build_client(tmp_path, {"gemini": mudo}, {Role.PERCEPTION: ["gemini"]})
    assert client.transcribe(b"audio") is None


def test_percepcao_remove_aspas_do_modelo(tmp_path):
    """Modelos costumam devolver a transcrição entre aspas mesmo quando pedido."""
    aspas = FakeProvider("gemini", reply='"abre o chrome"')
    client = build_client(tmp_path, {"gemini": aspas}, {Role.PERCEPTION: ["gemini"]})
    assert client.transcribe(b"audio") == "abre o chrome"


def test_percepcao_sem_provedor_devolve_none_em_vez_de_explodir(tmp_path):
    """O orquestrador precisa poder cair para o whisper.cpp local."""
    client = build_client(tmp_path, {}, {Role.PERCEPTION: []})
    assert client.transcribe(b"audio") is None


def test_provedor_sem_audio_no_papel_de_percepcao_e_recusado(tmp_path, openrouter):
    client = build_client(tmp_path, {"openrouter": openrouter}, {Role.PERCEPTION: ["openrouter"]})
    assert client.transcribe(b"audio") is None
    assert openrouter.calls == []


# ------------------------------------------------------------- raciocínio

def test_raciocinio_usa_o_provedor_de_texto(tmp_path, gemini, openrouter):
    client = build_client(
        tmp_path,
        {"gemini": gemini, "openrouter": openrouter},
        {Role.REASONING: ["openrouter", "gemini"]},
    )
    resposta = client.reason(Conversation(), text="abre o chrome")
    assert resposta.text == "resposta pensada"
    assert resposta.provider == "openrouter"
    assert gemini.calls == []


def test_raciocinio_cai_para_o_proximo_da_cadeia(tmp_path, gemini):
    quebrado = FakeProvider("openrouter", fail_with=QuotaExceeded("429"))
    client = build_client(
        tmp_path,
        {"openrouter": quebrado, "gemini": gemini},
        {Role.REASONING: ["openrouter", "gemini"]},
    )
    resposta = client.reason(Conversation(), text="oi")
    assert resposta.provider == "gemini"


def test_cadeia_inteira_falha_levanta_erro(tmp_path):
    client = build_client(
        tmp_path,
        {
            "openrouter": FakeProvider("openrouter", fail_with=QuotaExceeded("429")),
            "gemini": FakeProvider("gemini", fail_with=QuotaExceeded("429")),
        },
        {Role.REASONING: ["openrouter", "gemini"]},
    )
    with pytest.raises(NoProviderAvailable):
        client.reason(Conversation(), text="oi")
    assert client.state is ServiceState.QUOTA


def test_papel_sem_provedor_nenhum(tmp_path):
    client = build_client(tmp_path, {}, {Role.REASONING: []})
    with pytest.raises(NoProviderAvailable):
        client.reason(Conversation(), text="oi")


def test_instrucao_viaja_separada_do_comando(tmp_path, openrouter):
    """A saudação não pode ocupar o lugar do que o usuário disse."""
    client = build_client(tmp_path, {"openrouter": openrouter}, {Role.REASONING: ["openrouter"]})
    client.reason(Conversation(), text="abre o chrome", instruction="Cumprimente.")

    chamada = openrouter.calls[0]
    assert chamada["text"] == "abre o chrome"
    assert chamada["instruction"] == "Cumprimente."


def test_fragmentos_chegam_ao_callback(tmp_path, openrouter):
    client = build_client(tmp_path, {"openrouter": openrouter}, {Role.REASONING: ["openrouter"]})
    recebidos: list[str] = []
    client.reason(Conversation(), text="oi", on_text=recebidos.append)
    assert recebidos == ["resposta pensada"]


def test_tool_call_pendente_e_fechada_antes_da_requisicao(tmp_path, openrouter):
    """C10: sem o fechamento sintético, a próxima requisição quebra."""
    client = build_client(tmp_path, {"openrouter": openrouter}, {Role.REASONING: ["openrouter"]})
    conversa = Conversation()
    conversa.add_user_text("abre o chrome")
    conversa.add_model_response("Abrindo.", [ToolCall(name="abrir_app")])

    client.reason(conversa, text="e agora?")
    assert conversa.pending_tool_calls() == []


# ------------------------------------------------------------------ visão

def test_visao_envia_a_imagem(tmp_path, gemini):
    client = build_client(tmp_path, {"gemini": gemini}, {Role.VISION: ["gemini"]})
    descricao = client.describe_image(b"png", "image/png", "o que tem aqui?")

    assert descricao == "a transcrição literal"
    chamada = gemini.calls[0]
    assert chamada["kind"] == "image" and chamada["image"] == b"png"


def test_provedor_sem_visao_e_recusado(tmp_path):
    client = build_client(
        tmp_path, {"openrouter": TextOnlyProvider(name="openrouter")}, {Role.VISION: ["openrouter"]}
    )
    with pytest.raises(NoProviderAvailable):
        client.describe_image(b"png")


# ------------------------------------------------------------------ cotas

def test_cada_provedor_tem_cota_propria(tmp_path, gemini, openrouter):
    """O ganho central da divisão de papéis: as cotas não se somam."""
    client = build_client(
        tmp_path,
        {"gemini": gemini, "openrouter": openrouter},
        {Role.PERCEPTION: ["gemini"], Role.REASONING: ["openrouter"]},
        limits={"gemini": (100, 5), "openrouter": (100, 5)},
    )
    client.transcribe(b"audio")
    client.transcribe(b"audio")

    assert client.quota_summary()["gemini"] == 3
    assert client.quota_summary()["openrouter"] == 5


def test_cota_de_um_papel_esgotada_nao_afeta_o_outro(tmp_path, gemini, openrouter):
    client = build_client(
        tmp_path,
        {"gemini": gemini, "openrouter": openrouter},
        {Role.PERCEPTION: ["gemini"], Role.REASONING: ["openrouter"]},
        limits={"gemini": (100, 1), "openrouter": (100, 10)},
    )
    assert client.transcribe(b"audio") is not None
    assert client.transcribe(b"audio") is None          # cota do Gemini acabou

    # O raciocínio segue funcionando: é outro balde.
    assert client.reason(Conversation(), text="oi").provider == "openrouter"


def test_erro_de_rede_devolve_a_cota(tmp_path):
    """Uma requisição que nunca saiu não pode consumir cota."""
    client = build_client(
        tmp_path,
        {"openrouter": FakeProvider("openrouter", fail_with=ProviderError("conexão caiu"))},
        {Role.REASONING: ["openrouter"]},
        limits={"openrouter": (100, 10)},
    )
    antes = client.quota_summary()["openrouter"]
    with pytest.raises(NoProviderAvailable):
        client.reason(Conversation(), text="oi")
    assert client.quota_summary()["openrouter"] == antes


def test_429_nao_devolve_a_cota(tmp_path):
    """Do ponto de vista do provedor, a requisição contou."""
    client = build_client(
        tmp_path,
        {"openrouter": FakeProvider("openrouter", fail_with=QuotaExceeded("429"))},
        {Role.REASONING: ["openrouter"]},
        limits={"openrouter": (100, 10)},
    )
    antes = client.quota_summary()["openrouter"]
    with pytest.raises(NoProviderAvailable):
        client.reason(Conversation(), text="oi")
    assert client.quota_summary()["openrouter"] == antes - 1


def test_sem_provedor_nenhum_o_estado_ja_nasce_degradado(tmp_path):
    client = LLMClient(
        config=Config({"llm": {"openrouter": {"enabled": False}}}),
        system_prompt="",
        tools=[],
        state_dir=tmp_path,
    )
    assert client.available is False
    assert client.state.degraded is True
