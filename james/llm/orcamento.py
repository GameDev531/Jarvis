"""Quanto contexto vai na requisição, e o que cai primeiro quando não cabe.

## O que a medida mostrou

Uma conversa de quatro leituras de página, medida no código de verdade:

    system prompt .............  9.280 ch   18%
    schemas do catálogo .......  14.414 ch   28%
    histórico + turno atual ...  28.064 ch   54%
    -------------------------------------------
                                51.758 ch  (~12.900 tokens)

O histórico é a maior fatia, e não é a conversa: é **resultado de ferramenta**.
Uma leitura de página são 4.000 caracteres (teto do sanitizador), e quatro
delas são 16.000 — mais que o system prompt e os schemas somados.

`max_turns=12` não protege disso, porque conta TURNOS e não tamanho. Doze
turnos podem ser 3 KB ou 100 KB, e o código não sabia a diferença.

## Por que caractere e não token

O tokenizador certo depende do modelo, e a cadeia do James troca de modelo
quando um cai. Um número exato para o modelo errado é pior que uma estimativa
honesta: passa confiança que não existe. Aqui se conta caractere, e a divisão
por 4 aparece só onde é rotulada como ordem de grandeza.

O que importa é a RAZÃO — antes e depois —, e essa é a mesma nas duas unidades.

## A ordem do sacrifício

Quando não cabe, a ordem é do menos para o mais custoso de perder:

  1. **resultado de ferramenta antigo** — já foi lido, resumido e respondido;
     o que sobrou dele é o detalhe que ninguém vai reler;
  2. **turno de conversa antigo** — o começo da conversa, que a memória curada
     já guarda se for importante;
  3. nada mais.

O turno atual e o system prompt **nunca** caem. Um turno atual cortado é o
James respondendo a meia pergunta; um system prompt cortado é o James virando
outra pessoa no meio da conversa.

E um par chamada/resposta nunca é quebrado: o histórico que começa com um
`function_response` órfão é rejeitado pela API, e aí não sobra contexto nenhum.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from james.logs import get_logger

logger = get_logger("james.llm.orcamento")

# Teto padrão em caracteres. Escolhido contra a janela mais apertada da cadeia
# (modelos :free costumam ter 32k tokens), com folga para a resposta: 48.000
# caracteres são ~12.000 tokens de entrada, deixando o resto para o modelo
# falar. Um teto maior que a janela do modelo mais fraco da cadeia derrubaria
# justamente a reserva — que é a que precisa funcionar.
TETO_PADRAO = 48_000

# Reserva para a resposta. Não é medida aqui; é o espaço que NÃO se enche.
RESERVA_SAIDA = 8_000


def tamanho(valor) -> int:
    """Caracteres que este valor ocupa quando vira texto para o modelo."""
    if valor is None:
        return 0
    if isinstance(valor, str):
        return len(valor)
    if isinstance(valor, bytes):
        # Áudio e imagem viajam em base64: cresce ~4/3.
        return (len(valor) * 4) // 3
    try:
        return len(json.dumps(valor, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(valor))


@dataclass
class Medida:
    """A composição do contexto, parte a parte."""

    sistema: int = 0
    orientacao: int = 0
    historico: int = 0
    resultados: int = 0
    turno_atual: int = 0
    audio: int = 0

    @property
    def total(self) -> int:
        return (
            self.sistema + self.orientacao + self.historico
            + self.resultados + self.turno_atual + self.audio
        )

    @property
    def tokens_aprox(self) -> int:
        """Ordem de grandeza, não medida. Ver o cabeçalho do módulo."""
        return self.total // 4

    def como_dict(self) -> dict:
        return {
            "sistema": self.sistema,
            "orientacao": self.orientacao,
            "historico": self.historico,
            "resultados": self.resultados,
            "turno_atual": self.turno_atual,
            "audio": self.audio,
            "total": self.total,
            "tokens_aprox": self.tokens_aprox,
        }

    def relatorio(self) -> str:
        """Uma tabela legível — é o que responde "por que está tão grande?"."""
        linhas = []
        for nome, valor in (
            ("system prompt", self.sistema),
            ("orientação", self.orientacao),
            ("histórico", self.historico),
            ("resultados de tool", self.resultados),
            ("turno atual", self.turno_atual),
            ("áudio", self.audio),
        ):
            if valor:
                fracao = valor / self.total if self.total else 0
                linhas.append(f"  {valor:7d} ch  {fracao:5.0%}  {nome}")
        linhas.append(f"  {self.total:7d} ch  100%  TOTAL (~{self.tokens_aprox} tokens)")
        return "\n".join(linhas)


@dataclass
class Corte:
    """O que foi sacrificado, e quanto isso rendeu."""

    resultados_encolhidos: int = 0
    turnos_removidos: int = 0
    caracteres_liberados: int = 0
    coube: bool = True
    motivos: list[str] = field(default_factory=list)

    @property
    def houve_corte(self) -> bool:
        return bool(self.resultados_encolhidos or self.turnos_removidos)


def medir(mensagens) -> Medida:
    """Mede uma lista de `Mensagem` do `message_builder`."""
    from james.llm.message_builder import (
        ORIGEM_ORIENTACAO,
        ORIGEM_SISTEMA,
        ORIGEM_ATUAL,
    )

    m = Medida()
    for msg in mensagens:
        n = tamanho(msg.text) + tamanho(msg.tool_result)
        for chamada in (msg.tool_calls or []):
            n += tamanho(chamada.name) + tamanho(chamada.args)
        if msg.audio_wav:
            m.audio += tamanho(msg.audio_wav)

        origem = getattr(msg, "origem", "")
        if origem == ORIGEM_SISTEMA:
            m.sistema += n
        elif origem == ORIGEM_ORIENTACAO:
            m.orientacao += n
        elif origem == ORIGEM_ATUAL:
            m.turno_atual += n
        elif msg.tool_result is not None:
            m.resultados += n
        else:
            m.historico += n
    return m


# ------------------------------------------------------------------ o corte


# Quanto sobra de um resultado antigo depois de encolhido. Não é zero de
# propósito: "a ferramenta rodou e devolveu algo" é informação que muda a
# resposta do modelo, e apagar o turno inteiro quebraria o par com a chamada.
SOBRA_DE_RESULTADO = 300


def _encolher(valor, limite: int):
    """Reduz um resultado preservando a FORMA.

    Cortar o JSON no meio produziria texto inválido, e um modelo lendo JSON
    quebrado alucina o resto com confiança. Aqui a estrutura sobrevive e só o
    conteúdo encolhe — o modelo vê que houve um resultado, vê o formato, e vê
    que o detalhe foi omitido.
    """
    if isinstance(valor, str):
        if len(valor) <= limite:
            return valor
        return valor[:limite] + f"… [+{len(valor) - limite} caracteres omitidos]"

    if isinstance(valor, dict):
        saida = {}
        restante = limite
        for chave, item in valor.items():
            if restante <= 0:
                saida["…"] = f"+{len(valor) - len(saida)} campos omitidos"
                break
            reduzido = _encolher(item, max(40, restante // 2))
            saida[chave] = reduzido
            restante -= tamanho(reduzido)
        return saida

    if isinstance(valor, (list, tuple)):
        saida = []
        restante = limite
        for item in valor:
            if restante <= 0:
                saida.append(f"… +{len(valor) - len(saida)} itens omitidos")
                break
            reduzido = _encolher(item, max(40, restante // 2))
            saida.append(reduzido)
            restante -= tamanho(reduzido)
        return saida

    return valor


def aplicar(mensagens: list, teto: int = TETO_PADRAO) -> Corte:
    """Encaixa a lista de mensagens no teto, **modificando-a no lugar**.

    Devolve o que foi sacrificado. Nunca toca no system prompt nem no turno
    atual, e nunca deixa um resultado de tool sem a chamada correspondente.
    """
    from james.llm.message_builder import (
        ORIGEM_ORIENTACAO,
        ORIGEM_SISTEMA,
        ORIGEM_ATUAL,
    )

    corte = Corte()
    medida = medir(mensagens)
    if medida.total <= teto:
        return corte

    intocaveis = {ORIGEM_SISTEMA, ORIGEM_ATUAL, ORIGEM_ORIENTACAO}

    # --- 1. resultados antigos encolhem, do mais velho para o mais novo ---
    for msg in mensagens:
        if medida.total <= teto:
            break
        if getattr(msg, "origem", "") in intocaveis or msg.tool_result is None:
            continue
        antes = tamanho(msg.tool_result)
        if antes <= SOBRA_DE_RESULTADO:
            continue
        msg.tool_result = _encolher(msg.tool_result, SOBRA_DE_RESULTADO)
        ganho = antes - tamanho(msg.tool_result)
        corte.resultados_encolhidos += 1
        corte.caracteres_liberados += ganho
        medida = medir(mensagens)

    if medida.total <= teto:
        corte.motivos.append("resultados antigos encolhidos")
        return corte

    # --- 2. turnos antigos saem, em blocos que não quebram pares ---
    corte.motivos.append("resultados antigos encolhidos")
    removidos = _remover_turnos_antigos(mensagens, teto, intocaveis)
    corte.turnos_removidos = removidos
    if removidos:
        corte.motivos.append("turnos antigos removidos")

    medida = medir(mensagens)
    corte.coube = medida.total <= teto
    if not corte.coube:
        # Não caber depois de tudo significa que o INTOCÁVEL já não cabe: o
        # system prompt mais o turno atual passaram do teto sozinhos. Cortar
        # qualquer um dos dois seria pior que estourar, então o James tenta e
        # deixa o provedor recusar — com uma linha no log dizendo o porquê.
        logger.warning(
            "Contexto acima do teto mesmo após o corte (%d > %d). "
            "Sistema e turno atual não são cortáveis.",
            medida.total, teto,
        )
    return corte


def _remover_turnos_antigos(mensagens: list, teto: int, intocaveis: set) -> int:
    """Tira do começo, sempre parando num ponto que pode abrir a conversa.

    Um histórico que começa com resposta de ferramenta sem a chamada é
    rejeitado pela API — e aí o corte que devia salvar a requisição é o que a
    derruba.
    """
    removidos = 0
    while medir(mensagens).total > teto:
        indice = next(
            (
                i for i, m in enumerate(mensagens)
                if getattr(m, "origem", "") not in intocaveis
            ),
            None,
        )
        if indice is None:
            break

        fim = indice + 1
        # Leva junto os resultados que pertencem a esta mensagem: separá-los
        # deixaria a resposta órfã.
        while fim < len(mensagens) and mensagens[fim].tool_result is not None:
            fim += 1

        if fim <= indice:
            break
        del mensagens[indice:fim]
        removidos += fim - indice

        if not any(getattr(m, "origem", "") not in intocaveis for m in mensagens):
            break
    return removidos
