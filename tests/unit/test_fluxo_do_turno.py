"""O turno como o orquestrador o executa, do comando ao relato.

Não instancia o `Orchestrator` — ele carrega Qt, áudio e microfone, e nada
disso diz respeito à ordem em que o histórico é consolidado. O que este arquivo
replica é a SEQUÊNCIA documentada em `_process_transcript`/`_reason_turn`, com
o `Conversation` e o `LLMClient` de verdade no meio.
"""

from __future__ import annotations

import re
from pathlib import Path

from james.config import Config
from james.llm.base import LLMResponse
from james.llm.client import LLMClient
from james.llm.history import Conversation, ToolCall
from james.llm.message_builder import build_llm_context
from james.llm.rate_limiter import RateLimiter
from james.llm.roles import Role

RAIZ = Path(__file__).resolve().parent.parent.parent
COMANDO = "que horas são em Tóquio"


class ProvedorEspiao:
    """Guarda o contexto lógico de cada requisição, para inspeção depois."""

    name = "espiao"
    accepts_audio = False
    accepts_image = False

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.contextos = []

    def generate(self, conversation, audio_wav=None, text=None, instruction=None,
                 tools=None, on_text=None):
        self.contextos.append(
            build_llm_context(conversation, text, instruction=instruction)
        )
        return self.respostas.pop(0)


def cliente(tmp_path, provedor):
    client = LLMClient(
        config=Config({"llm": {"openrouter": {"enabled": False}}}),
        system_prompt="prompt",
        tools=[],
        state_dir=tmp_path,
    )
    client.providers = {"espiao": provedor}
    client.limiters = {"espiao": RateLimiter(100, 100, state_path=tmp_path / "u.json")}
    client.chains = {Role.REASONING: ["espiao"]}
    return client


def test_o_turno_completo_com_tool_manda_o_comando_uma_unica_vez(tmp_path):
    """Duas requisições no turno; o comando aparece uma vez em CADA uma."""
    provedor = ProvedorEspiao([
        LLMResponse(text="Já vejo.", tool_calls=[ToolCall(name="que_horas_sao", call_id="c1")]),
        LLMResponse(text="São nove da noite em Tóquio, senhor."),
    ])
    client = cliente(tmp_path, provedor)
    conversa = Conversation()

    # --- primeira requisição: o turno atual viaja FORA do histórico
    resposta = client.reason(conversa, text=COMANDO)
    conversa.add_user_text(COMANDO)                     # consolidação, depois
    conversa.add_model_response(resposta.text, resposta.tool_calls)

    # --- a tool roda e o resultado entra no histórico
    conversa.add_tool_result("que_horas_sao", {"hora": "21:00"}, "c1")

    # --- segunda requisição: orientação, não comando novo
    client.reason(conversa, instruction="Relate o resultado ao usuário.")

    assert [c.ocorrencias(COMANDO) for c in provedor.contextos] == [1, 1]
    # E o histórico final tem o comando uma vez só, na posição certa.
    assert [t.role for t in conversa.turns()] == ["user", "model", "tool"]


def test_o_caminho_do_roteador_local_consolida_na_hora(tmp_path):
    """Sem requisição não há o que esperar: o turno entra no histórico já.

    É o que faz o comando seguinte ter contexto — "abre o chrome" / "e o
    YouTube" só funciona se o primeiro estiver no histórico.
    """
    provedor = ProvedorEspiao([LLMResponse(text="Abrindo o YouTube.")])
    client = cliente(tmp_path, provedor)
    conversa = Conversation()

    conversa.add_user_text("abre o chrome")            # roteador local: direto
    conversa.add_model_response("", [ToolCall(name="abrir_app", call_id="r1")])
    conversa.add_tool_result("abrir_app", {"status": "ok"}, "r1")

    client.reason(conversa, text="e o YouTube")

    contexto = provedor.contextos[0]
    assert contexto.ocorrencias("abre o chrome") == 1
    assert contexto.ocorrencias("e o YouTube") == 1


# ----------------------------------------------------- a ordem no código-fonte


def _fonte(metodo: str) -> str:
    """O corpo de um método do orquestrador, como texto."""
    texto = (RAIZ / "james" / "runtime" / "orchestrator.py").read_text(encoding="utf-8")
    inicio = texto.index(f"    def {metodo}(")
    resto = texto[inicio + 1:]
    fim = re.search(r"\n    def ", resto)
    return resto[: fim.start()] if fim else resto


def test_o_orquestrador_consolida_o_turno_depois_da_requisicao():
    """A regressão que este teste tranca é a ORDEM, e ela some sem alarde.

    Trocar as duas linhas de lugar não quebra nada visível: o James responde
    igual, os testes de fluxo passam, e o comando volta a viajar duas vezes em
    toda requisição — pagando tokens e confundindo modelos pequenos.
    """
    corpo = _fonte("_reason_turn")
    assert "self.llm.reason(" in corpo and "add_user_text(transcript)" in corpo
    assert corpo.index("self.llm.reason(") < corpo.index("add_user_text(transcript)")


def test_o_orquestrador_nao_consolida_antes_de_escolher_o_caminho():
    """`_process_transcript` não pode consolidar antes do roteador/raciocínio.

    Era exatamente ali que a linha estava.
    """
    corpo = _fonte("_process_transcript")
    primeira_consolidacao = corpo.index("add_user_text(transcript)")
    assert corpo.index("self.router.match(") < primeira_consolidacao


def test_a_segunda_rodada_manda_orientacao_e_nao_um_turno_novo():
    corpo = _fonte("_second_round")
    assert "instruction=" in corpo
    assert "text=None" in corpo
