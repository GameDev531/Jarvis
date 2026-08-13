"""Briefing do dia: montagem determinística, clima é acessório."""

import pytest

from james.config import Config
from james.memory import MemoryScope, MemoryStore
from james.tools import briefing as briefing_module
from james.tools.briefing import register
from james.tools.registry import ToolRegistry


@pytest.fixture
def rig(tmp_path, config_data):
    config_data["briefing"] = {"cidade": ""}
    config = Config(config_data)
    memoria = MemoryStore(tmp_path / "mem", max_chars=2000)
    registry = ToolRegistry()
    register(registry, config, None, memoria)
    return registry, memoria


CLIMA = {
    "descricao": "parcialmente nublado",
    "temperatura": 24,
    "sensacao": 26,
    "minima": 18,
    "maxima": 29,
    "chuva_pct": 10,
}


def test_briefing_sem_clima_ainda_funciona(rig):
    """Sem cidade configurada, o briefing sai sem a parte de clima."""
    registry, _ = rig
    resultado = registry.execute("briefing_do_dia", {})
    assert resultado.ok is True
    assert resultado.speech
    assert "hora" in resultado.data and "data" in resultado.data


def test_briefing_comeca_com_saudacao(rig):
    registry, _ = rig
    fala = registry.execute("briefing_do_dia", {}).speech
    assert any(fala.startswith(s) for s in ("Bom dia", "Boa tarde", "Boa noite", "Boa madrugada"))


def test_clima_entra_na_fala(rig, monkeypatch):
    registry, _ = rig
    monkeypatch.setattr(briefing_module, "fetch_weather", lambda cidade: CLIMA)

    resultado = registry.execute("briefing_do_dia", {"cidade": "Recife"})
    assert "Recife" in resultado.speech
    assert "parcialmente nublado" in resultado.speech
    assert "24 graus" in resultado.speech
    assert resultado.data["clima"] == CLIMA


def test_chance_alta_de_chuva_e_mencionada(rig, monkeypatch):
    registry, _ = rig
    monkeypatch.setattr(
        briefing_module, "fetch_weather", lambda cidade: {**CLIMA, "chuva_pct": 80}
    )
    fala = registry.execute("briefing_do_dia", {"cidade": "Recife"}).speech
    assert "chuva" in fala


def test_chance_baixa_de_chuva_nao_polui_a_fala(rig, monkeypatch):
    registry, _ = rig
    monkeypatch.setattr(
        briefing_module, "fetch_weather", lambda cidade: {**CLIMA, "chuva_pct": 5}
    )
    fala = registry.execute("briefing_do_dia", {"cidade": "Recife"}).speech
    assert "chance de chuva" not in fala


def test_falha_do_clima_nao_derruba_o_briefing(rig, monkeypatch):
    """O clima é acessório: sem ele o resto do briefing continua."""
    registry, _ = rig
    monkeypatch.setattr(briefing_module, "fetch_weather", lambda cidade: None)

    resultado = registry.execute("briefing_do_dia", {"cidade": "Recife"})
    assert resultado.ok is True
    assert "não consegui consultar o clima" in resultado.speech.lower()


def test_lembretes_da_memoria_aparecem(rig):
    registry, memoria = rig
    memoria.add(MemoryScope.USER, "lembrete: revisar o contrato")

    resultado = registry.execute("briefing_do_dia", {})
    assert "revisar o contrato" in resultado.speech
    assert resultado.data["lembretes"]


def test_memoria_vazia_nao_gera_secao(rig):
    registry, _ = rig
    resultado = registry.execute("briefing_do_dia", {})
    assert "Do que eu guardei" not in resultado.speech


def test_lembretes_duplicados_aparecem_uma_vez(rig):
    """'lembrete' e 'hoje' são buscados separadamente e podem casar o mesmo item."""
    registry, memoria = rig
    memoria.add(MemoryScope.USER, "lembrete de hoje: pagar a conta")

    resultado = registry.execute("briefing_do_dia", {})
    assert resultado.speech.count("pagar a conta") == 1


def test_briefing_e_fire_and_forget(rig):
    """O texto sai pronto daqui: pedir ao modelo para redigi-lo custaria uma
    requisição por uso sem melhorar nada."""
    registry, _ = rig
    assert registry.get("briefing_do_dia").fire_and_forget is True


def test_sem_memoria_configurada_ainda_funciona(config_data):
    registry = ToolRegistry()
    register(registry, Config(config_data), None, None)
    assert registry.execute("briefing_do_dia", {}).ok is True
