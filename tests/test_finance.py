"""Métricas de mercado: matemática pura, sem rede e sem opinião."""

import math

import pytest

from james.finance.metrics import compute_metrics, _queda_maxima, _tendencia, _volatilidade
from james.finance.yahoo import MarketDataError, normalize_ticker, parse_chart


def serie_constante(valor=100.0, n=300):
    return [valor] * n


def serie_crescente(inicio=100.0, passo=1.0, n=300):
    return [inicio + passo * i for i in range(n)]


# ---------------------------------------------------------------- retorno

def test_retornos_por_periodo():
    closes = serie_crescente(100, 1, 300)
    m = compute_metrics("TESTE", closes)
    # 21 pregões atrás o preço era 21 pontos menor.
    esperado = (closes[-1] / closes[-22] - 1) * 100
    assert m.retornos_pct["1_mes"] == pytest.approx(esperado)


def test_periodo_mais_longo_que_o_historico_e_omitido():
    m = compute_metrics("TESTE", serie_crescente(n=30))
    assert "1_semana" in m.retornos_pct
    assert "1_ano" not in m.retornos_pct


def test_historico_curto_demais_e_erro():
    with pytest.raises(ValueError):
        compute_metrics("TESTE", [100.0])


def test_valores_nulos_e_negativos_sao_descartados():
    m = compute_metrics("TESTE", [100.0, None, -5.0, 110.0, 0.0])
    assert m.pregoes == 2
    assert m.preco == 110.0


# ------------------------------------------------------------ volatilidade

def test_serie_constante_nao_tem_volatilidade():
    assert _volatilidade(serie_constante()) == pytest.approx(0.0)


def test_volatilidade_e_anualizada():
    """Oscilação diária conhecida deve anualizar pelo fator de 252 pregões."""
    closes = [100.0]
    for i in range(1, 200):
        closes.append(closes[-1] * (1.01 if i % 2 else 1 / 1.01))
    vol = _volatilidade(closes)
    diaria = math.log(1.01)
    assert vol == pytest.approx(diaria * math.sqrt(252) * 100, rel=0.05)


def test_serie_mais_volatil_tem_numero_maior():
    calma = [100 * (1.001 if i % 2 else 1 / 1.001) for i in range(200)]
    agitada = [100 * (1.05 if i % 2 else 1 / 1.05) for i in range(200)]
    assert _volatilidade(agitada) > _volatilidade(calma)


# ----------------------------------------------------------- queda máxima

def test_serie_sempre_subindo_nao_tem_queda():
    assert _queda_maxima(serie_crescente()) == pytest.approx(0.0)


def test_queda_maxima_e_do_topo_ao_fundo_seguinte():
    # Sobe até 200, cai até 100 (queda de 50%), sobe de novo.
    closes = [100, 150, 200, 150, 100, 120]
    assert _queda_maxima(closes) == pytest.approx(50.0)


def test_queda_maxima_ignora_recuperacao_posterior():
    """O que importa é quanto se viu sumir, não onde terminou."""
    closes = [100, 50, 300]
    assert _queda_maxima(closes) == pytest.approx(50.0)


# ------------------------------------------------------------- tendência

def test_tendencia_de_alta():
    assert "alta" in _tendencia(preco=120, media_50=110, media_200=100)


def test_tendencia_de_baixa():
    assert "baixa" in _tendencia(preco=90, media_50=100, media_200=110)


def test_tendencia_lateral():
    assert "lateral" in _tendencia(preco=105, media_50=100, media_200=110)


def test_sem_media_200_a_descricao_e_parcial():
    assert "50 dias" in _tendencia(preco=120, media_50=110, media_200=None)


def test_sem_media_50_a_tendencia_e_indefinida():
    assert _tendencia(preco=120, media_50=None, media_200=None) == "indefinida"


def test_tendencia_nunca_sugere_acao():
    """O código descreve onde o preço está; opinar é trabalho do modelo."""
    for descricao in (
        _tendencia(120, 110, 100),
        _tendencia(90, 100, 110),
        _tendencia(105, 100, 110),
    ):
        texto = descricao.lower()
        for palavra in ("compre", "venda", "barato", "caro", "oportunidade"):
            assert palavra not in texto


# ------------------------------------------------------------ faixa e saída

def test_posicao_na_faixa():
    m = compute_metrics("TESTE", [100.0, 200.0, 150.0])
    assert m.minima_periodo == 100 and m.maxima_periodo == 200
    assert m.posicao_na_faixa_pct == pytest.approx(50.0)


def test_faixa_sem_variacao_nao_divide_por_zero():
    m = compute_metrics("TESTE", serie_constante())
    assert m.posicao_na_faixa_pct is None


def test_saida_serializavel():
    dados = compute_metrics("PETR4.SA", serie_crescente(), nome="Petro", moeda="BRL").to_dict()
    assert dados["simbolo"] == "PETR4.SA"
    assert dados["moeda"] == "BRL"
    assert isinstance(dados["retornos_pct"], dict)
    assert "tendencia" in dados


def test_volume_medio_e_recente():
    volumes = [100.0] * 50 + [200.0] * 5
    m = compute_metrics("TESTE", serie_crescente(n=55), volumes=volumes)
    assert m.volume_medio == pytest.approx(sum(volumes) / len(volumes))
    assert m.volume_recente_pct > 0


def test_volume_curto_e_ignorado():
    m = compute_metrics("TESTE", serie_crescente(n=20), volumes=[1.0, 2.0])
    assert m.volume_medio is None


# ---------------------------------------------------------------- ticker

@pytest.mark.parametrize("bruto,esperado", [
    ("petr4.sa", "PETR4.SA"),
    ("  aapl  ", "AAPL"),
    ("BRK-B", "BRK-B"),
    ("BTC-USD", "BTC-USD"),
    ("^BVSP", "^BVSP"),
])
def test_tickers_validos(bruto, esperado):
    assert normalize_ticker(bruto) == esperado


@pytest.mark.parametrize("bruto", ["", "   ", "a" * 40, "../etc/passwd", "PETR4;rm -rf", "<script>"])
def test_tickers_invalidos_sao_recusados(bruto):
    """O código do ativo vira parte de uma URL — não pode ser texto livre."""
    with pytest.raises(MarketDataError):
        normalize_ticker(bruto)


# ------------------------------------------------------- resposta do provedor

def resposta(closes, volumes=None, meta=None):
    return {
        "chart": {
            "result": [
                {
                    "meta": meta or {"symbol": "X", "currency": "BRL", "regularMarketPrice": closes[-1]},
                    "indicators": {"quote": [{"close": closes, "volume": volumes or []}]},
                }
            ]
        }
    }


def test_resposta_valida():
    dados = parse_chart(resposta([10.0, 11.0, 12.0]), "X")
    assert dados["fechamentos"] == [10.0, 11.0, 12.0]
    assert dados["moeda"] == "BRL"


def test_lacunas_no_historico_sao_removidas():
    dados = parse_chart(resposta([10.0, None, 12.0]), "X")
    assert dados["fechamentos"] == [10.0, 12.0]


def test_erro_do_provedor_vira_excecao():
    payload = {"chart": {"error": {"description": "Not Found"}, "result": None}}
    with pytest.raises(MarketDataError, match="Not Found"):
        parse_chart(payload, "X")


def test_resposta_sem_resultado():
    with pytest.raises(MarketDataError):
        parse_chart({"chart": {"result": []}}, "X")


def test_historico_insuficiente():
    with pytest.raises(MarketDataError, match="insuficiente"):
        parse_chart(resposta([10.0]), "X")


def test_resposta_vazia_nao_explode():
    with pytest.raises(MarketDataError):
        parse_chart({}, "X")
