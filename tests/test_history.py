"""Histórico: o pareamento de tool calls é o que quebra o fire-and-forget (C10)."""

from james.llm.history import AUDIO_PLACEHOLDER, Conversation, ToolCall


def test_turnos_simples_entram_na_ordem():
    conversa = Conversation(max_turns=10)
    conversa.add_user_text("oi")
    conversa.add_model_response("Boa noite, senhor.")
    turnos = conversa.turns()
    assert [t.role for t in turnos] == ["user", "model"]


def test_tool_call_sem_resposta_fica_pendente():
    conversa = Conversation()
    conversa.add_user_text("abre o chrome")
    conversa.add_model_response("Abrindo.", [ToolCall(name="abrir_app", args={"nome": "chrome"})])
    assert len(conversa.pending_tool_calls()) == 1


def test_fire_and_forget_e_fechado_com_resultado_sintetico():
    """Sem isto, a requisição seguinte quebra: a API exige o par completo."""
    conversa = Conversation()
    conversa.add_user_text("abre o chrome")
    conversa.add_model_response("Abrindo.", [ToolCall(name="abrir_app")])

    fechadas = conversa.close_pending_tool_calls()

    assert fechadas == 1
    assert conversa.pending_tool_calls() == []
    ultimo = conversa.turns()[-1]
    assert ultimo.role == "tool" and ultimo.synthetic is True


def test_fechar_e_idempotente():
    conversa = Conversation()
    conversa.add_model_response("", [ToolCall(name="abrir_app")])
    assert conversa.close_pending_tool_calls() == 1
    assert conversa.close_pending_tool_calls() == 0


def test_resultado_real_nao_e_duplicado_por_um_sintetico():
    conversa = Conversation()
    conversa.add_model_response("", [ToolCall(name="que_horas_sao")])
    conversa.add_tool_result("que_horas_sao", {"hora": "14:30"})
    assert conversa.close_pending_tool_calls() == 0


def test_mesma_tool_chamada_duas_vezes_parear_por_id():
    """Parear só por nome marcaria a segunda chamada como já respondida."""
    conversa = Conversation()
    conversa.add_model_response(
        "",
        [
            ToolCall(name="abrir_app", args={"nome": "chrome"}, call_id="c1"),
            ToolCall(name="abrir_app", args={"nome": "spotify"}, call_id="c2"),
        ],
    )
    conversa.add_tool_result("abrir_app", {"ok": True}, call_id="c1")

    pendentes = conversa.pending_tool_calls()
    assert len(pendentes) == 1
    assert pendentes[0].call_id == "c2"


def test_mesma_tool_duas_vezes_sem_id_pareia_por_ordem():
    conversa = Conversation()
    conversa.add_model_response("", [ToolCall(name="abrir_app"), ToolCall(name="abrir_app")])
    conversa.add_tool_result("abrir_app", {"ok": True})
    assert len(conversa.pending_tool_calls()) == 1


def test_varias_tools_diferentes_no_mesmo_turno():
    conversa = Conversation()
    conversa.add_model_response(
        "", [ToolCall(name="abrir_app"), ToolCall(name="pesquisar_web")]
    )
    conversa.add_tool_result("abrir_app", {"ok": True})
    pendentes = conversa.pending_tool_calls()
    assert [c.name for c in pendentes] == ["pesquisar_web"]


# ------------------------------------------------------- transcrição tardia

def test_comando_de_voz_entra_como_marcador():
    conversa = Conversation()
    indice = conversa.add_user_audio()
    assert conversa.turns()[indice].text == AUDIO_PLACEHOLDER
    assert conversa.turns()[indice].transcribed is False


def test_transcricao_em_segundo_plano_preenche_o_turno():
    conversa = Conversation()
    indice = conversa.add_user_audio()
    assert conversa.set_user_transcript(indice, "abre o chrome") is True
    assert conversa.turns()[indice].text == "abre o chrome"
    assert conversa.turns()[indice].transcribed is True


def test_transcricao_nao_sobrescreve_turno_ja_transcrito():
    conversa = Conversation()
    indice = conversa.add_user_audio()
    conversa.set_user_transcript(indice, "primeiro")
    assert conversa.set_user_transcript(indice, "segundo") is False
    assert conversa.turns()[indice].text == "primeiro"


def test_transcricao_de_indice_invalido_nao_explode():
    conversa = Conversation()
    assert conversa.set_user_transcript(99, "texto") is False
    assert conversa.set_user_transcript(-1, "texto") is False


def test_transcricao_vazia_e_ignorada():
    conversa = Conversation()
    indice = conversa.add_user_audio()
    assert conversa.set_user_transcript(indice, "   ") is False


# --------------------------------------------------------------- poda

def test_poda_respeita_o_limite():
    conversa = Conversation(max_turns=4)
    for i in range(10):
        conversa.add_user_text(f"msg {i}")
    assert len(conversa) <= 4


def test_poda_nunca_deixa_resposta_de_tool_orfa_no_inicio():
    """Histórico começando com function_response solto é rejeitado pela API."""
    conversa = Conversation(max_turns=3)
    conversa.add_user_text("oi")
    conversa.add_model_response("", [ToolCall(name="abrir_app")])
    conversa.add_tool_result("abrir_app", {"ok": True})
    conversa.add_tool_result("abrir_app", {"ok": True})
    conversa.add_user_text("e agora")

    turnos = conversa.turns()
    assert turnos and turnos[0].role != "tool"


def test_limpar_zera_o_historico():
    conversa = Conversation()
    conversa.add_user_text("oi")
    conversa.clear()
    assert len(conversa) == 0
