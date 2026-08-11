"""Hora falada em português — dígitos crus soam mal no TTS."""

from datetime import datetime

import pytest

from james.tools.system import format_time_pt, que_horas_sao


def at(hour, minute):
    return datetime(2026, 8, 11, hour, minute)


@pytest.mark.parametrize(
    "hora,minuto,esperado",
    [
        (0, 0, "É meia-noite em ponto"),
        (12, 0, "É meio-dia em ponto"),
        (1, 0, "É uma hora em ponto"),
        (13, 0, "É uma hora em ponto"),
        (14, 30, "São duas horas e meia"),
        (9, 15, "São nove horas e 15"),
        (23, 45, "São onze horas e 45"),
    ],
)
def test_hora_por_extenso(hora, minuto, esperado):
    assert format_time_pt(at(hora, minuto)) == esperado


def test_nunca_devolve_hora_em_digitos_com_dois_pontos():
    """'14:35' sai do TTS como 'quatorze dois pontos trinta e cinco'."""
    for hora in range(24):
        for minuto in (0, 7, 30, 59):
            assert ":" not in format_time_pt(at(hora, minuto))


def test_tool_devolve_fala_e_dado_estruturado():
    resultado = que_horas_sao({})
    assert resultado.ok is True
    assert resultado.speech
    assert "hora" in resultado.data and "data" in resultado.data


def test_tool_inclui_periodo_do_dia():
    resultado = que_horas_sao({})
    assert any(
        periodo in resultado.speech
        for periodo in ("da manhã", "da tarde", "da noite", "da madrugada")
    )
