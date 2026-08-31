"""Leitura do fluxo do OpenRouter.

O streaming importa porque este provedor é o respondedor principal: sem ele, o
James só começaria a falar depois da resposta inteira pronta.

Os fragmentos chegam picotados de forma imprevisível — nome da ferramenta num
pedaço, argumentos em vários outros — e é aí que este tipo de código costuma
quebrar.
"""

import json

from james.llm.openrouter_provider import (
    _finalize_tool_calls,
    _iter_sse,
    _merge_tool_calls,
    _parse_arguments,
    _read_event,
)


def sse(*objetos) -> list[str]:
    return [f"data: {json.dumps(obj)}" for obj in objetos]


# --------------------------------------------------------------- protocolo

def test_extrai_objetos_do_fluxo():
    linhas = sse({"a": 1}, {"b": 2}) + ["data: [DONE]"]
    assert list(_iter_sse(iter(linhas))) == [{"a": 1}, {"b": 2}]


def test_comentarios_de_keep_alive_sao_ignorados():
    """O OpenRouter intercala ':' para manter a conexão viva; não é JSON."""
    linhas = [": OPENROUTER PROCESSING", "", *sse({"a": 1})]
    assert list(_iter_sse(iter(linhas))) == [{"a": 1}]


def test_done_encerra_o_fluxo():
    linhas = [*sse({"a": 1}), "data: [DONE]", *sse({"b": 2})]
    assert list(_iter_sse(iter(linhas))) == [{"a": 1}]


def test_fragmento_json_invalido_nao_derruba_o_fluxo():
    linhas = ["data: {isso nao e json", *sse({"a": 1})]
    assert list(_iter_sse(iter(linhas))) == [{"a": 1}]


def test_linhas_sem_prefixo_data_sao_ignoradas():
    linhas = ["event: ping", "id: 42", *sse({"a": 1})]
    assert list(_iter_sse(iter(linhas))) == [{"a": 1}]


# ------------------------------------------------------------------ texto

def test_le_o_texto_do_fragmento():
    texto, calls = _read_event({"choices": [{"delta": {"content": "Boa "}}]})
    assert texto == "Boa " and calls == []


def test_fragmento_sem_choices_nao_quebra():
    assert _read_event({}) == ("", [])
    assert _read_event({"choices": []}) == ("", [])


def test_fragmento_so_com_papel_nao_produz_texto():
    assert _read_event({"choices": [{"delta": {"role": "assistant"}}]}) == ("", [])


# ------------------------------------------------------------ tool calls

def test_argumentos_picotados_sao_remontados():
    """O caso normal: os argumentos chegam caractere a caractere."""
    acumulador: dict = {}
    _merge_tool_calls(
        acumulador,
        [{"index": 0, "id": "c1", "function": {"name": "abrir_app", "arguments": ""}}],
    )
    for pedaco in ('{"no', 'me": "chr', 'ome"}'):
        _merge_tool_calls(acumulador, [{"index": 0, "function": {"arguments": pedaco}}])

    calls = _finalize_tool_calls(acumulador)
    assert len(calls) == 1
    assert calls[0].name == "abrir_app"
    assert calls[0].args == {"nome": "chrome"}
    assert calls[0].call_id == "c1"


def test_duas_chamadas_da_mesma_tool_nao_se_misturam():
    """O índice é a chave, não o nome — senão os argumentos se fundiriam."""
    acumulador: dict = {}
    _merge_tool_calls(
        acumulador,
        [
            {"index": 0, "id": "a", "function": {"name": "abrir_app", "arguments": '{"nome":"chrome"}'}},
            {"index": 1, "id": "b", "function": {"name": "abrir_app", "arguments": '{"nome":"spotify"}'}},
        ],
    )
    calls = _finalize_tool_calls(acumulador)
    assert [c.args["nome"] for c in calls] == ["chrome", "spotify"]


def test_ordem_das_chamadas_segue_o_indice():
    acumulador: dict = {}
    _merge_tool_calls(acumulador, [{"index": 2, "function": {"name": "terceira"}}])
    _merge_tool_calls(acumulador, [{"index": 0, "function": {"name": "primeira"}}])
    _merge_tool_calls(acumulador, [{"index": 1, "function": {"name": "segunda"}}])

    assert [c.name for c in _finalize_tool_calls(acumulador)] == [
        "primeira",
        "segunda",
        "terceira",
    ]


def test_chamada_sem_nome_e_descartada():
    acumulador: dict = {}
    _merge_tool_calls(acumulador, [{"index": 0, "function": {"arguments": "{}"}}])
    assert _finalize_tool_calls(acumulador) == []


def test_chamada_sem_id_ainda_funciona():
    """Nem todo provedor manda id nos fragmentos."""
    acumulador: dict = {}
    _merge_tool_calls(
        acumulador, [{"index": 0, "function": {"name": "que_horas_sao", "arguments": "{}"}}]
    )
    calls = _finalize_tool_calls(acumulador)
    assert calls[0].name == "que_horas_sao" and calls[0].call_id is None


# -------------------------------------------------------------- argumentos

def test_argumentos_validos():
    assert _parse_arguments('{"nome": "chrome"}') == {"nome": "chrome"}


def test_argumentos_ja_em_dicionario():
    assert _parse_arguments({"nome": "chrome"}) == {"nome": "chrome"}


def test_argumentos_vazios():
    assert _parse_arguments("") == {}
    assert _parse_arguments(None) == {}


def test_json_quebrado_vira_dicionario_vazio():
    """Modelo pequeno erra o JSON; o guard barra o resto sozinho."""
    assert _parse_arguments('{"nome": "chr') == {}


def test_json_que_nao_e_objeto_vira_dicionario_vazio():
    assert _parse_arguments('["chrome"]') == {}
    assert _parse_arguments('"chrome"') == {}


# ------------------------------------------- modelo que saiu do catálogo

# O catálogo `:free` do OpenRouter muda sozinho: em agosto de 2026 o tier
# grátis inteiro da Meta e da Qwen foi removido de uma vez, e o config do James
# ficou apontando para dois modelos mortos. Eles não quebravam nada — só
# devolviam 404 e cediam a vez ao próximo. O custo era invisível: uma viagem de
# rede inteira desperdiçada POR REQUISIÇÃO, para sempre. Numa conexão de ~2 s,
# dois IDs mortos no topo da lista sozinhos estouram a meta de 3,5 s da voz.

import pytest

from james.llm.base import ProviderError, QuotaExceeded
from james.llm.history import Conversation
from james.llm.openrouter_provider import ModeloInexistente, OpenRouterProvider


def provider(models, **kwargs):
    return OpenRouterProvider(api_key="k", models=list(models), **kwargs)


def test_modelo_inexistente_continua_sendo_provider_error():
    """Quem não conhece a classe precisa seguir tratando como falha comum."""
    assert issubclass(ModeloInexistente, ProviderError)


def test_404_tira_o_modelo_de_circulacao(monkeypatch):
    p = provider(["morto:free", "vivo:free"])
    tentados = []

    def stream(payload, model, on_text):
        tentados.append(model)
        if model == "morto:free":
            raise ModeloInexistente("404 model not found")
        return "resposta"

    monkeypatch.setattr(p, "_stream_one", stream)

    assert p.generate(Conversation(), text="oi") == "resposta"
    assert tentados == ["morto:free", "vivo:free"]

    # A segunda requisição é o ponto do teste: o modelo morto não pode custar
    # outra viagem de rede.
    tentados.clear()
    assert p.generate(Conversation(), text="de novo") == "resposta"
    assert tentados == ["vivo:free"]


def test_429_continua_sendo_temporario(monkeypatch):
    """Cota estourada volta; catálogo removido não. Tratar igual desperdiçaria
    um modelo bom pelo resto do dia."""
    p = provider(["a:free", "b:free"], cooldown_s=0)

    def stream(payload, model, on_text):
        if model == "a:free":
            raise QuotaExceeded("429")
        return "ok"

    monkeypatch.setattr(p, "_stream_one", stream)
    p.generate(Conversation(), text="oi")
    # cooldown_s=0: já saiu do resfriamento e volta para o começo da fila.
    assert p._available_models()[0] == "a:free"


def test_erro_comum_nao_descarta_o_modelo(monkeypatch):
    """Um timeout não significa que o modelo deixou de existir."""
    p = provider(["a:free", "b:free"])
    chamadas = []

    def stream(payload, model, on_text):
        chamadas.append(model)
        if model == "a:free":
            raise ProviderError("timeout")
        return "ok"

    monkeypatch.setattr(p, "_stream_one", stream)
    p.generate(Conversation(), text="oi")
    chamadas.clear()
    p.generate(Conversation(), text="de novo")
    assert chamadas[0] == "a:free", "erro passageiro não tira o modelo da fila"


def test_todos_mortos_ainda_levanta_erro_claro(monkeypatch):
    p = provider(["x:free", "y:free"])
    monkeypatch.setattr(
        p, "_stream_one",
        lambda payload, model, on_text: (_ for _ in ()).throw(
            ModeloInexistente("404")
        ),
    )
    with pytest.raises(QuotaExceeded, match="Nenhum modelo"):
        p.generate(Conversation(), text="oi")
