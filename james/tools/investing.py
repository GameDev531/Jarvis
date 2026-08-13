"""Análise de ativos — dados de mercado para o modelo raciocinar em cima.

A divisão desta ferramenta é a coisa mais importante dela:

    o código entrega FATOS calculáveis  →  o modelo faz a LEITURA

O código nunca diz "está barato", "vale a pena" ou "compre". Ele mede: retorno
por período, volatilidade anualizada, queda máxima, distância do topo, posição
frente às médias móveis. A leitura desses números — e principalmente do que
eles *não* dizem — é feita pelo modelo, com a disciplina descrita na seção de
investimento do prompt do sistema.

Por que a decisão fica com o usuário, sempre: um assistente não conhece o
patrimônio, o prazo, a tolerância a perda nem os outros compromissos de quem
pergunta. Sem isso, "compre" é chute com voz confiante — e o prejuízo é de
quem ouviu, não de quem falou.
"""

from __future__ import annotations

from james.finance.metrics import compute_metrics
from james.finance.yahoo import MarketDataError, fetch_history
from james.logs import audit, get_logger
from james.security.sanitizer import strip_dangerous_chars
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.investing")

MAX_COMPARACAO = 5
_INTERVALOS = {"1_mes": "1mo", "6_meses": "6mo", "1_ano": "1y", "5_anos": "5y"}


def _analisar(simbolo: str, intervalo: str) -> dict:
    dados = fetch_history(simbolo, intervalo)
    metrics = compute_metrics(
        simbolo=dados["simbolo"],
        closes=dados["fechamentos"],
        volumes=dados["volumes"],
        # O nome vem do provedor: é texto externo entrando no contexto.
        nome=strip_dangerous_chars(dados["nome"])[:80],
        moeda=dados["moeda"],
    )
    return metrics.to_dict()


def register(registry: ToolRegistry, config, guard) -> None:
    def analisar_acao(args: dict) -> ToolResult:
        codigo = str(args.get("codigo", "")).strip()
        periodo = str(args.get("periodo", "1_ano")).strip().lower()
        intervalo = _INTERVALOS.get(periodo, "1y")

        try:
            dados = _analisar(codigo, intervalo)
        except MarketDataError as exc:
            logger.info("Análise de %r falhou: %s", codigo, exc)
            return ToolResult(ok=False, speech=str(exc), data={"erro": str(exc)})
        except ValueError as exc:
            return ToolResult(ok=False, speech=str(exc), data={"erro": str(exc)})

        audit("analisar_acao", codigo=dados["simbolo"], periodo=periodo)
        return ToolResult(
            ok=True,
            data={
                "periodo": periodo,
                "ativo": dados,
                # Lembrete no próprio resultado: quando o histórico fica longo,
                # a instrução do prompt do sistema perde peso relativo.
                "lembrete": (
                    "Estes são dados históricos. Eles não preveem preço futuro. "
                    "Apresente cenários e riscos, não recomendação de compra ou venda."
                ),
            },
        )

    def comparar_acoes(args: dict) -> ToolResult:
        codigos_raw = args.get("codigos") or []
        if isinstance(codigos_raw, str):
            codigos_raw = [c for c in codigos_raw.replace(";", ",").split(",") if c.strip()]
        codigos = [str(c).strip() for c in codigos_raw if str(c).strip()][:MAX_COMPARACAO]
        if len(codigos) < 2:
            return ToolResult.failure("Preciso de pelo menos dois ativos para comparar.")

        periodo = str(args.get("periodo", "1_ano")).strip().lower()
        intervalo = _INTERVALOS.get(periodo, "1y")

        analisados: list[dict] = []
        falhas: list[str] = []
        for codigo in codigos:
            try:
                analisados.append(_analisar(codigo, intervalo))
            except (MarketDataError, ValueError) as exc:
                falhas.append(f"{codigo}: {exc}")

        if not analisados:
            return ToolResult.failure("Não consegui dados de nenhum dos ativos.")

        audit("comparar_acoes", codigos=[a["simbolo"] for a in analisados], periodo=periodo)
        return ToolResult(
            ok=True,
            data={
                "periodo": periodo,
                "ativos": analisados,
                "falhas": falhas,
                "lembrete": (
                    "Comparar retorno passado não indica qual será melhor adiante. "
                    "Considere também risco, setor e horizonte."
                ),
            },
        )

    registry.register(
        Tool(
            name="analisar_acao",
            description=(
                "Traz dados históricos de um ativo (ação, índice, fundo ou cripto): "
                "retorno por período, volatilidade, queda máxima, distância do topo "
                "e posição frente às médias móveis. Use o código do ativo — na B3 "
                "com o sufixo .SA, como PETR4.SA ou VALE3.SA; fora do Brasil o "
                "código direto, como AAPL. Estes são dados, não recomendação."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "codigo": {
                        "type": "string",
                        "description": "Código do ativo, ex: PETR4.SA, AAPL, BTC-USD.",
                    },
                    "periodo": {
                        "type": "string",
                        "enum": ["1_mes", "6_meses", "1_ano", "5_anos"],
                        "description": "Janela do histórico. Padrão: 1_ano.",
                    },
                },
                "required": ["codigo"],
            },
            handler=analisar_acao,
            fire_and_forget=False,
        )
    )
    registry.register(
        Tool(
            name="comparar_acoes",
            description=(
                "Compara dois ou mais ativos lado a lado, com as mesmas métricas de "
                "analisar_acao. Útil quando o usuário quer entender diferenças de "
                "risco e comportamento entre alternativas."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "codigos": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": f"De 2 a {MAX_COMPARACAO} códigos de ativo.",
                    },
                    "periodo": {
                        "type": "string",
                        "enum": ["1_mes", "6_meses", "1_ano", "5_anos"],
                        "description": "Janela do histórico. Padrão: 1_ano.",
                    },
                },
                "required": ["codigos"],
            },
            handler=comparar_acoes,
            fire_and_forget=False,
        )
    )
