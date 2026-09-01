"""A persona do James — o que separa um assistente de um chatbot com nome.

Alguém disse "acorda james, papai chegou" e ouviu de volta "posso mandar essa
foto para análise, senhor?". Duas falhas na mesma frase: um não-sequitur, e o
reflexo de oferecer serviço quando não se entende bem o que foi dito.

A causa estava no FORMATO do prompt: 52% das linhas de regra eram proibições
("nunca", "não"), e não havia um único exemplo de como ele fala. Prompt feito
só de proibição produz um modelo cauteloso e duro — que é exatamente o que soa
como chatbot. Regra diz o que evitar; exemplo diz quem ser.
"""

import re

from james.system_prompt import _BASE


def test_o_prompt_mostra_como_ele_fala():
    """Sem exemplo, "tom seco e espirituoso" é uma instrução que o modelo não
    tem como seguir — ele não sabe o que isso soa."""
    assert _BASE.count("Você: ") >= 5, "poucos exemplos de fala no prompt"


def test_o_exemplo_da_saudacao_brincalhona_existe():
    """Foi o caso concreto que quebrou. Vale estar no prompt, literal."""
    assert "papai chegou" in _BASE


def test_proibe_oferecer_servico_nao_pedido():
    """A frase que o usuário ouviu era uma oferta de capacidade, não resposta."""
    trecho = _BASE[_BASE.index("NUNCA OFEREÇA"):]
    for isca in ("Posso analisar", "Em que posso ajudar"):
        assert isca in trecho


def test_diz_o_que_fazer_quando_nao_entender():
    """O reflexo errado é preencher o silêncio oferecendo uma ferramenta.
    Proibir sem dar a alternativa deixa o modelo sem saída."""
    trecho = _BASE[_BASE.index("NUNCA OFEREÇA"):]
    assert "perguntar o que ele quis dizer" in trecho


def test_o_prompt_nao_e_so_proibicao():
    """Termômetro do defeito original: se as regras voltarem a ser quase todas
    negativas, a persona volta a endurecer."""
    linhas = [l for l in _BASE.splitlines() if l.strip().startswith("-")]
    negativas = [l for l in linhas if re.search(r"\b([Nn]unca|NUNCA|[Nn]ão|NÃO)\b", l)]
    assert len(negativas) / len(linhas) < 0.60, (
        f"{len(negativas)} de {len(linhas)} regras são proibições — "
        "prompt só de 'não' produz assistente duro"
    )


def test_exemplos_so_usam_capacidades_reais():
    """Um exemplo que mostra o James oferecendo algo que ele não faz ensina
    exatamente a alucinação que a gente quer evitar."""
    assert "volume das notificações" not in _BASE
