"""Divisão em sentenças: o que quebra um split('.') ingênuo em português."""

import pytest

from james.voice.sentences import split_sentences, split_stream


def test_frase_unica():
    assert split_sentences("Boa noite, senhor.") == ["Boa noite, senhor."]


def test_duas_frases_sao_separadas():
    resultado = split_sentences(
        "Abri o navegador para o senhor. A pesquisa já está na tela agora."
    )
    assert len(resultado) == 2


def test_abreviacao_nao_quebra_a_frase():
    resultado = split_sentences("O Sr. Stark chegou ao laboratório principal agora.")
    assert len(resultado) == 1


@pytest.mark.parametrize(
    "texto",
    [
        "A conta ficou em R$ 1.500,00 no total final.",
        "A versão 3.5 já está instalada na máquina.",
        "O arquivo pesa 2.750 megabytes no disco.",
    ],
)
def test_ponto_em_numero_nao_quebra(texto):
    assert len(split_sentences(texto)) == 1


def test_reticencias_contam_como_um_terminador():
    resultado = split_sentences("Deixe-me verificar isso... Já encontrei a resposta.")
    assert len(resultado) == 2
    assert resultado[0].endswith("...")


def test_interrogacao_e_exclamacao_terminam_frase():
    resultado = split_sentences("Posso prosseguir com isso? Perfeito, então! Vou começar.")
    assert len(resultado) == 3


def test_dominio_no_meio_do_texto_nao_quebra():
    """'google.com' tem ponto sem espaço depois — não é fim de frase."""
    resultado = split_sentences("Abri o google.com para o senhor agora mesmo.")
    assert len(resultado) == 1


def test_fragmento_curto_gruda_no_seguinte():
    """'Sim.' sozinho sairia com prosódia estranha no TTS."""
    resultado = split_sentences("Sim. Já abri a pesquisa que o senhor pediu agora.")
    assert len(resultado) == 1


def test_fragmento_curto_no_fim_gruda_no_anterior():
    resultado = split_sentences("Já abri a pesquisa que o senhor pediu agora. Pronto.")
    assert len(resultado) == 1


def test_frase_longa_demais_e_quebrada():
    """Senão a primeira fala demora tanto quanto a resposta inteira."""
    longa = "primeira parte bem longa, " * 30
    resultado = split_sentences(longa, max_chars=120)
    assert len(resultado) > 1
    assert all(len(pedaco) <= 130 for pedaco in resultado)


def test_palavra_unica_gigante_nao_trava_o_algoritmo():
    resultado = split_sentences("a" * 900, max_chars=100)
    assert len(resultado) >= 9
    assert "".join(resultado).count("a") == 900


@pytest.mark.parametrize("texto", ["", "   ", "\n\n", None])
def test_texto_vazio_devolve_lista_vazia(texto):
    assert split_sentences(texto) == []


def test_texto_sem_pontuacao_vira_um_bloco():
    assert split_sentences("sem nenhuma pontuacao aqui nesta frase") == [
        "sem nenhuma pontuacao aqui nesta frase"
    ]


def test_espacos_e_quebras_sao_normalizados():
    resultado = split_sentences("Primeira   frase   aqui.\n\n  Segunda frase completa.")
    assert "  " not in resultado[0]
    assert len(resultado) == 2


def test_nada_do_texto_original_se_perde():
    texto = "Boa noite senhor. Já verifiquei o sistema. Tudo em ordem por aqui."
    juntado = " ".join(split_sentences(texto))
    for palavra in ("Boa", "verifiquei", "ordem"):
        assert palavra in juntado


def test_max_menor_que_min_e_erro_explicito():
    with pytest.raises(ValueError):
        split_sentences("texto", min_chars=100, max_chars=10)


# ------------------------------------------------------- split_stream

def test_stream_separa_completo_do_incompleto():
    prontas, resto = split_stream("Primeira frase completa aqui. Segunda pela")
    assert prontas == ["Primeira frase completa aqui."]
    assert resto.strip() == "Segunda pela"


def test_stream_preserva_o_espaco_do_resto():
    """Regressão: normalizar o resto colava a próxima frase na anterior.

    Sem o espaço, "…completa." + "Terceira…" vira "…completa.Terceira", que o
    divisor trata como uma frase só — a mesma regra que impede "google.com" de
    virar duas sentenças.
    """
    _, resto = split_stream("Primeira frase completa aqui. Segunda frase completa aqui. ")
    assert resto == " "

    prontas, resto2 = split_stream(resto + "Terceira frase para fechar.")
    assert prontas == ["Terceira frase para fechar."]
    assert resto2 == ""


def test_stream_acumulado_em_pedacos_produz_as_frases_certas():
    """Simula os fragmentos como o modelo os entrega."""
    fragmentos = [
        "Boa noite, senhor.",
        " Já verifiquei",
        " o sistema inteiro.",
        " Está tudo",
        " em ordem por aqui.",
    ]
    buffer = ""
    emitidas = []
    for fragmento in fragmentos:
        prontas, buffer = split_stream(buffer + fragmento)
        emitidas.extend(prontas)
    emitidas.extend(split_sentences(buffer))

    assert emitidas == [
        "Boa noite, senhor.",
        "Já verifiquei o sistema inteiro.",
        "Está tudo em ordem por aqui.",
    ]


def test_stream_sem_terminador_segura_tudo():
    prontas, resto = split_stream("ainda escrevendo esta frase")
    assert prontas == []
    assert resto == "ainda escrevendo esta frase"


def test_stream_segura_fragmento_curto_esperando_companhia():
    """'Sim.' sozinho espera o resto em vez de virar um bloco de síntese."""
    prontas, resto = split_stream("Sim. E depois")
    assert prontas == []
    assert resto == "Sim. E depois"


def test_stream_emite_fragmento_curto_quando_e_tudo_que_ha():
    prontas, resto = split_stream("Sim.")
    assert prontas == ["Sim."]
    assert resto == ""


def test_stream_com_buffer_vazio():
    assert split_stream("") == ([], "")

