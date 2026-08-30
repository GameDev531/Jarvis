"""Orçamento de caracteres da voz na nuvem.

A ElevenLabs cobra por **caractere sintetizado**, e o plano grátis dá 10.000
por mês. Isso é pouco de um jeito que não é óbvio: uma resposta média do James
tem uns 150 caracteres, então são cerca de 66 respostas no mês inteiro — duas
por dia.

Sem um contador, o que acontece é o pior dos mundos: o James fala lindamente
por três dias, a cota acaba, e a partir daí ele fica mudo sem explicar por quê.
O erro da API viria como um 401 genérico no meio de um turno.

Com o contador, a passagem é suave e anunciada: ao acabar o orçamento, a
cadeia cai para o Piper local. Você ouve outra voz, que é o próprio aviso.

## Sobre a virada do mês

A ElevenLabs zera na data de assinatura, não no dia 1º. Aqui o mês civil é uma
aproximação deliberada: errar para MENOS de cota é seguro (sobra crédito), e
errar para mais deixaria o James mudo justamente quando ele acha que pode
falar. Quem quiser precisão ajusta `voz.elevenlabs.dia_da_virada`.

## Por que caracteres e não requisições

O `RateLimiter` do LLM conta requisições porque é assim que Gemini e OpenRouter
cobram. Aqui a unidade é outra, e misturar as duas contas daria um número que
não corresponde a nada. Uma frase de 500 caracteres custa o mesmo que cinco de
100 — o que importa é o texto, não quantas vezes você pediu.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Callable

from james.logs import audit, get_logger

logger = get_logger("james.voice.orcamento")

# Plano grátis da ElevenLabs. Um número conservador é melhor que um otimista:
# estourar significa ficar mudo no meio de uma frase.
CARACTERES_GRATIS_POR_MES = 10_000

# Avisa quando passa disto, para dar tempo de decidir antes de acabar.
_LIMIAR_DE_AVISO = 0.8


class CharacterBudget:
    """Conta caracteres sintetizados no mês, com persistência em disco."""

    def __init__(
        self,
        limite_mensal: int = CARACTERES_GRATIS_POR_MES,
        state_path: str | Path | None = None,
        dia_da_virada: int = 1,
        hoje: Callable[[], date] = date.today,
    ) -> None:
        self.limite = max(0, int(limite_mensal))
        self.state_path = Path(state_path) if state_path else None
        self.dia_da_virada = min(28, max(1, int(dia_da_virada)))
        self._hoje = hoje
        self._lock = threading.RLock()
        self._ciclo = self._ciclo_atual()
        self._usado = 0
        self._avisou = False
        self._carregar()

    # ------------------------------------------------------------ o ciclo

    def _ciclo_atual(self) -> str:
        """Identificador do ciclo vigente, do tipo '2026-08'.

        Antes do dia da virada, ainda estamos no ciclo do mês anterior — é o
        que faz `dia_da_virada=15` significar "de 15 a 14", e não "todo dia 15
        o contador zera e volta a zerar no dia 1º".
        """
        d = self._hoje()
        ano, mes = d.year, d.month
        if d.day < self.dia_da_virada:
            mes -= 1
            if mes == 0:
                mes, ano = 12, ano - 1
        return f"{ano:04d}-{mes:02d}"

    def _virar_se_preciso(self) -> None:
        atual = self._ciclo_atual()
        if atual != self._ciclo:
            logger.info(
                "Ciclo de voz virou (%s -> %s): %d caracteres liberados.",
                self._ciclo, atual, self.limite,
            )
            self._ciclo = atual
            self._usado = 0
            self._avisou = False
            self._salvar()

    # ------------------------------------------------------- persistência

    def _carregar(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            dados = json.loads(self.state_path.read_text(encoding="utf-8"))
            if str(dados.get("ciclo")) == self._ciclo:
                self._usado = max(0, int(dados.get("usado", 0)))
                logger.info(
                    "Voz na nuvem: %d/%d caracteres usados neste ciclo.",
                    self._usado, self.limite,
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            # Contador ilegível: recomeçar do zero é melhor que travar. O risco
            # é gastar a mais uma vez; o da alternativa é o James nunca falar.
            logger.warning("Contador de voz ilegível (%s); recomeçando o ciclo.", exc)

    def _salvar(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporario = self.state_path.with_suffix(".tmp")
            temporario.write_text(
                json.dumps({"ciclo": self._ciclo, "usado": self._usado}),
                encoding="utf-8",
            )
            temporario.replace(self.state_path)
        except OSError as exc:
            logger.warning("Não consegui gravar o contador de voz: %s", exc)

    # --------------------------------------------------------------- uso

    def cabe(self, texto: str) -> bool:
        """O texto inteiro cabe no que resta?

        Perguntar ANTES de sintetizar é o que permite cair para o Piper sem
        gastar meia frase na nuvem e a outra metade local — o que soaria como
        duas pessoas diferentes terminando a mesma frase.
        """
        with self._lock:
            self._virar_se_preciso()
            return len(texto or "") <= self.restante

    def consumir(self, texto: str) -> int:
        """Registra o gasto. Devolve quanto foi cobrado."""
        custo = len(texto or "")
        if custo <= 0:
            return 0
        with self._lock:
            self._virar_se_preciso()
            self._usado += custo
            self._salvar()
            usado, limite = self._usado, self.limite
            avisar = (
                not self._avisou
                and limite > 0
                and usado >= limite * _LIMIAR_DE_AVISO
            )
            if avisar:
                self._avisou = True

        if avisar:
            logger.warning(
                "Voz na nuvem em %.0f%% do ciclo (%d/%d caracteres). "
                "Ao acabar, o James passa a falar com a voz local.",
                100 * usado / limite, usado, limite,
            )
            audit("voz_orcamento_alto", usado=usado, limite=limite)
        return custo

    # -------------------------------------------------------------- leitura

    @property
    def restante(self) -> int:
        with self._lock:
            self._virar_se_preciso()
            return max(0, self.limite - self._usado)

    @property
    def usado(self) -> int:
        with self._lock:
            self._virar_se_preciso()
            return self._usado

    @property
    def esgotado(self) -> bool:
        return self.restante <= 0

    def resumo(self) -> dict:
        with self._lock:
            self._virar_se_preciso()
            return {
                "ciclo": self._ciclo,
                "usado": self._usado,
                "limite": self.limite,
                "restante": max(0, self.limite - self._usado),
            }
