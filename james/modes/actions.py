"""O que um gesto pode, de fato, fazer.

Esta é a fronteira entre "a câmera viu uma mão" e "o computador mudou". Ela
existe separada do rastreamento por um motivo: o rastreamento é probabilístico
— sombra, luz ruim e uma mão passando por acaso produzem classificações
erradas — e nada probabilístico deveria ter acesso direto ao sistema.

Três travas, em ordem:

1. **Lista fechada de ações.** Um gesto não chama ferramenta por nome; ele pede
   uma das ações de `ACOES_DE_GESTO`. Não existe caminho de "gesto" para
   "mover arquivo", nem por configuração errada nem por engano.
2. **O guard continua valendo.** As ações que mexem no sistema passam pelo
   mesmo guard que uma ordem falada. Gesto não é um atalho por fora.
3. **Nível 2 é recusado, não perguntado.** Se o guard responder CONFIRM, a ação
   morre aqui. Confirmar exige saber quem está pedindo, e uma mão na frente da
   câmera não identifica ninguém — pode ser outra pessoa, pode ser uma foto.
   Promover o gesto a uma pergunta transformaria "qualquer um na sala" em
   "qualquer um na sala que consiga dizer sim".

Repare no que sobra: parar, pausar a escuta, mexer no volume e desligar o
próprio modo. Tudo reversível em um segundo. É o teto certo para um comando
que pode disparar sem querer.
"""

from __future__ import annotations

from typing import Callable

from james.logs import audit, get_logger
from james.permissions.guard import Decision

logger = get_logger("james.modes.acoes")

# Ação de gesto -> (ferramenta, argumentos). O que não está aqui é tratado
# internamente (parar, pausar) ou não existe.
_FERRAMENTAS: dict[str, tuple[str, dict]] = {
    "volume_mais": ("ajustar_volume", {"acao": "aumentar"}),
    "volume_menos": ("ajustar_volume", {"acao": "diminuir"}),
    "mudo": ("ajustar_volume", {"acao": "mutar"}),
}


class GestureActions:
    """Executa as ações de gesto. Devolve sempre um texto do que aconteceu."""

    def __init__(
        self,
        guard,
        registry,
        *,
        on_parar: Callable[[], None] | None = None,
        on_pausar: Callable[[], None] | None = None,
        on_desligar_modo: Callable[[], None] | None = None,
    ) -> None:
        self.guard = guard
        self.registry = registry
        self._on_parar = on_parar
        self._on_pausar = on_pausar
        self._on_desligar_modo = on_desligar_modo

    def __call__(self, gesto: str, acao: str) -> str:
        return self.executar(gesto, acao)

    def executar(self, gesto: str, acao: str) -> str:
        if acao == "parar":
            return self._chamar(self._on_parar, "parar")
        if acao == "pausar_escuta":
            return self._chamar(self._on_pausar, "pausar a escuta")
        if acao == "desligar_modo":
            return self._chamar(self._on_desligar_modo, "desligar o modo")

        alvo = _FERRAMENTAS.get(acao)
        if alvo is None:
            logger.warning("Gesto '%s' pediu ação desconhecida: %r", gesto, acao)
            return "ação desconhecida"
        return self._via_guard(gesto, *alvo)

    # ------------------------------------------------------------ internos

    def _chamar(self, callback: Callable[[], None] | None, rotulo: str) -> str:
        if callback is None:
            return f"não sei {rotulo} nesta execução"
        callback()
        return rotulo

    def _via_guard(self, gesto: str, ferramenta: str, argumentos: dict) -> str:
        veredito = self.guard.evaluate(ferramenta, dict(argumentos))

        if veredito.decision is Decision.CONFIRM:
            # Ver o docstring do módulo: gesto não escala para Nível 2.
            audit("gesto_recusado", gesto=gesto, ferramenta=ferramenta, motivo="nivel_2")
            logger.info(
                "Gesto '%s' pediu '%s', que exige confirmação: recusado.",
                gesto,
                ferramenta,
            )
            return "recusado: exige confirmação falada"

        if veredito.decision is not Decision.ALLOW:
            audit("gesto_recusado", gesto=gesto, ferramenta=ferramenta, motivo=veredito.reason)
            return f"recusado: {veredito.reason}"

        tool = self.registry.get(ferramenta) if self.registry is not None else None
        if tool is None:
            return "ferramenta indisponível"

        resultado = tool.handler(dict(veredito.args or argumentos))
        if not getattr(resultado, "ok", False):
            return f"falhou: {getattr(resultado, 'speech', '')}".strip()
        return ferramenta
