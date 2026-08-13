"""PIN de confirmação: hash com sal, comparação em tempo constante."""

import json

import pytest

from james.security.pin import MIN_PIN_LENGTH, PinError, PinStore


@pytest.fixture
def store(tmp_path):
    return PinStore(tmp_path / "pin.json")


def test_sem_pin_configurado(store):
    assert store.configured is False
    assert store.verify("1234") is False


def test_definir_e_verificar(store):
    store.set_pin("4729")
    assert store.configured is True
    assert store.verify("4729") is True
    assert store.verify("4728") is False


def test_pin_nao_e_gravado_em_texto(store, tmp_path):
    """O arquivo não pode conter o PIN nem em pedaços."""
    store.set_pin("segredo123")
    bruto = (tmp_path / "pin.json").read_text(encoding="utf-8")
    assert "segredo123" not in bruto

    payload = json.loads(bruto)
    assert payload["algoritmo"] == "scrypt"
    assert len(payload["sal"]) >= 32   # 16 bytes em hexadecimal


def test_sal_e_diferente_a_cada_definicao(tmp_path):
    """Dois usuários com o mesmo PIN não podem gerar o mesmo hash."""
    primeiro = PinStore(tmp_path / "a.json")
    segundo = PinStore(tmp_path / "b.json")
    primeiro.set_pin("1234")
    segundo.set_pin("1234")

    hash_a = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["hash"]
    hash_b = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))["hash"]
    assert hash_a != hash_b


def test_pin_curto_demais_e_recusado(store):
    with pytest.raises(PinError):
        store.set_pin("1" * (MIN_PIN_LENGTH - 1))


def test_pin_longo_demais_e_recusado(store):
    with pytest.raises(PinError):
        store.set_pin("1" * 200)


def test_pin_vazio_e_recusado(store):
    for vazio in ("", "   "):
        with pytest.raises(PinError):
            store.set_pin(vazio)


def test_espacos_nas_pontas_sao_ignorados(store):
    store.set_pin("  4729  ")
    assert store.verify("4729") is True


def test_trocar_o_pin(store):
    store.set_pin("1111")
    store.set_pin("2222")
    assert store.verify("1111") is False
    assert store.verify("2222") is True


def test_remover_o_pin(store):
    store.set_pin("1234")
    store.clear()
    assert store.configured is False
    assert store.verify("1234") is False


def test_arquivo_corrompido_nega_em_vez_de_explodir(store, tmp_path):
    """Falhar aqui precisa significar 'não', nunca 'sim'."""
    store.set_pin("1234")
    (tmp_path / "pin.json").write_text("{lixo", encoding="utf-8")
    assert store.verify("1234") is False


def test_campo_faltando_no_arquivo_nega(store, tmp_path):
    (tmp_path / "pin.json").write_text('{"algoritmo": "scrypt"}', encoding="utf-8")
    assert store.verify("1234") is False


def test_verificar_com_valor_nao_texto_nao_explode(store):
    """Valores não textuais são convertidos, nunca causam exceção.

    `1234` vira `"1234"` e confere de verdade — o que é correto e não afrouxa
    nada: quem chama é o campo de texto do diálogo, que sempre entrega string.
    O que importa é que nada aqui levante exceção, porque uma exceção no meio
    da confirmação poderia deixar a ação num estado indefinido.
    """
    store.set_pin("1234")
    assert store.verify(None) is False
    assert store.verify([]) is False
    assert store.verify(1234) is True
    assert store.verify(9999) is False
