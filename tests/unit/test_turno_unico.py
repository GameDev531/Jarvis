"""A invariante do turno único: o comando do usuário viaja UMA vez.

O bug: o orquestrador punha o comando no histórico e passava o MESMO texto
adiante como turno atual; cada provedor serializava o histórico e ainda
acrescentava o texto atual ao fim. O modelo lia o comando duas vezes, lado a
lado, em toda requisição de raciocínio.

Estes testes cobrem os quatro caminhos em que a duplicação podia voltar:
a primeira chamada em cada provedor, a segunda chamada depois de uma tool, e
a repetição por retry.
"""

from __future__ import annotations

import pytest

from james.llm.history import Conversation, ToolCall
from james.llm.message_builder import (
    ORIGEM_ATUAL,
    TurnoAtual,
    build_llm_context,
)
from james.llm.openrouter_provider import OpenRouterProvider

COMANDO = "abre o chrome e procura o horário do voo"


# ------------------------------------------------------- provedores de teste


def openrouter() -> OpenRouterProvider:
    """Provedor real, sem rede: só a montagem de mensagens é exercitada."""
    return OpenRouterProvider(
        api_key="chave-de-teste",
        models=["modelo/a", "modelo/b"],
        system_prompt="prompt de sistema",
    )


class _FakePart:
    def __init__(self, **campos):
        self.__dict__.update(campos)

    @classmethod
    def from_text(cls, text):
        return cls(kind="text", text=text)

    @classmethod
    def from_bytes(cls, data, mime_type):
        return cls(kind="bytes", data=data, mime_type=mime_type)

    @classmethod
    def from_function_call(cls, name, args):
        return cls(kind="function_call", name=name, args=args)

    @classmethod
    def from_function_response(cls, name, response):
        return cls(kind="function_response", name=name, response=response)


class _FakeContent:
    def __init__(self, role, parts):
        self.role = role
        self.parts = list(parts)


class _FakeTypes:
    Part = _FakePart
    Content = _FakeContent


def gemini():
    """GeminiProvider sem o SDK: só o tradutor de contexto é exercitado.

    `__new__` em vez de `__init__` de propósito — o construtor exige o pacote
    `google-genai` e uma chave, e nenhum dos dois diz nada sobre a montagem do
    contexto, que é o que está sob teste.
    """
    from james.llm.gemini_provider import GeminiProvider

    provider = GeminiProvider.__new__(GeminiProvider)
    provider._types = _FakeTypes
    provider.system_prompt = "prompt de sistema"
    return provider


def textos_de_usuario_gemini(contents) -> list[str]:
    textos = []
    for content in contents:
        if content.role != "user":
            continue
        for part in content.parts:
            if getattr(part, "kind", "") == "text":
                textos.append(part.text)
    return textos


def textos_de_usuario_openrouter(messages) -> list[str]:
    return [m["content"] for m in messages if m["role"] == "user"]


# ------------------------------------------------- primeira chamada do turno

# O estado EXATO em que o bug acontecia: o orquestrador punha o comando no
# histórico e passava o mesmo texto como turno atual. Cada teste abaixo cobre
# as duas situações — o chamador que segue o contrato e o que regrediu —
# porque a invariante é "exatamente uma vez", e não "uma vez se todo mundo se
# comportar".


def historico_consolidado() -> Conversation:
    """O contrato: só turnos fechados no histórico."""
    conversa = Conversation()
    conversa.add_user_text("bom dia")
    conversa.add_model_response("Bom dia, senhor.")
    return conversa


def historico_com_o_turno_atual_dentro() -> Conversation:
    """O bug: o comando já foi consolidado ANTES da requisição."""
    conversa = historico_consolidado()
    conversa.add_user_text(COMANDO)
    return conversa


def test_current_user_turn_sent_once_openrouter():
    for conversa in (historico_consolidado(), historico_com_o_turno_atual_dentro()):
        mensagens = openrouter()._build_messages(conversa, COMANDO)
        assert textos_de_usuario_openrouter(mensagens).count(COMANDO) == 1
        # E fica no fim, que é onde o modelo espera o comando da vez.
        assert mensagens[-1] == {"role": "user", "content": COMANDO}


def test_current_user_turn_sent_once_gemini():
    for conversa in (historico_consolidado(), historico_com_o_turno_atual_dentro()):
        contents = gemini()._build_contents(conversa, None, COMANDO)
        assert textos_de_usuario_gemini(contents).count(COMANDO) == 1


@pytest.mark.parametrize("provedor", ["openrouter", "gemini"])
def test_o_turno_atual_nao_dobra_nem_com_o_chamador_errado(provedor):
    """A rede de segurança, e o aviso que ela deixa.

    Sair correto não basta: se um chamador novo regredir, alguém precisa
    conseguir descobrir. `duplicacao_evitada` é o sinal, e um WARNING vai para
    o log — o contexto fica certo E o bug fica visível.
    """
    conversa = historico_com_o_turno_atual_dentro()

    if provedor == "openrouter":
        textos = textos_de_usuario_openrouter(openrouter()._build_messages(conversa, COMANDO))
    else:
        textos = textos_de_usuario_gemini(gemini()._build_contents(conversa, None, COMANDO))

    assert textos.count(COMANDO) == 1
    assert build_llm_context(conversa, COMANDO).duplicacao_evitada is True


# --------------------------------------------- segunda chamada, após a tool


def test_tool_followup_does_not_duplicate_user_turn():
    """Depois da tool, o comando original está no histórico e não se repete.

    Dois momentos, e os dois já erraram:

      - a SEGUNDA RODADA do mesmo turno manda uma ORIENTAÇÃO ("relate o
        resultado"), não um turno de usuário novo — quem pede o relato é o
        James, não a pessoa;
      - o TURNO SEGUINTE, com o par chamada/resposta ainda no histórico, é
        onde o chamador regredido dobrava o comando novo.
    """
    conversa = Conversation()
    conversa.add_user_text(COMANDO)
    conversa.add_model_response("Já vejo.", [ToolCall(name="abrir_aba", call_id="c1")])
    conversa.add_tool_result("abrir_aba", {"url": "https://exemplo.com"}, "c1")

    # (1) segunda rodada: orientação, sem comando novo
    mensagens = openrouter()._build_messages(
        conversa, None, "Relate o resultado ao usuário em uma ou duas frases."
    )
    assert textos_de_usuario_openrouter(mensagens).count(COMANDO) == 1
    contents = gemini()._build_contents(
        conversa, None, None, "Relate o resultado ao usuário em uma ou duas frases."
    )
    assert textos_de_usuario_gemini(contents).count(COMANDO) == 1

    # (2) turno seguinte, com o chamador regredido: o comando novo já entrou
    # no histórico e ainda vai como turno atual.
    seguinte = "e fecha a aba"
    conversa.add_model_response("Aba aberta, senhor.")
    conversa.add_user_text(seguinte)

    mensagens = openrouter()._build_messages(conversa, seguinte)
    assert textos_de_usuario_openrouter(mensagens).count(seguinte) == 1
    contents = gemini()._build_contents(conversa, None, seguinte)
    assert textos_de_usuario_gemini(contents).count(seguinte) == 1


def test_o_par_chamada_resposta_sobrevive_a_serializacao():
    """Cortar um `tool_call` do seu `tool_result` faz a API recusar a requisição."""
    conversa = Conversation()
    conversa.add_user_text(COMANDO)
    conversa.add_model_response("", [ToolCall(name="abrir_aba", call_id="c1")])
    conversa.add_tool_result("abrir_aba", {"ok": True}, "c1")

    mensagens = openrouter()._build_messages(conversa, None, "resuma")
    chamadas = [m for m in mensagens if m.get("tool_calls")]
    respostas = [m for m in mensagens if m["role"] == "tool"]
    assert len(chamadas) == len(respostas) == 1
    assert chamadas[0]["tool_calls"][0]["id"] == respostas[0]["tool_call_id"] == "c1"


# ----------------------------------------------------------------- retries


def test_retry_does_not_duplicate_turn():
    """O payload é montado UMA vez e reusado a cada modelo da lista.

    Se a montagem acontecesse dentro do laço com o turno já consolidado, cada
    nova tentativa acrescentaria outra cópia do comando.
    """
    provider = openrouter()
    # Com o chamador regredido: se cada tentativa remontasse o payload a partir
    # de um histórico que já contém o comando, cada retry acrescentaria outra
    # cópia — e o modelo veria o comando três vezes na terceira tentativa.
    conversa = historico_com_o_turno_atual_dentro()

    payloads = []

    def _falso_stream(payload, model, on_text):
        payloads.append(payload)
        if len(payloads) < 3:
            from james.llm.base import ProviderError

            raise ProviderError("modelo fora do ar")
        from james.llm.base import LLMResponse

        return LLMResponse(text="pronto", provider="openrouter", model=model)

    provider._stream_one = _falso_stream
    provider.models = ["a", "b", "c"]
    provider.generate(conversa, text=COMANDO)

    assert len(payloads) == 3
    for payload in payloads:
        assert textos_de_usuario_openrouter(payload["messages"]).count(COMANDO) == 1
    # E são a MESMA lista de mensagens, não três montagens independentes.
    assert payloads[0]["messages"] == payloads[1]["messages"] == payloads[2]["messages"]


def test_fallback_entre_provedores_nao_duplica(tmp_path):
    """A cadeia de papéis reenvia os MESMOS argumentos ao próximo provedor.

    Como o turno atual só é consolidado depois que a chamada volta, um
    provedor que falha não deixa rastro no histórico do próximo.
    """
    from james.config import Config
    from james.llm.base import LLMResponse, ProviderError
    from james.llm.client import LLMClient
    from james.llm.rate_limiter import RateLimiter
    from james.llm.roles import Role

    class _Provedor:
        def __init__(self, nome, falha=False):
            self.name = nome
            self.falha = falha
            self.contextos = []

        def generate(self, conversation, audio_wav=None, text=None, instruction=None,
                     tools=None, on_text=None):
            self.contextos.append(build_llm_context(conversation, text))
            if self.falha:
                raise ProviderError("caiu")
            return LLMResponse(text="ok", provider=self.name, model=self.name)

    primeiro, segundo = _Provedor("a", falha=True), _Provedor("b")
    client = LLMClient(
        config=Config({"llm": {"openrouter": {"enabled": False}}}),
        system_prompt="p",
        tools=[],
        state_dir=tmp_path,
    )
    client.providers = {"a": primeiro, "b": segundo}
    client.limiters = {
        nome: RateLimiter(100, 100, state_path=tmp_path / f"u_{nome}.json")
        for nome in ("a", "b")
    }
    client.chains = {Role.REASONING: ["a", "b"]}

    conversa = Conversation()
    client.reason(conversa, text=COMANDO)

    for provedor in (primeiro, segundo):
        assert provedor.contextos[0].ocorrencias(COMANDO) == 1
    # O histórico só é tocado por quem chamou, e só depois da resposta.
    assert len(conversa) == 0


# ---------------------------------------------------------------- contrato


def test_o_turno_atual_e_sempre_a_ultima_mensagem():
    contexto = build_llm_context(
        Conversation(), TurnoAtual(text=COMANDO), instruction="seja breve", system_prompt="p"
    )
    assert contexto.mensagens[-1].origem == ORIGEM_ATUAL
    assert contexto.mensagens[-1].text == COMANDO


def test_sem_turno_atual_o_contexto_e_so_o_historico():
    conversa = Conversation()
    conversa.add_user_text("oi")
    contexto = build_llm_context(conversa, None)
    assert contexto.ocorrencias("oi") == 1
    assert contexto.mensagens[-1].origem != ORIGEM_ATUAL


def test_repetir_o_mesmo_comando_depois_de_uma_resposta_e_legitimo():
    """"De novo" duas vezes é o usuário insistindo, não duplicação.

    A rede de segurança olha só se o histórico TERMINA no texto atual. Apagar
    qualquer repetição apagaria informação verdadeira.
    """
    conversa = Conversation()
    conversa.add_user_text("de novo")
    conversa.add_model_response("Feito.")
    contexto = build_llm_context(conversa, "de novo")
    assert contexto.ocorrencias("de novo") == 2
    assert contexto.duplicacao_evitada is False
