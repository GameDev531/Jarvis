"""Cotações e histórico pela API pública de gráficos do Yahoo Finance.

Escolhida por ser gratuita, não exigir cadastro nem chave, e cobrir tanto a B3
(`PETR4.SA`) quanto bolsas estrangeiras (`AAPL`) com o mesmo formato.

Ressalva honesta: é um endpoint **não documentado**. Ele é estável há anos e é
o que praticamente todas as bibliotecas de mercado usam por baixo, mas pode
mudar sem aviso. Quando isso acontecer, a falha é explícita — o James diz que
não conseguiu os dados, em vez de responder com número inventado.
"""

from __future__ import annotations

import re
from typing import Any

from james.logs import get_logger

logger = get_logger("james.finance.yahoo")

_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}"
_TIMEOUT_S = 12
# Sem User-Agent de navegador o Yahoo devolve 429 para requisição anônima.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    )
}
# Um ticker é curto: alfanumérico, com sufixo de bolsa (PETR4.SA), hífen
# (BRK-B, BTC-USD) ou '=' de futuros (CL=F). O acento circunflexo inicial
# marca índice (^BVSP, ^GSPC) e só é aceito na primeira posição.
# Validar aqui evita montar URL com texto arbitrário vindo do modelo.
_TICKER = re.compile(r"^\^?[A-Za-z0-9][A-Za-z0-9.\-=]{0,14}$")


class MarketDataError(RuntimeError):
    """Não foi possível obter os dados de mercado."""


def normalize_ticker(raw: str) -> str:
    """Valida e normaliza o código do ativo."""
    texto = str(raw or "").strip().upper().replace(" ", "")
    if not texto:
        raise MarketDataError("Preciso do código do ativo.")
    if not _TICKER.match(texto):
        raise MarketDataError(f"'{raw}' não parece um código de ativo válido.")
    return texto


def fetch_history(simbolo: str, intervalo: str = "1y") -> dict[str, Any]:
    """Histórico diário. Devolve símbolo, nome, moeda, fechamentos e volumes."""
    try:
        import httpx
    except ImportError as exc:
        raise MarketDataError("Pacote 'httpx' não instalado.") from exc

    ticker = normalize_ticker(simbolo)
    try:
        with httpx.Client(timeout=_TIMEOUT_S, headers=_HEADERS) as client:
            resposta = client.get(
                _ENDPOINT.format(simbolo=ticker),
                params={"range": intervalo, "interval": "1d"},
            )
    except Exception as exc:  # noqa: BLE001 — httpx tem vários tipos de erro
        raise MarketDataError(f"Não consegui falar com o provedor de cotações: {exc}") from exc

    if resposta.status_code == 404:
        raise MarketDataError(f"Não encontrei o ativo '{ticker}'.")
    if resposta.status_code >= 400:
        raise MarketDataError(
            f"O provedor de cotações devolveu {resposta.status_code}."
        )

    try:
        dados = resposta.json()
    except ValueError as exc:
        raise MarketDataError("Resposta ilegível do provedor de cotações.") from exc

    return parse_chart(dados, ticker)


def parse_chart(dados: dict[str, Any], ticker: str) -> dict[str, Any]:
    """Extrai o que interessa da resposta. Separado para poder ser testado."""
    grafico = (dados or {}).get("chart") or {}
    erro = grafico.get("error")
    if erro:
        descricao = erro.get("description") if isinstance(erro, dict) else str(erro)
        raise MarketDataError(f"Provedor recusou '{ticker}': {descricao}")

    resultados = grafico.get("result") or []
    if not resultados:
        raise MarketDataError(f"Sem dados para '{ticker}'.")

    resultado = resultados[0]
    meta = resultado.get("meta") or {}
    indicadores = (resultado.get("indicators") or {}).get("quote") or [{}]
    cotacoes = indicadores[0] if indicadores else {}

    fechamentos = [c for c in (cotacoes.get("close") or []) if c is not None]
    if len(fechamentos) < 2:
        raise MarketDataError(f"Histórico insuficiente para '{ticker}'.")

    volumes = [v for v in (cotacoes.get("volume") or []) if v is not None]

    return {
        "simbolo": str(meta.get("symbol") or ticker),
        # O nome vem do provedor: é texto externo e o chamador precisa saneá-lo.
        "nome": str(meta.get("longName") or meta.get("shortName") or "").strip(),
        "moeda": str(meta.get("currency") or "").strip(),
        "preco_atual": float(meta.get("regularMarketPrice") or fechamentos[-1]),
        "fechamentos": [float(c) for c in fechamentos],
        "volumes": [float(v) for v in volumes],
    }
