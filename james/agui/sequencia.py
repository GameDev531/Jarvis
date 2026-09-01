"""Validação de ordem — o que impede a interface de mostrar frase pela metade.

O AG-UI não é uma lista de eventos: é uma **sequência com gramática**. Um
`TEXT_MESSAGE_CONTENT` sem `START` antes não tem onde ser colado. Um
`TOOL_CALL_ARGS` depois do `END` chega com o JSON já interpretado. Um segundo
`RUN_FINISHED` deixa o cliente sem saber qual valeu.

O que torna isso perigoso é que o navegador **não quebra** com nenhum desses.
Ele mostra menos texto, ou um painel que não atualiza, ou um resultado que não
aparece — e a pessoa conclui que o assistente "ficou lento" ou "não respondeu
direito". Bug que se disfarça de qualidade ruim é o mais caro de encontrar.

Esta classe valida na EMISSÃO. O erro aparece na linha que produziu o evento
errado, com o nome do que faltou — em vez de aparecer três camadas depois, no
navegador de outra pessoa.

## Por que não validar no cliente

Porque aí já é tarde: o dado saiu, o run está no meio, e a única saída seria
descartar o resto. Validar na origem permite falhar antes de emitir, quando
ainda dá para consertar o run — ou pelo menos encerrá-lo com `RUN_ERROR`
honesto em vez de deixar a tela parada esperando um `RUN_FINISHED` que nunca
vem.
"""

from __future__ import annotations

from typing import Any

from james.agui.eventos import TipoEvento


class OrdemInvalida(RuntimeError):
    """Um evento chegou fora da gramática do protocolo."""


# Eventos que podem aparecer antes do RUN_STARTED ou depois do fim: nenhum.
# A regra é literal no protocolo, e vale a pena ser literal aqui também.
_FECHAM_O_RUN = (TipoEvento.RUN_FINISHED.value, TipoEvento.RUN_ERROR.value)


class Sequencia:
    """Acompanha um run e recusa o que sair da ordem.

    Uma instância por run. Ela guarda o mínimo necessário — quais mensagens e
    chamadas estão abertas —, não o conteúdo: o objetivo é validar a forma, não
    duplicar o estado.
    """

    def __init__(self, thread_id: str, run_id: str) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.comecou = False
        self.terminou = False
        self._mensagens_abertas: set[str] = set()
        self._mensagens_fechadas: set[str] = set()
        self._chamadas_abertas: set[str] = set()
        self._chamadas_fechadas: set[str] = set()
        self._passos_abertos: list[str] = []
        self.emitidos = 0

    # ------------------------------------------------------------ validação

    def validar(self, evento: dict[str, Any]) -> None:
        """Levanta `OrdemInvalida` se este evento não pode vir agora."""
        tipo = evento.get("type")

        if self.terminou:
            raise OrdemInvalida(
                f"{tipo} depois do fim do run — o run {self.run_id} já foi encerrado."
            )

        if tipo == TipoEvento.RUN_STARTED.value:
            if self.comecou:
                raise OrdemInvalida(f"RUN_STARTED repetido no run {self.run_id}.")
            self.comecou = True
            self.emitidos += 1
            return

        if not self.comecou:
            raise OrdemInvalida(f"{tipo} antes do RUN_STARTED.")

        despacho = {
            TipoEvento.TEXT_MESSAGE_START.value: self._msg_start,
            TipoEvento.TEXT_MESSAGE_CONTENT.value: self._msg_content,
            TipoEvento.TEXT_MESSAGE_END.value: self._msg_end,
            TipoEvento.TOOL_CALL_START.value: self._tool_start,
            TipoEvento.TOOL_CALL_ARGS.value: self._tool_args,
            TipoEvento.TOOL_CALL_END.value: self._tool_end,
            TipoEvento.TOOL_CALL_RESULT.value: self._tool_result,
            TipoEvento.STEP_STARTED.value: self._step_start,
            TipoEvento.STEP_FINISHED.value: self._step_end,
        }
        verificador = despacho.get(tipo)
        if verificador is not None:
            verificador(evento)

        if tipo in _FECHAM_O_RUN:
            self._fechar(tipo)

        self.emitidos += 1

    # -------------------------------------------------------------- mensagens

    def _msg_start(self, evento) -> None:
        mid = evento.get("messageId")
        if mid in self._mensagens_abertas:
            raise OrdemInvalida(f"TEXT_MESSAGE_START repetido para {mid}.")
        if mid in self._mensagens_fechadas:
            # Reabrir um id já fechado faria o frontend anexar texto novo numa
            # bolha antiga — o pior tipo de erro, porque parece funcionar.
            raise OrdemInvalida(f"messageId {mid} já foi encerrado; use outro.")
        self._mensagens_abertas.add(mid)

    def _msg_content(self, evento) -> None:
        mid = evento.get("messageId")
        if mid not in self._mensagens_abertas:
            raise OrdemInvalida(
                f"TEXT_MESSAGE_CONTENT para {mid} sem START antes — "
                "o texto não teria onde ser colado."
            )

    def _msg_end(self, evento) -> None:
        mid = evento.get("messageId")
        if mid not in self._mensagens_abertas:
            raise OrdemInvalida(f"TEXT_MESSAGE_END para {mid} que não está aberto.")
        self._mensagens_abertas.discard(mid)
        self._mensagens_fechadas.add(mid)

    # ------------------------------------------------------------ ferramentas

    def _tool_start(self, evento) -> None:
        tid = evento.get("toolCallId")
        if tid in self._chamadas_abertas or tid in self._chamadas_fechadas:
            raise OrdemInvalida(f"toolCallId {tid} repetido no mesmo run.")
        self._chamadas_abertas.add(tid)

    def _tool_args(self, evento) -> None:
        tid = evento.get("toolCallId")
        if tid not in self._chamadas_abertas:
            raise OrdemInvalida(
                f"TOOL_CALL_ARGS para {tid} sem START — os argumentos chegam "
                "picotados e precisam de um acumulador já aberto."
            )

    def _tool_end(self, evento) -> None:
        tid = evento.get("toolCallId")
        if tid not in self._chamadas_abertas:
            raise OrdemInvalida(f"TOOL_CALL_END para {tid} que não está aberto.")
        self._chamadas_abertas.discard(tid)
        self._chamadas_fechadas.add(tid)

    def _tool_result(self, evento) -> None:
        tid = evento.get("toolCallId")
        if tid in self._chamadas_abertas:
            # Resultado antes do END significa que a ferramenta rodou com os
            # argumentos ainda chegando. No James isso é mais grave que um erro
            # de protocolo: é ter executado ANTES do guard decidir.
            raise OrdemInvalida(
                f"TOOL_CALL_RESULT para {tid} antes do END — a ferramenta teria "
                "rodado com os argumentos incompletos, e antes do guard."
            )
        if tid not in self._chamadas_fechadas:
            raise OrdemInvalida(f"TOOL_CALL_RESULT para {tid}, que nunca começou.")

    # ----------------------------------------------------------------- passos

    def _step_start(self, evento) -> None:
        self._passos_abertos.append(evento.get("stepName"))

    def _step_end(self, evento) -> None:
        nome = evento.get("stepName")
        if nome not in self._passos_abertos:
            raise OrdemInvalida(f"STEP_FINISHED('{nome}') sem STEP_STARTED.")
        # Remove a ocorrência mais recente: passos podem se repetir, e fechar o
        # mais antigo embaralharia a duração de cada um.
        for i in range(len(self._passos_abertos) - 1, -1, -1):
            if self._passos_abertos[i] == nome:
                del self._passos_abertos[i]
                break

    # ------------------------------------------------------------ encerramento

    def _fechar(self, tipo: str) -> None:
        self.terminou = True
        # Aberto no fim não é erro de protocolo — é vazamento nosso. Vira aviso
        # no log em vez de exceção, porque o run já acabou e derrubá-lo agora
        # não devolve o que ficou faltando.
        if self._mensagens_abertas or self._chamadas_abertas or self._passos_abertos:
            from james.logs import get_logger

            get_logger("james.agui").warning(
                "Run %s encerrado com %d mensagem(ns), %d chamada(s) e %d passo(s) "
                "em aberto.",
                self.run_id, len(self._mensagens_abertas),
                len(self._chamadas_abertas), len(self._passos_abertos),
            )

    @property
    def pendencias(self) -> dict[str, list[str]]:
        """O que ficou aberto. Útil para teste e para diagnóstico."""
        return {
            "mensagens": sorted(self._mensagens_abertas),
            "chamadas": sorted(self._chamadas_abertas),
            "passos": list(self._passos_abertos),
        }
