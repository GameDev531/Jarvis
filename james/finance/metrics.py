"""Métricas de um histórico de preços — matemática pura, sem rede.

Tudo aqui é fato calculável: retorno, volatilidade, distância do topo, queda
máxima, médias móveis. Nada aqui opina.

A separação é deliberada. O que este módulo produz são números verificáveis; a
leitura desses números é trabalho do modelo, com a disciplina descrita no
prompt do sistema. Misturar as duas coisas — código que já sugere "está barato"
— seria embutir opinião num lugar onde ninguém vai procurá-la depois.

Uma nota sobre volatilidade: ela mede oscilação, não risco. Risco é perda
permanente de capital. Um ativo pode oscilar muito e não ser arriscado no
horizonte certo, e pode oscilar pouco e quebrar. A volatilidade importa para
dimensionar posição e para saber se a pessoa consegue segurar a queda sem
vender no pior momento.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

# Pregões num ano — o fator padrão para anualizar volatilidade diária.
TRADING_DAYS = 252

_PERIODOS = {"1_semana": 5, "1_mes": 21, "3_meses": 63, "6_meses": 126, "1_ano": 252}


@dataclass
class Metrics:
    simbolo: str
    nome: str = ""
    moeda: str = ""
    preco: float = 0.0
    pregoes: int = 0
    retornos_pct: dict[str, float] = field(default_factory=dict)
    volatilidade_anual_pct: float | None = None
    queda_maxima_pct: float | None = None
    maxima_periodo: float | None = None
    minima_periodo: float | None = None
    posicao_na_faixa_pct: float | None = None
    media_50: float | None = None
    media_200: float | None = None
    tendencia: str = "indefinida"
    volume_medio: float | None = None
    volume_recente_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "simbolo": self.simbolo,
            "nome": self.nome,
            "moeda": self.moeda,
            "preco": _round(self.preco),
            "pregoes_analisados": self.pregoes,
            "retornos_pct": {k: _round(v) for k, v in self.retornos_pct.items()},
            "volatilidade_anual_pct": _round(self.volatilidade_anual_pct),
            "queda_maxima_pct": _round(self.queda_maxima_pct),
            "maxima_periodo": _round(self.maxima_periodo),
            "minima_periodo": _round(self.minima_periodo),
            "posicao_na_faixa_pct": _round(self.posicao_na_faixa_pct),
            "media_50": _round(self.media_50),
            "media_200": _round(self.media_200),
            "tendencia": self.tendencia,
            "volume_medio": _round(self.volume_medio, 0),
            "volume_recente_vs_medio_pct": _round(self.volume_recente_pct),
        }


def compute_metrics(
    simbolo: str,
    closes: list[float],
    volumes: list[float] | None = None,
    nome: str = "",
    moeda: str = "",
) -> Metrics:
    """Calcula as métricas a partir dos fechamentos diários, do mais antigo ao mais novo."""
    limpos = [float(c) for c in closes if c is not None and float(c) > 0]
    if len(limpos) < 2:
        raise ValueError("Histórico curto demais para calcular qualquer coisa.")

    preco = limpos[-1]
    metrics = Metrics(
        simbolo=simbolo, nome=nome, moeda=moeda, preco=preco, pregoes=len(limpos)
    )

    for rotulo, dias in _PERIODOS.items():
        if len(limpos) > dias:
            anterior = limpos[-(dias + 1)]
            if anterior > 0:
                metrics.retornos_pct[rotulo] = (preco / anterior - 1) * 100

    metrics.volatilidade_anual_pct = _volatilidade(limpos)
    metrics.queda_maxima_pct = _queda_maxima(limpos)

    metrics.maxima_periodo = max(limpos)
    metrics.minima_periodo = min(limpos)
    faixa = metrics.maxima_periodo - metrics.minima_periodo
    if faixa > 0:
        # 0% = no fundo do período, 100% = na máxima. Ajuda a situar o preço
        # atual sem sugerir que "perto do fundo" signifique barato.
        metrics.posicao_na_faixa_pct = (preco - metrics.minima_periodo) / faixa * 100

    metrics.media_50 = _media(limpos, 50)
    metrics.media_200 = _media(limpos, 200)
    metrics.tendencia = _tendencia(preco, metrics.media_50, metrics.media_200)

    if volumes:
        metrics.volume_medio, metrics.volume_recente_pct = _volume(volumes)

    return metrics


def _volatilidade(closes: list[float]) -> float | None:
    """Desvio padrão dos retornos logarítmicos diários, anualizado."""
    retornos = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    if len(retornos) < 2:
        return None
    return statistics.stdev(retornos) * math.sqrt(TRADING_DAYS) * 100


def _queda_maxima(closes: list[float]) -> float:
    """Maior queda do topo até o fundo seguinte, dentro do período.

    É o número que responde "quanto eu teria visto sumir se tivesse comprado no
    pior momento" — bem mais concreto que volatilidade para decidir se dá para
    segurar a posição.
    """
    pico = closes[0]
    pior = 0.0
    for valor in closes:
        pico = max(pico, valor)
        if pico > 0:
            pior = max(pior, (pico - valor) / pico)
    return pior * 100


def _media(closes: list[float], janela: int) -> float | None:
    if len(closes) < janela:
        return None
    return sum(closes[-janela:]) / janela


def _tendencia(preco: float, media_50: float | None, media_200: float | None) -> str:
    """Descrição factual da posição do preço frente às médias.

    É descrição, não sinal de compra: "acima das médias" diz onde o preço está,
    não para onde vai.
    """
    if media_50 is None:
        return "indefinida"
    if media_200 is None:
        return "acima da média de 50 dias" if preco > media_50 else "abaixo da média de 50 dias"

    if preco > media_50 > media_200:
        return "alta (preço acima das médias de 50 e 200 dias)"
    if preco < media_50 < media_200:
        return "baixa (preço abaixo das médias de 50 e 200 dias)"
    return "lateral (médias sem alinhamento claro)"


def _volume(volumes: list[float]) -> tuple[float | None, float | None]:
    limpos = [float(v) for v in volumes if v is not None and float(v) >= 0]
    if len(limpos) < 10:
        return None, None
    medio = sum(limpos) / len(limpos)
    recente = sum(limpos[-5:]) / min(5, len(limpos))
    if medio <= 0:
        return medio, None
    return medio, (recente / medio - 1) * 100


def _round(valor: float | None, casas: int = 2) -> float | None:
    if valor is None:
        return None
    arredondado = round(float(valor), casas)
    return int(arredondado) if casas == 0 else arredondado
