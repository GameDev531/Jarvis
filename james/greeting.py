"""A saudação da partida. Sem modelo, sem rede, sem cota.

Antes disto a saudação viajava junto com o primeiro comando, para não gastar
uma requisição só para cumprimentar. Economizava, mas errava o alvo: **uma
saudação que só acontece depois de você falar não é uma saudação.** É um
preâmbulo colado na resposta.

Consertar isso não custou a requisição que se queria evitar, porque cumprimentar
não precisa de um modelo de linguagem. É hora do dia mais um nome. Gerar aqui:

  - **zero requisição.** A cota diária do OpenRouter é de 50 sem crédito, e
    gastar uma com "boa noite, senhor" é desperdício puro.
  - **zero espera.** A rede da máquina de destino leva ~1.900 ms só para
    conectar. Com o modelo no caminho, o James subiria e ficaria mudo por
    alguns segundos — exatamente o oposto de anunciar que está de pé.
  - **funciona offline.** A saudação é a primeira prova de que ele está vivo,
    e ela não pode depender de internet.

O que se perde é a variação infinita que um modelo daria. Compensa-se com
algumas formas por período, sorteadas — o bastante para não virar bordão.

A apresentação da primeira execução é texto fixo, e isso é melhoria, não
preguiça: é a única frase da vida da instalação que precisa estar exatamente
certa sobre o que ele faz e sobre o guard. Deixar um modelo improvisar essa
explicação é como ela sai errada.
"""

from __future__ import annotations

import random
from datetime import datetime

# Faixas de hora. `madrugada` existe separada de `noite` porque quem liga o
# assistente às 3 da manhã não quer "boa noite" — quer o mínimo de cerimônia.
_MADRUGADA = range(0, 6)
_MANHA = range(6, 12)
_TARDE = range(12, 18)

_FORMAS = {
    "madrugada": [
        "Ainda acordado, {t}?",
        "Boa madrugada, {t}.",
        "Às ordens, {t}. Hora avançada.",
        "{T}. Sistemas de pé.",
    ],
    "manha": [
        "Bom dia, {t}.",
        "Bom dia, {t}. Tudo operacional.",
        "Bom dia, {t}. Às ordens.",
        "Sistemas prontos. Bom dia, {t}.",
    ],
    "tarde": [
        "Boa tarde, {t}.",
        "Boa tarde, {t}. Às ordens.",
        "Boa tarde, {t}. Tudo em ordem por aqui.",
        "Pronto quando o {t} estiver.",
    ],
    "noite": [
        "Boa noite, {t}.",
        "Boa noite, {t}. Às ordens.",
        "Boa noite, {t}. Sistemas operacionais.",
        "De volta, {t}. Boa noite.",
    ],
}

# Texto exato da primeira execução. Curto porque ninguém decora um manual dito
# em voz alta — e as duas coisas que importam são o nome e o guard.
_PRIMEIRA_VEZ = (
    "{T}, sou o {nome}. Chame meu nome e eu atendo — ouço, respondo e ajo "
    "nesta máquina. Toda ação de risco pede sua confirmação antes de "
    "executar; nada acontece pelas minhas costas."
)


def periodo(hora: int) -> str:
    if hora in _MADRUGADA:
        return "madrugada"
    if hora in _MANHA:
        return "manha"
    if hora in _TARDE:
        return "tarde"
    return "noite"


def saudacao(
    tratamento: str = "senhor",
    nome: str = "James",
    momento: datetime | None = None,
    primeira_vez: bool = False,
    rng: random.Random | None = None,
) -> str:
    """A frase que o James diz ao subir.

    `rng` existe para o teste poder fixar o sorteio. Em produção fica `None`.
    """
    agora = momento or datetime.now()
    tratamento = (tratamento or "senhor").strip() or "senhor"
    campos = {"t": tratamento, "T": tratamento.capitalize(), "nome": nome or "James"}

    if primeira_vez:
        return _PRIMEIRA_VEZ.format(**campos)

    sorteio = rng or random
    return sorteio.choice(_FORMAS[periodo(agora.hour)]).format(**campos)
