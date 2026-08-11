"""Interpretação do atalho do kill switch."""

import pytest

from james.hotkey.killswitch import HotkeyError, parse_hotkey

MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8


def test_atalho_padrao():
    modificadores, tecla = parse_hotkey("ctrl+alt+j")
    assert modificadores == MOD_CTRL | MOD_ALT
    assert tecla == ord("J")


def test_maiusculas_e_espacos_sao_tolerados():
    assert parse_hotkey("  CTRL + ALT + J  ") == parse_hotkey("ctrl+alt+j")


def test_sinonimos_de_modificador():
    assert parse_hotkey("control+shift+k") == parse_hotkey("ctrl+shift+k")
    assert parse_hotkey("win+p") == parse_hotkey("super+p")


def test_teclas_nomeadas():
    assert parse_hotkey("ctrl+alt+f4")[1] == 0x73
    assert parse_hotkey("ctrl+space")[1] == 0x20
    assert parse_hotkey("ctrl+alt+escape")[1] == 0x1B


def test_digito_como_tecla_principal():
    assert parse_hotkey("ctrl+alt+5")[1] == ord("5")


def test_todos_os_modificadores_juntos():
    modificadores, _ = parse_hotkey("ctrl+alt+shift+win+j")
    assert modificadores == MOD_CTRL | MOD_ALT | MOD_SHIFT | MOD_WIN


@pytest.mark.parametrize("spec", ["", "   ", "+", "++"])
def test_atalho_vazio_e_rejeitado(spec):
    with pytest.raises(HotkeyError):
        parse_hotkey(spec)


def test_atalho_sem_modificador_e_rejeitado():
    """Sem modificador, o atalho engoliria a tecla no sistema inteiro."""
    with pytest.raises(HotkeyError):
        parse_hotkey("j")


def test_atalho_so_com_modificador_e_rejeitado():
    with pytest.raises(HotkeyError):
        parse_hotkey("ctrl+alt")


def test_duas_teclas_principais_sao_rejeitadas():
    with pytest.raises(HotkeyError):
        parse_hotkey("ctrl+j+k")


def test_tecla_desconhecida_e_rejeitada():
    with pytest.raises(HotkeyError):
        parse_hotkey("ctrl+alt+teclaqueNaoExiste")


def test_none_e_rejeitado():
    with pytest.raises(HotkeyError):
        parse_hotkey(None)
