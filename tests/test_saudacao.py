"""A saudação da partida.

Alguém ligou o James e ele ficou calado até ser chamado — aí veio a
apresentação completa, como resposta a "acorda james papai chegou". Duas
coisas erradas de uma vez: **saudação que só sai depois de você falar não é
saudação**, é preâmbulo colado na resposta; e a apresentação longa é da
primeira vez, não de toda vez.

O motivo do desenho antigo era economia: a saudação viajava junto com o
primeiro comando para não gastar uma requisição. Consertar não custou essa
requisição, porque cumprimentar não precisa de modelo nenhum — é hora do dia
mais um nome.
"""

import random
from datetime import datetime

from james.greeting import periodo, saudacao


def em(hora, minuto=0):
    return datetime(2026, 9, 1, hora, minuto)


# ------------------------------------------------------------- por período


def test_periodos_cobrem_o_dia_inteiro():
    """Um buraco aqui deixaria `saudacao` sem lista para sortear."""
    assert {periodo(h) for h in range(24)} == {"madrugada", "manha", "tarde", "noite"}


def test_madrugada_nao_diz_boa_noite():
    """Quem liga às 3 da manhã não quer 'boa noite' — quer pouca cerimônia."""
    for _ in range(20):
        frase = saudacao(momento=em(3))
        assert "boa noite" not in frase.lower()


def test_a_frase_sempre_vem_da_lista_do_periodo_certo():
    """Nem toda frase nomeia o período — "Pronto quando o senhor estiver." é
    saudação de tarde e não contém a palavra "tarde". É de propósito: frase que
    sempre anuncia a hora vira bordão. A propriedade que importa é a origem.
    """
    from james.greeting import _FORMAS

    for hora, esperado in ((9, "manha"), (15, "tarde"), (21, "noite"), (3, "madrugada")):
        formas = {f.format(t="senhor", T="Senhor", nome="James") for f in _FORMAS[esperado]}
        for semente in range(30):
            assert saudacao(momento=em(hora), rng=random.Random(semente)) in formas


def test_usa_o_tratamento_configurado():
    frase = saudacao(tratamento="chefe", momento=em(9))
    assert "chefe" in frase.lower()
    assert "senhor" not in frase.lower()


def test_nao_vira_bordao():
    """Frase única todo dia cansa. Não precisa de modelo para variar — precisa
    de mais de uma forma por período."""
    vistas = {saudacao(momento=em(9), rng=random.Random(s)) for s in range(50)}
    assert len(vistas) > 1


# ------------------------------------------------------- primeira execução


def test_primeira_vez_se_apresenta():
    frase = saudacao(primeira_vez=True, nome="James")
    assert "James" in frase
    # As duas coisas que precisam estar certas nesta frase única da instalação.
    assert "confirmação" in frase.lower()


def test_primeira_vez_nao_faz_pergunta():
    """A versão antiga terminava com "como prefere ser chamado?" — e aí o James
    ficava esperando resposta antes de servir para qualquer coisa."""
    assert "?" not in saudacao(primeira_vez=True)


def test_apresentacao_nao_se_repete_nas_seguintes():
    normal = saudacao(momento=em(9))
    assert "assistente" not in normal.lower()
    assert len(normal) < 80, f"longa demais para uma saudação: {normal!r}"


# ------------------------------------------------- estado entre execuções


def test_estado_lembra_que_ja_cumprimentou(tmp_path):
    from james.state.runtime_state import RuntimeState

    estado = RuntimeState(tmp_path / "runtime_state.json")
    assert estado.segundos_desde_a_saudacao() == float("inf")
    estado.marcar_saudacao()
    assert estado.segundos_desde_a_saudacao() < 5


def test_a_marca_sobrevive_ao_processo_morrer(tmp_path):
    """O watchdog reinicia o orquestrador quando ele cai. Num ciclo de queda,
    sem esta marca em disco, o James cumprimentaria a cada reinício — e cada
    'boa noite' custa caracteres da cota de voz."""
    from james.state.runtime_state import RuntimeState

    caminho = tmp_path / "runtime_state.json"
    RuntimeState(caminho).marcar_saudacao()
    # Outro objeto = outro processo, na prática.
    assert RuntimeState(caminho).segundos_desde_a_saudacao() < 5


def test_primeira_execucao_e_marcada_em_disco(tmp_path):
    from james.state.runtime_state import RuntimeState

    caminho = tmp_path / "runtime_state.json"
    estado = RuntimeState(caminho)
    assert not estado.first_run_done()
    estado.mark_first_run_done()
    assert RuntimeState(caminho).first_run_done()
