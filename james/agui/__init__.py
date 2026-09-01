"""AG-UI — o protocolo que liga o James a interfaces de agente.

Quatro peças, e cada uma resolve um problema que só apareceu ao encaixar o
protocolo no que já existia:

    eventos.py     o formato de fio (camelCase) e os construtores
    sequencia.py   a gramática — o que impede frase pela metade
    fluxo.py       um run, e a política de o que pode ser descartado
    adaptador.py   traduz o que o `StateBus` já publica

Esta camada NÃO substitui o `/events`, que continua sendo a transmissão que
todo mundo vê. Ela acrescenta `POST /ag-ui`, que é 1:1 com uma requisição e
tem ciclo de execução. Os dois convivem de propósito: trocar um pelo outro
perderia o espectador, e ter só o antigo não daria `runId`.
"""

from james.agui.adaptador import AdaptadorDeEstado, AdaptadorDeFerramenta
from james.agui.fluxo import FluxoDeRun
from james.agui.sequencia import OrdemInvalida, Sequencia

__all__ = [
    "AdaptadorDeEstado",
    "AdaptadorDeFerramenta",
    "FluxoDeRun",
    "OrdemInvalida",
    "Sequencia",
]
