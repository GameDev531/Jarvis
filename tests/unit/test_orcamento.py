"""O teto de contexto, e o que ele sacrifica primeiro.

`max_turns=12` conta TURNOS, não tamanho. Doze turnos podem ser 3 KB ou 100 KB,
e o código não sabia a diferença — foi assim que o histórico virou 54% do
contexto numa conversa de quatro leituras de página.

O risco desta camada não é cortar demais: é cortar ERRADO. Duas formas:

  - cortar o turno atual, e o James responde a meia pergunta;
  - quebrar um par chamada/resposta, e a API rejeita o histórico inteiro — o
    corte que devia salvar a requisição é o que a derruba.

A segunda é a pior porque parece funcionar em teste pequeno: com um par só, o
corte cai fora dele por sorte.
"""

from __future__ import annotations

import json

import pytest

from james.llm.history import ToolCall
from james.llm.message_builder import (
    ORIGEM_ATUAL,
    ORIGEM_HISTORICO,
    ORIGEM_ORIENTACAO,
    ORIGEM_SISTEMA,
    Mensagem,
)
from james.llm.orcamento import (
    SOBRA_DE_RESULTADO,
    Medida,
    aplicar,
    medir,
    tamanho,
)


def sistema(texto="prompt do sistema"):
    return Mensagem(role="system", text=texto, origem=ORIGEM_SISTEMA)


def usuario(texto, origem=ORIGEM_HISTORICO):
    return Mensagem(role="user", text=texto, origem=origem)


def modelo(texto="", chamadas=None):
    return Mensagem(
        role="model", text=texto, tool_calls=list(chamadas or []),
        origem=ORIGEM_HISTORICO,
    )


def resultado(nome="ler_pagina", dados=None, call_id="1"):
    return Mensagem(
        role="tool", tool_name=nome, call_id=call_id,
        tool_result=dados if dados is not None else {"texto": "x" * 4000},
        origem=ORIGEM_HISTORICO,
    )


def par(call_id="1", tamanho_dados=4000):
    """Uma chamada e a resposta dela — o que nunca pode ser separado."""
    return [
        modelo(chamadas=[ToolCall(name="ler_pagina", call_id=call_id)]),
        resultado(dados={"texto": "x" * tamanho_dados}, call_id=call_id),
    ]


def tem_orfao(mensagens) -> bool:
    """Um resultado sem a chamada correspondente antes dele."""
    abertas = set()
    for m in mensagens:
        for c in (m.tool_calls or []):
            abertas.add(c.call_id)
        if m.tool_result is not None:
            if m.call_id not in abertas:
                return True
            abertas.discard(m.call_id)
    return False


# ------------------------------------------------------------------ a medida


def test_a_medida_separa_as_partes():
    msgs = [sistema("s" * 100), usuario("u" * 50), resultado(dados={"a": "b" * 200})]
    m = medir(msgs)
    assert m.sistema == 100
    assert m.historico == 50
    assert m.resultados > 200
    assert m.total == m.sistema + m.historico + m.resultados


def test_o_relatorio_diz_a_fracao_de_cada_parte():
    """É o que responde "por que está tão grande?" sem ninguém ler JSON."""
    texto = medir([sistema("s" * 900), resultado(dados={"a": "b" * 9000})]).relatorio()
    assert "system prompt" in texto and "resultados de tool" in texto
    assert "TOTAL" in texto


def test_audio_conta_como_base64():
    """Áudio cru enganaria a conta: ele cresce ~4/3 ao virar base64, e é isso
    que ocupa lugar na requisição."""
    msg = Mensagem(role="user", audio_wav=b"\x00" * 3000, origem=ORIGEM_ATUAL)
    assert medir([msg]).audio == 4000


def test_tamanho_de_estrutura_nao_estoura_com_tipo_estranho():
    class Esquisito:
        pass

    assert tamanho(Esquisito()) > 0
    assert tamanho(None) == 0


# ---------------------------------------------------------- o que não corta


def test_contexto_pequeno_passa_intacto():
    msgs = [sistema(), usuario("oi"), modelo("olá")]
    copia = list(msgs)
    corte = aplicar(msgs, teto=10_000)
    assert corte.houve_corte is False
    assert msgs == copia


def test_o_system_prompt_nunca_e_cortado():
    """Um system prompt cortado é o James virando outra pessoa no meio da
    conversa — pior que estourar o teto."""
    msgs = [sistema("S" * 20_000), *par("1", 20_000), usuario("agora", ORIGEM_ATUAL)]
    aplicar(msgs, teto=5_000)
    assert msgs[0].text == "S" * 20_000


def test_o_turno_atual_nunca_e_cortado():
    """Cortá-lo é o James respondendo a meia pergunta."""
    pergunta = "P" * 6_000
    msgs = [sistema(), *par("1", 20_000), usuario(pergunta, ORIGEM_ATUAL)]
    aplicar(msgs, teto=5_000)
    assert any(m.text == pergunta for m in msgs)


def test_a_orientacao_do_turno_sobrevive():
    """Ela diz ao modelo o que fazer AGORA; sem ela a segunda volta perde o
    enunciado e o modelo inventa o que estava fazendo."""
    msgs = [
        sistema(), *par("1", 30_000),
        Mensagem(role="system", text="Relate o resultado.", origem=ORIGEM_ORIENTACAO),
        usuario("x", ORIGEM_ATUAL),
    ]
    aplicar(msgs, teto=2_000)
    assert any(m.origem == ORIGEM_ORIENTACAO for m in msgs)


# ------------------------------------------------------------ o que corta


def test_resultado_antigo_encolhe_antes_de_qualquer_turno_sair():
    """A ordem importa: o detalhe de um resultado já lido é o que menos custa
    perder, e sai antes da conversa."""
    msgs = [sistema(), *par("1", 30_000), usuario("agora", ORIGEM_ATUAL)]
    quantas = len(msgs)

    corte = aplicar(msgs, teto=5_000)
    assert corte.resultados_encolhidos == 1
    assert corte.turnos_removidos == 0
    assert len(msgs) == quantas


def test_o_resultado_encolhido_ainda_diz_que_existiu():
    """Apagar por completo faria o modelo achar que a ferramenta não rodou."""
    msgs = [sistema(), *par("1", 30_000), usuario("agora", ORIGEM_ATUAL)]
    aplicar(msgs, teto=5_000)
    sobrou = next(m for m in msgs if m.tool_result is not None)
    assert sobrou.tool_result
    assert tamanho(sobrou.tool_result) <= SOBRA_DE_RESULTADO * 3


def test_o_encolhimento_preserva_a_forma():
    """JSON cortado no meio é texto inválido, e modelo lendo JSON quebrado
    alucina o resto com confiança."""
    dados = {"titulo": "t" * 100, "itens": [{"a": "b" * 500} for _ in range(20)]}
    msgs = [sistema(), modelo(chamadas=[ToolCall(name="x", call_id="1")]),
            resultado(dados=dados, call_id="1"), usuario("agora", ORIGEM_ATUAL)]
    aplicar(msgs, teto=1_000)

    sobrou = next(m for m in msgs if m.tool_result is not None).tool_result
    # Continua serializável, que é o que a API precisa.
    json.dumps(sobrou, ensure_ascii=False)
    assert isinstance(sobrou, dict)


def test_turnos_saem_quando_encolher_nao_basta():
    msgs = [sistema()]
    for i in range(8):
        msgs += [usuario(f"pergunta {i} " + "p" * 2000), modelo("resposta " + "r" * 2000)]
    msgs.append(usuario("agora", ORIGEM_ATUAL))

    corte = aplicar(msgs, teto=6_000)
    assert corte.turnos_removidos > 0
    assert medir(msgs).total <= 6_000


# -------------------------------------------------- a invariante do par


def conversa_que_forca_remocao(pares=8, texto=3000, dados=3000):
    """Um histórico em que encolher resultados NÃO basta.

    Isto é o detalhe que a primeira versão destes testes errou. Encolher um
    resultado para 300 caracteres é tão eficaz que, com resultados grandes e
    conversa curta, o teto já é atingido no passo 1 — e o passo 2, que remove
    turnos, nunca roda. Os testes de órfão passavam com a lógica de par
    INTEIRAMENTE removida, porque nunca chegavam nela.

    Só se descobre isso sabotando o código e vendo o teste continuar verde.

    Aqui o texto da conversa é grande de propósito: encolher resultados não
    resolve, a remoção de turnos precisa acontecer, e ela acontece com pares no
    meio — que é o cenário que importa.
    """
    msgs = [sistema()]
    for i in range(pares):
        msgs += [usuario(f"pergunta {i} " + "p" * texto), *par(str(i), dados)]
    msgs.append(usuario("agora", ORIGEM_ATUAL))
    return msgs


def test_a_conversa_de_teste_realmente_forca_a_remocao():
    """Guarda do guarda: se um dia encolher passar a bastar aqui, os testes de
    órfão abaixo voltam a ser decorativos — e em silêncio."""
    msgs = conversa_que_forca_remocao()
    corte = aplicar(msgs, teto=4_000)
    assert corte.turnos_removidos > 0, (
        "o passo de remoção não rodou; os testes de órfão não testam nada"
    )


def test_o_corte_nunca_deixa_resultado_orfao():
    """A pior falha possível: a API rejeita o histórico inteiro, e o corte que
    devia salvar a requisição é o que a derruba."""
    msgs = conversa_que_forca_remocao()
    aplicar(msgs, teto=4_000)
    assert not tem_orfao(msgs), "sobrou resposta de ferramenta sem a chamada"


def test_nenhum_teto_produz_orfao():
    """Varredura, e não uma lista de tetos escolhidos a dedo. Eis por quê.

    A remoção tira uma mensagem por vez, do começo. Tirando a mensagem do
    modelo que fez a chamada, a resposta dela fica na frente — e seria removida
    na iteração seguinte, então o par some inteiro e nada quebra. Quase sempre.

    O órfão só sobra quando o teto é atingido EXATAMENTE entre as duas
    remoções: a chamada saiu, a resposta ficou, e o laço parou ali no meio.

    Medido contra o código com a lógica de par removida: de 570 tetos entre 300
    e 6.000, apenas DOIS produzem órfão. Uma lista de cinco tetos escolhidos à
    mão tem chance praticamente nula de acertar um deles — e foi assim que a
    primeira versão deste teste passou com a proteção inteiramente ausente.

    A varredura acha os dois.
    """
    orfaos = []
    for teto in range(300, 6_000, 10):
        msgs = conversa_que_forca_remocao(pares=6, texto=1500, dados=2000)
        aplicar(msgs, teto=teto)
        if tem_orfao(msgs):
            orfaos.append(teto)
    assert not orfaos, f"resposta de ferramenta sem a chamada, nos tetos: {orfaos}"


def test_duas_chamadas_no_mesmo_turno_saem_juntas():
    """O modelo pode pedir duas ferramentas de uma vez; separar as respostas
    da chamada dupla é o mesmo erro, só mais difícil de ver."""
    msgs = [
        sistema(),
        # Texto grande para que a remoção de turnos precise rodar de verdade.
        usuario("faz as duas coisas " + "z" * 4000),
        modelo(chamadas=[
            ToolCall(name="a", call_id="1"), ToolCall(name="b", call_id="2"),
        ]),
        resultado("a", {"x": "y" * 5000}, "1"),
        resultado("b", {"x": "y" * 5000}, "2"),
        usuario("depois disso " + "w" * 4000),
        usuario("agora", ORIGEM_ATUAL),
    ]
    corte = aplicar(msgs, teto=1_500)
    assert corte.turnos_removidos > 0
    assert not tem_orfao(msgs)


# ----------------------------------------------------------- o caso extremo


def test_quando_o_intocavel_ja_estoura_ele_avisa_e_nao_mutila():
    """Sistema + turno atual sozinhos acima do teto: cortar qualquer um dos
    dois é pior que estourar. O código tenta e deixa o provedor recusar."""
    msgs = [sistema("S" * 30_000), usuario("P" * 30_000, ORIGEM_ATUAL)]
    corte = aplicar(msgs, teto=1_000)

    assert corte.coube is False
    assert len(msgs[0].text) == 30_000
    assert len(msgs[1].text) == 30_000


def test_lista_vazia_nao_estoura():
    assert aplicar([], teto=100).houve_corte is False


def test_so_o_sistema_nao_estoura():
    msgs = [sistema("s" * 5000)]
    aplicar(msgs, teto=100)
    assert len(msgs) == 1
