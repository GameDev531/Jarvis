"""AG-UI — o protocolo, e as duas coisas que quase deram errado.

O AG-UI não é uma lista de eventos: é uma sequência com gramática. E o que
torna os erros dela perigosos é que **o navegador não quebra** com nenhum
deles. Um `TEXT_MESSAGE_CONTENT` sem `START` some; um delta descartado tira uma
palavra do meio da frase; um run sem `RUN_FINISHED` deixa a tela girando. Em
todos, a pessoa conclui que o assistente "ficou ruim", não que há um bug.

Dois achados que só apareceram ao encaixar o protocolo no que já existia:

1. `StateBus` e AG-UI têm modelos de confiabilidade OPOSTOS. O barramento
   descarta o evento mais antigo quando o assinante não acompanha — certo para
   estado, catastrófico para delta de mensagem.

2. O instantâneo inicial já vem com `estado=PRONTO`, porque o James está
   parado quando a aba abre. Fechar o run no primeiro PRONTO o mataria antes
   de começar.
"""

from __future__ import annotations

import pytest

from james.agui import eventos as ev
from james.agui.adaptador import AdaptadorDeEstado, AdaptadorDeFerramenta
from james.agui.fluxo import DESCARTAVEIS, TAMANHO_DA_FILA, FluxoDeRun
from james.agui.sequencia import OrdemInvalida, Sequencia


# ------------------------------------------------------- formato de fio


def test_o_json_sai_em_camelCase():
    """O contrato é o JSON. Python usa snake_case; a tradução acontece num
    lugar só, e é aqui que se garante que ela aconteceu."""
    e = ev.run_started("t1", "r1", parent_run_id="r0")
    assert e["threadId"] == "t1" and e["runId"] == "r1" and e["parentRunId"] == "r0"
    assert "thread_id" not in e


def test_campo_opcional_ausente_nao_vira_null():
    """`parentRunId: null` e ausência não são a mesma coisa para todo cliente."""
    assert "parentRunId" not in ev.run_started("t", "r")


def test_todo_evento_tem_type_e_timestamp():
    for e in (ev.run_started("t", "r"), ev.step_started("x"),
              ev.state_snapshot({}), ev.custom("jarvis.x", 1)):
        assert e["type"] and isinstance(e["timestamp"], int)


def test_delta_de_texto_vazio_e_recusado():
    """Delta vazio não transporta nada e esconde um bug de quem produz."""
    with pytest.raises(ValueError):
        ev.text_message_content("m1", "")


# ------------------------------------------------------------- a gramática


def _seq():
    s = Sequencia("t", "r")
    s.validar(ev.run_started("t", "r"))
    return s


def test_fluxo_valido_passa_inteiro():
    s = Sequencia("t", "r")
    for e in (
        ev.run_started("t", "r"),
        ev.step_started("pesquisa"),
        ev.text_message_start("m1"),
        ev.text_message_content("m1", "olá"),
        ev.text_message_end("m1"),
        ev.step_finished("pesquisa"),
        ev.run_finished("t", "r"),
    ):
        s.validar(e)
    assert s.terminou and s.pendencias == {"mensagens": [], "chamadas": [], "passos": []}


def test_evento_antes_do_run_started_e_recusado():
    with pytest.raises(OrdemInvalida, match="antes do RUN_STARTED"):
        Sequencia("t", "r").validar(ev.step_started("x"))


def test_conteudo_sem_start_e_recusado():
    """O erro que faz a frase sumir sem ninguém notar."""
    with pytest.raises(OrdemInvalida, match="sem START"):
        _seq().validar(ev.text_message_content("m9", "texto perdido"))


def test_reabrir_mensagem_fechada_e_recusado():
    """Anexar texto novo numa bolha antiga é o pior tipo de erro: parece
    funcionar."""
    s = _seq()
    s.validar(ev.text_message_start("m1"))
    s.validar(ev.text_message_end("m1"))
    with pytest.raises(OrdemInvalida, match="já foi encerrado"):
        s.validar(ev.text_message_start("m1"))


def test_resultado_antes_do_end_e_recusado():
    """No James isto é mais que erro de protocolo: significaria ter executado
    a ferramenta com os argumentos incompletos, e ANTES do guard decidir."""
    s = _seq()
    s.validar(ev.tool_call_start("c1", "buscar"))
    with pytest.raises(OrdemInvalida, match="antes do END"):
        s.validar(ev.tool_call_result("c1", "m", "{}"))


def test_argumentos_sem_start_sao_recusados():
    with pytest.raises(OrdemInvalida, match="sem START"):
        _seq().validar(ev.tool_call_args("c9", '{"a":'))


def test_dois_run_finished_sao_recusados():
    s = _seq()
    s.validar(ev.run_finished("t", "r"))
    with pytest.raises(OrdemInvalida, match="depois do fim"):
        s.validar(ev.run_finished("t", "r"))


def test_passo_repetido_fecha_o_mais_recente():
    """Passos podem se repetir; fechar o mais antigo embaralharia a duração."""
    s = _seq()
    s.validar(ev.step_started("busca"))
    s.validar(ev.step_started("busca"))
    s.validar(ev.step_finished("busca"))
    assert s.pendencias["passos"] == ["busca"]


def test_step_finished_sem_started_e_recusado():
    with pytest.raises(OrdemInvalida, match="sem STEP_STARTED"):
        _seq().validar(ev.step_finished("nunca_comecou"))


# -------------------------------------- confiabilidade: o achado principal


def test_estado_pode_ser_descartado():
    """Perder um `estado` velho não custa nada: o próximo corrige."""
    f = FluxoDeRun("t")
    f.iniciar()
    for i in range(TAMANHO_DA_FILA + 50):
        f.emitir(ev.state_delta([{"op": "replace", "path": "/n", "value": i}]))
    assert f.descartados > 0
    assert not f.sequencia.terminou, "o run não devia morrer por estado descartado"


def test_delta_de_mensagem_NAO_pode_ser_descartado():
    """O ponto do arquivo inteiro.

    O cliente concatena os deltas na ordem. Um descartado no meio faz a frase
    chegar com um pedaço faltando — e a tela mostra algo que parece completo.
    Melhor um erro honesto que uma resposta mutilada.
    """
    f = FluxoDeRun("t")
    f.iniciar()
    f.emitir(ev.text_message_start("m1"))
    for i in range(TAMANHO_DA_FILA + 50):
        if not f.emitir(ev.text_message_content("m1", f"pedaco{i} ")):
            break
    tipos = []
    while not f.terminou:
        e = f.ler(timeout=0.05)
        if e:
            tipos.append(e["type"])
    assert tipos[-1] == "RUN_ERROR", "o cliente ficaria esperando um fim que não vem"


def test_o_run_error_cabe_mesmo_com_a_fila_cheia():
    """Sem abrir espaço para a má notícia, o erro causado por fila cheia não
    teria lugar na fila cheia — e o cliente esperaria para sempre."""
    f = FluxoDeRun("t")
    f.iniciar()
    f.emitir(ev.text_message_start("m1"))
    for i in range(TAMANHO_DA_FILA + 20):
        if not f.emitir(ev.text_message_content("m1", "x")):
            break
    entregues = []
    while not f.terminou:
        e = f.ler(timeout=0.05)
        if e:
            entregues.append(e)
    assert any(e["type"] == "RUN_ERROR" for e in entregues)


def test_a_classificacao_cobre_o_que_importa():
    """Estado e atividade caem; ciclo de vida, mensagem e ferramenta, não."""
    assert ev.TipoEvento.STATE_DELTA.value in DESCARTAVEIS
    assert ev.TipoEvento.ACTIVITY_DELTA.value in DESCARTAVEIS
    for essencial in ("RUN_STARTED", "RUN_FINISHED", "TEXT_MESSAGE_CONTENT",
                      "TOOL_CALL_ARGS", "TOOL_CALL_RESULT"):
        assert essencial not in DESCARTAVEIS, f"{essencial} não pode ser descartado"


def test_emitir_nunca_bloqueia_a_thread_de_quem_produz():
    """O orquestrador não pode engasgar porque uma aba parou de ler."""
    import time

    f = FluxoDeRun("t")
    f.iniciar()
    inicio = time.monotonic()
    for i in range(TAMANHO_DA_FILA * 3):
        f.emitir(ev.state_delta([{"op": "replace", "path": "/n", "value": i}]))
    assert time.monotonic() - inicio < 1.0


def test_ordem_invalida_mata_o_run_em_vez_de_emitir():
    """Erro de programação nosso não pode virar sequência que o cliente não
    consegue montar."""
    f = FluxoDeRun("t")
    f.iniciar()
    assert f.emitir(ev.text_message_content("m-inexistente", "x")) is False
    entregues = []
    while not f.terminou:
        e = f.ler(timeout=0.05)
        if e:
            entregues.append(e["type"])
    assert "RUN_ERROR" in entregues


# ------------------------------------------------------------- adaptador


def test_a_fronteira_do_barramento_e_a_do_protocolo():
    """O `StateBus` já separava estado de acontecimento antes de existir AG-UI
    aqui. As duas fronteiras são a mesma, e a constante é importada de lá para
    não virarem duas."""
    from james.ui.bus import CHAVES_EFEMERAS

    # `turno` entrou depois, quando o orquestrador passou a marcar o início e
    # o fim explicitamente — e é acontecimento como os outros três.
    assert CHAVES_EFEMERAS == {"log", "transcricao", "resposta", "turno"}


def _coletar(fluxo) -> list[dict]:
    saida = []
    while True:
        e = fluxo.ler(timeout=0.05)
        if e is None:
            return saida
        saida.append(e)


def test_transcricao_vira_mensagem_do_usuario():
    f = FluxoDeRun("t"); f.iniciar()
    AdaptadorDeEstado(f, fechar_no_fim=False).publicar({"transcricao": "que horas são"})
    tipos = [e for e in _coletar(f) if e["type"] == "TEXT_MESSAGE_START"]
    assert tipos and tipos[0]["role"] == "user"


def test_resposta_vira_mensagem_do_assistente():
    f = FluxoDeRun("t"); f.iniciar()
    AdaptadorDeEstado(f, fechar_no_fim=False).publicar({"resposta": "Quase quatro."})
    inicios = [e for e in _coletar(f) if e["type"] == "TEXT_MESSAGE_START"]
    assert inicios and inicios[0]["role"] == "assistant"


def test_primeira_publicacao_manda_snapshot_e_nao_delta():
    """Delta sobre um estado que o cliente não tem é patch no vazio."""
    f = FluxoDeRun("t"); f.iniciar()
    AdaptadorDeEstado(f, fechar_no_fim=False).publicar({"estado": "OUVINDO"})
    tipos = [e["type"] for e in _coletar(f)]
    assert "STATE_SNAPSHOT" in tipos and "STATE_DELTA" not in tipos


def test_mudanca_seguinte_vira_delta():
    f = FluxoDeRun("t"); f.iniciar()
    a = AdaptadorDeEstado(f, fechar_no_fim=False)
    a.snapshot({"estado": "PRONTO"})
    a.publicar({"estado": "PENSANDO"})
    deltas = [e for e in _coletar(f) if e["type"] == "STATE_DELTA"]
    assert deltas[0]["delta"] == [
        {"op": "replace", "path": "/estado", "value": "PENSANDO"}
    ]


def test_chave_nova_usa_add_e_nao_replace():
    """`replace` em chave inexistente é erro pelo RFC 6902, e alguns clientes
    levam isso a sério."""
    f = FluxoDeRun("t"); f.iniciar()
    a = AdaptadorDeEstado(f, fechar_no_fim=False)
    a.snapshot({"estado": "PRONTO"})
    a.publicar({"vitals": {"cpu": 10}})
    delta = [e for e in _coletar(f) if e["type"] == "STATE_DELTA"][0]
    assert delta["delta"][0]["op"] == "add"


def test_valor_igual_nao_gera_evento():
    f = FluxoDeRun("t"); f.iniciar()
    a = AdaptadorDeEstado(f, fechar_no_fim=False)
    a.snapshot({"estado": "PRONTO"})
    a.publicar({"estado": "PRONTO"})
    assert not [e for e in _coletar(f) if e["type"] == "STATE_DELTA"]


# -------------------------------------------- o fim do run, e a armadilha


def test_o_snapshot_inicial_NAO_fecha_o_run():
    """A armadilha: o James está PRONTO quando a aba abre. Fechar no primeiro
    PRONTO mataria o run antes de ele começar, e a tela mostraria vazio."""
    f = FluxoDeRun("t"); f.iniciar()
    a = AdaptadorDeEstado(f)
    a.snapshot({"estado": "PRONTO"})
    a.publicar({"estado": "PRONTO"})
    assert not f.sequencia.terminou


def test_voltar_ao_ocioso_DEPOIS_de_trabalhar_fecha_o_run():
    """Sem isso o cliente fica pendurado esperando um fim que não vem."""
    f = FluxoDeRun("t"); f.iniciar()
    a = AdaptadorDeEstado(f)
    a.snapshot({"estado": "PRONTO"})
    a.publicar({"estado": "PENSANDO"})
    a.publicar({"estado": "PRONTO"})
    assert f.sequencia.terminou
    assert [e["type"] for e in _coletar(f)][-1] == "RUN_FINISHED"


# ------------------------------------------- ferramenta: o guard no meio


def test_a_ordem_poe_o_guard_entre_a_intencao_e_o_resultado():
    """O AG-UI transporta a INTENÇÃO e o RESULTADO; ele não decide se pode.

    Emitir o resultado antes de o guard falar mostraria na tela algo que talvez
    nunca devesse existir — e sugeriria, a quem lê o código, que a execução
    acontece no protocolo em vez de no guard.
    """
    f = FluxoDeRun("t"); f.iniciar()
    t = AdaptadorDeFerramenta(f)
    tid = t.anunciar("buscar_na_web", '{"consulta":"x"}')
    t.decisao(tid, "allow", "sem risco")
    t.resultado(tid, '{"resultados": 5}')

    tipos = [e["type"] for e in _coletar(f)]
    assert tipos.index("TOOL_CALL_END") < tipos.index("CUSTOM") < tipos.index("TOOL_CALL_RESULT")


def test_a_decisao_do_guard_vai_como_custom_nomeado():
    f = FluxoDeRun("t"); f.iniciar()
    AdaptadorDeFerramenta(f).decisao("c1", "confirm", "acao no navegador")
    custom = [e for e in _coletar(f) if e["type"] == "CUSTOM"][0]
    assert custom["name"] == "jarvis.guard.decision"
    assert custom["value"]["decision"] == "confirm"


# ----------------------------------- a marca explícita de turno (Fase 21)

# A heurística "voltou para PRONTO, então acabou" funcionava e tinha uma
# armadilha real: o James também está PRONTO quando ninguém pediu nada, e o
# estado pode passar por PRONTO no meio de um turno. Agora o orquestrador diz
# onde o turno começa e acaba, publicando `turno="inicio"` / `turno="fim"` — o
# segundo num `finally`, para que um turno que estoura feche o run do mesmo
# jeito.


def _com_marca(passos, snapshot_inicial="PRONTO"):
    f = FluxoDeRun("t")
    f.iniciar()
    a = AdaptadorDeEstado(f)
    a.snapshot({"estado": snapshot_inicial})
    for passo in passos:
        a.publicar(passo)
    return f


def test_turno_fim_fecha_o_run():
    f = _com_marca([{"turno": "inicio"}, {"estado": "PENSANDO"}, {"turno": "fim"}])
    assert f.sequencia.terminou


def test_pronto_no_meio_do_turno_nao_fecha():
    """O ponto da mudança. Com a heurística sozinha, um `estado=PRONTO`
    passageiro fecharia o run antes de a resposta sair."""
    f = _com_marca([{"turno": "inicio"}, {"estado": "PRONTO"}, {"estado": "PENSANDO"}])
    assert not f.sequencia.terminou


def test_a_marca_desliga_o_palpite():
    """Deixar os dois ativos seria pior que só o palpite: a presença da marca
    já prova que quem publica sabe o que está fazendo."""
    f = FluxoDeRun("t")
    f.iniciar()
    a = AdaptadorDeEstado(f)
    a.snapshot({"estado": "PRONTO"})
    a.publicar({"turno": "inicio"})
    assert a._explicito is True


def test_a_heuristica_continua_valendo_sem_a_marca():
    """Reserva para quem publica sem ser o orquestrador — um teste, uma
    ferramenta isolada. Tirar a heurística deixaria esses casos pendurados."""
    f = _com_marca([{"estado": "PENSANDO"}, {"estado": "PRONTO"}])
    assert f.sequencia.terminou


def test_turno_e_efemero_e_nao_vira_estado():
    """`turno` é acontecimento. Guardá-lo no instantâneo faria uma aba que
    recarrega ver "fim" e fechar o run recém-aberto."""
    from james.ui.bus import CHAVES_EFEMERAS

    assert "turno" in CHAVES_EFEMERAS


def test_o_orquestrador_fecha_o_turno_no_finally():
    """Um turno que estoura precisa fechar o run do mesmo jeito, senão a tela
    fica girando esperando um fim que a exceção levou embora."""
    from pathlib import Path

    fonte = (Path(__file__).resolve().parent.parent
             / "james" / "runtime" / "orchestrator.py").read_text(encoding="utf-8")
    depois_do_finally = fonte.split("finally:")
    assert any('turno="fim"' in trecho for trecho in depois_do_finally[1:]), (
        "a marca de fim precisa estar num finally"
    )
