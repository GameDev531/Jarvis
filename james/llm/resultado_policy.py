"""Quanto de um resultado de ferramenta entra no histórico.

## O número que motivou isto

Numa conversa de quatro leituras de página, medida no código de verdade:

    system prompt .........  9.028 ch   29%
    histórico (a conversa)   3.324 ch   11%
    RESULTADOS DE TOOL ....  19.191 ch   61%
    -----------------------------------------
                            31.551 ch

A conversa em si é 11%. O resto é o modelo relendo, a cada turno, o texto
integral de páginas que ele já resumiu no turno em que as leu.

O orçamento (`orcamento.py`) é a rede de segurança: ele age quando o total
estoura. Isto aqui é a economia do dia a dia — o resultado já entra do
tamanho certo, e o teto quase nunca precisa ser acionado.

## A diferença entre este módulo e o orçamento

  - **Aqui**: por ferramenta, na ENTRADA, sabendo o que aquele resultado
    significa. `listar_abas` precisa da lista inteira; `ler_pagina` precisa dos
    primeiros parágrafos e não dos cem seguintes.
  - **Lá**: genérico, na SAÍDA, quando o total não coube. Não sabe o que é o
    quê, então corta parelho.

## O padrão falha fechado

Uma ferramenta sem política declarada recebe `PADRAO`. É a mesma escolha da
política de auditoria e pelo mesmo motivo: a ferramenta que alguém escrever
amanhã não pode vazar contexto por esquecimento. Quem precisar de mais espaço
declara — e a declaração é uma linha, que se lê como uma decisão.

## O que NUNCA é cortado

Campos que o CÓDIGO lê depois. `tab_id`, `snapshot_id`, `element_id`, `id` de
fato: se um deles for truncado, a ferramenta seguinte recebe um identificador
que parece válido e aponta para nada. Truncar dado é perder detalhe; truncar
identificador é criar um bug com cara de dado.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from james.llm.orcamento import tamanho
from james.logs import get_logger

logger = get_logger("james.llm.resultado")

# Chaves que são IDENTIFICADOR, não conteúdo. Sobrevivem inteiras a qualquer
# política — um id truncado é pior que um id ausente, porque parece válido.
IDENTIFICADORES = frozenset({
    "tab_id", "snapshot_id", "element_id", "call_id", "id", "fato_id",
    "pack", "status", "ok", "recusado", "codigo", "criada", "ja_existia",
})


@dataclass(frozen=True)
class Politica:
    """Quanto cabe, e o que sobrevive ao aperto."""

    max_chars: int
    # Campos preservados por inteiro, gastos antes de qualquer outro. É onde
    # mora a diferença entre "resumo útil" e "primeiros N bytes".
    essenciais: tuple[str, ...] = ()
    # Campos jogados fora primeiro — grandes e raramente relidos.
    descartaveis: tuple[str, ...] = ()


# 1.200 caracteres é ~300 tokens: cabe um resumo com substância e não cabe uma
# página inteira. Foi escolhido contra o custo real: doze turnos de histórico
# com este teto somam ~14 KB, que é um terço do orçamento total.
PADRAO = Politica(max_chars=1_200)

POLITICAS: dict[str, Politica] = {
    # --- leitura da web: o texto é longo por natureza e já foi resumido ---
    "ler_pagina": Politica(
        max_chars=1_500,
        essenciais=("titulo", "url", "dominio"),
        descartaveis=("html", "texto_bruto"),
    ),
    "buscar_na_web": Politica(
        max_chars=1_800,
        # Título e link são o resultado; o trecho é o que dá para encurtar.
        essenciais=("resultados",),
    ),
    "pesquisa_aprofundada": Politica(max_chars=2_500, essenciais=("resumo", "fontes")),

    # --- navegador: a lista de elementos É o resultado, e o modelo age nela ---
    "inspecionar_pagina": Politica(
        max_chars=3_000,
        essenciais=("snapshot_id", "tab_id", "elementos", "resumo"),
        # O relatório de QA é útil para falar, não para agir; se algo tem de
        # encolher, encolhe ele antes da lista de elementos.
        descartaveis=("achados", "formularios", "contagem"),
    ),
    "listar_abas": Politica(max_chars=1_500, essenciais=("abas",)),

    # --- memória: a resposta é curta por construção ---
    "consultar_fatos": Politica(max_chars=1_500, essenciais=("fatos",)),
    "consultar_memoria": Politica(max_chars=1_500),
    "como_se_conectam": Politica(max_chars=1_200, essenciais=("caminho",)),

    # --- arquivos: uma pasta pode ter milhares de entradas ---
    "listar_arquivos": Politica(max_chars=1_500, essenciais=("total", "pasta")),
    "organizar_arquivos": Politica(max_chars=800, essenciais=("movidos", "falhas")),

    # --- finanças e escritório ---
    "analisar_acao": Politica(max_chars=1_500, essenciais=("simbolo", "preco")),
    "comparar_acoes": Politica(max_chars=2_000, essenciais=("simbolos",)),
    "criar_apresentacao": Politica(max_chars=400, essenciais=("arquivo",)),
    "criar_planilha": Politica(max_chars=400, essenciais=("arquivo",)),

    # --- sequência: o resumo de vários passos, cada um já podado ---
    "executar_sequencia": Politica(max_chars=2_500, essenciais=("passos", "resultado")),

    # --- visão: a descrição é o resultado inteiro ---
    "ver_tela": Politica(max_chars=1_500),
    "ver_camera": Politica(max_chars=1_500),
}


def politica_de(nome: str) -> Politica:
    return POLITICAS.get(nome, PADRAO)


@dataclass
class Poda:
    """O que foi podado deste resultado."""

    antes: int = 0
    depois: int = 0
    campos_removidos: list[str] = field(default_factory=list)

    @property
    def houve(self) -> bool:
        return self.depois < self.antes


def aplicar(nome: str, resultado, politica: Politica | None = None):
    """Devolve o resultado do tamanho que cabe no histórico.

    Não modifica o original: o chamador ainda usa `result.data` para decidir
    coisas (o `tab_id` que voltou, o pack que foi carregado), e podar aquilo
    seria podar a lógica junto.
    """
    pol = politica or politica_de(nome)
    if tamanho(resultado) <= pol.max_chars:
        return resultado
    if not isinstance(resultado, dict):
        return _cortar(resultado, pol.max_chars)

    poda = Poda(antes=tamanho(resultado))
    saida: dict = {}
    restante = pol.max_chars

    # 1. Identificadores primeiro, inteiros, sempre. Um id truncado parece
    #    válido e aponta para nada.
    for chave, valor in resultado.items():
        if chave in IDENTIFICADORES:
            saida[chave] = valor
            restante -= tamanho(valor)

    # 2. Descartáveis não entram enquanto houver aperto.
    ignorar = set(pol.descartaveis)

    # 3. Essenciais, na ordem declarada — é essa ordem que decide o que
    #    sobrevive quando o espaço acaba no meio.
    for chave in pol.essenciais:
        if chave in saida or chave not in resultado:
            continue
        valor = _cortar(resultado[chave], max(200, restante))
        saida[chave] = valor
        restante -= tamanho(valor)

    # 4. O resto, com o que sobrou.
    for chave, valor in resultado.items():
        if chave in saida or chave in ignorar:
            continue
        if restante <= 0:
            poda.campos_removidos.append(chave)
            continue
        podado = _cortar(valor, max(120, restante))
        saida[chave] = podado
        restante -= tamanho(podado)

    descartados = [c for c in ignorar if c in resultado]
    poda.campos_removidos.extend(descartados)
    if poda.campos_removidos:
        # A marca precisa existir: sem ela o modelo lê um resultado parcial
        # como se fosse o resultado inteiro, e responde com confiança sobre o
        # que não viu.
        saida["_podado"] = f"campos omitidos: {', '.join(sorted(poda.campos_removidos))}"

    poda.depois = tamanho(saida)
    if poda.houve:
        logger.debug(
            "Resultado de '%s' podado: %d -> %d ch.", nome, poda.antes, poda.depois
        )
    return saida


def _cortar(valor, limite: int):
    """Encurta preservando a forma. JSON quebrado faz o modelo alucinar."""
    if limite <= 0:
        return "…"
    if isinstance(valor, str):
        if len(valor) <= limite:
            return valor
        return valor[:limite].rstrip() + f"… [+{len(valor) - limite} caracteres]"

    if isinstance(valor, (list, tuple)):
        saida = []
        restante = limite
        for item in valor:
            if restante <= 0:
                saida.append(f"… +{len(valor) - len(saida)} itens")
                break
            reduzido = _cortar(item, max(80, restante // 2))
            saida.append(reduzido)
            restante -= tamanho(reduzido)
        return saida

    if isinstance(valor, dict):
        saida = {}
        restante = limite
        for chave, item in valor.items():
            if chave in IDENTIFICADORES:
                saida[chave] = item
                restante -= tamanho(item)
                continue
            if restante <= 0:
                saida["…"] = f"+{len(valor) - len(saida)} campos"
                break
            reduzido = _cortar(item, max(80, restante // 2))
            saida[chave] = reduzido
            restante -= tamanho(reduzido)
        return saida

    return valor
