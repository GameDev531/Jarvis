"""Controle de taxa: distinguir "espere" de "acabou por hoje"."""

from datetime import date

import pytest

from james.llm.rate_limiter import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


def make(clock, rpm=3, rpd=10, path=None, today=None):
    return RateLimiter(
        requests_per_minute=rpm,
        requests_per_day=rpd,
        state_path=path,
        clock=clock,
        today=today or date.today,
    )


def test_requisicoes_dentro_do_limite_passam(clock):
    limiter = make(clock, rpm=3)
    assert all(limiter.acquire().allowed for _ in range(3))


def test_estouro_por_minuto_pede_espera(clock):
    limiter = make(clock, rpm=2)
    limiter.acquire()
    limiter.acquire()

    verdict = limiter.acquire()
    assert verdict.allowed is False
    assert verdict.retry_after_s > 0
    assert verdict.daily_exhausted is False


def test_janela_por_minuto_desliza(clock):
    limiter = make(clock, rpm=2)
    limiter.acquire()
    limiter.acquire()
    assert limiter.acquire().allowed is False

    clock.advance(61)
    assert limiter.acquire().allowed is True


def test_cota_diaria_esgotada_nao_pede_espera(clock):
    """Esperar não resolve cota diária — o sinal precisa ser diferente."""
    limiter = make(clock, rpm=100, rpd=2)
    limiter.acquire()
    limiter.acquire()

    verdict = limiter.acquire()
    assert verdict.allowed is False
    assert verdict.daily_exhausted is True
    assert verdict.retry_after_s == 0


def test_refund_devolve_requisicao_que_nao_saiu(clock):
    limiter = make(clock, rpm=1, rpd=5)
    limiter.acquire()
    assert limiter.acquire().allowed is False

    limiter.refund()
    assert limiter.acquire().allowed is True


def test_contador_diario_persiste_entre_reinicios(clock, tmp_path):
    caminho = tmp_path / "uso.json"
    primeiro = make(clock, rpm=100, rpd=5, path=caminho)
    primeiro.acquire()
    primeiro.acquire()

    segundo = make(FakeClock(), rpm=100, rpd=5, path=caminho)
    assert segundo.daily_remaining == 3


def test_virada_de_dia_reinicia_a_cota(clock, tmp_path):
    dias = [date(2026, 8, 11)]
    limiter = make(clock, rpm=100, rpd=2, path=tmp_path / "uso.json", today=lambda: dias[0])
    limiter.acquire()
    limiter.acquire()
    assert limiter.acquire().allowed is False

    dias[0] = date(2026, 8, 12)
    assert limiter.acquire().allowed is True


def test_contador_de_outro_dia_e_ignorado_ao_carregar(clock, tmp_path):
    caminho = tmp_path / "uso.json"
    caminho.write_text('{"date": "2020-01-01", "count": 999}', encoding="utf-8")
    limiter = make(clock, rpm=100, rpd=5, path=caminho)
    assert limiter.daily_remaining == 5


def test_arquivo_corrompido_nao_derruba_o_james(clock, tmp_path):
    caminho = tmp_path / "uso.json"
    caminho.write_text("{lixo não é json", encoding="utf-8")
    limiter = make(clock, rpm=100, rpd=5, path=caminho)
    assert limiter.acquire().allowed is True


def test_check_nao_consome(clock):
    limiter = make(clock, rpm=1)
    assert limiter.check().allowed is True
    assert limiter.check().allowed is True
    assert limiter.acquire().allowed is True
    assert limiter.check().allowed is False


def test_limites_invalidos_viram_minimo_seguro(clock):
    limiter = make(clock, rpm=0, rpd=0)
    assert limiter.acquire().allowed is True
