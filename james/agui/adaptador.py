"""Traduz o que o `StateBus` já publica para eventos AG-UI.

O barramento existe desde a interface holográfica e funciona. Este arquivo NÃO
o substitui: lê o que ele publica e traduz. O orquestrador continua chamando
`bus.publish(estado=...)` sem saber que existe um protocolo do outro lado.

## A distinção que o barramento já fazia

O `StateBus` separa, no próprio `publish`, o que é ESTADO do que é
ACONTECIMENTO:

    "`log`, `transcricao` e `resposta` são acontecimentos, não estado:
     guardá-los faria a tela repetir a última fala a cada recarga."

Essa linha, escrita muito antes de existir AG-UI aqui, é exatamente a fronteira
do protocolo:

    estado         ->  STATE_SNAPSHOT / STATE_DELTA
    transcrição    ->  TEXT_MESSAGE_* com role "user"
    resposta       ->  TEXT_MESSAGE_* com role "assistant"
    log            ->  ACTIVITY (só interface, não volta ao modelo)

Não é coincidência feliz: as duas coisas resolvem o mesmo problema — o que vale
para quem chega atrasado e o que só faz sentido no instante.

## JSON Patch de graça

O `publish(**dados)` recebe chaves planas. Um `{"estado": "PENSANDO"}` vira um
patch de uma operação, sem precisar comparar árvores. Se um dia o estado virar
aninhado, aqui é onde entra um diff de verdade — e o comentário fica para
lembrar que hoje não precisa.
"""

from __future__ import annotations

from typing import Any

from james.agui import eventos as ev
from james.agui.fluxo import FluxoDeRun, novo_id

# As mesmas três chaves que o `StateBus` recusa guardar no instantâneo.
# Duplicar a lista seria criar duas verdades; ela é importada de lá.
from james.ui.bus import CHAVES_EFEMERAS


# Estados em que o James não está fazendo nada. Voltar a um deles é o fim do
# turno — e, portanto, o fim do run.
ESTADOS_OCIOSOS = frozenset({"PRONTO", "IDLE", "ERRO"})


class AdaptadorDeEstado:
    """Converte publicações do barramento em eventos de um run.

    Guarda o último estado enviado para poder emitir deltas em vez de
    instantâneos inteiros — que é o ganho de banda que o protocolo promete.

    ## Quando o run acaba

    O protocolo exige exatamente um `RUN_FINISHED` ou `RUN_ERROR`; sem ele o
    cliente fica pendurado esperando.

    O orquestrador diz isso **explicitamente**, publicando `turno="inicio"` e
    `turno="fim"` — este último num `finally`, para que um turno que estoura
    feche o run do mesmo jeito.

    Antes daqui saía uma heurística: "o `estado` voltou para PRONTO, então
    acabou". Ela funcionava e tinha uma armadilha real — o James também está
    PRONTO quando ninguém pediu nada, e o instantâneo inicial já vem assim.
    A heurística continua como RESERVA, para quando quem publica não é o
    orquestrador (um teste, uma ferramenta isolada), mas o caminho normal não
    adivinha mais nada.
    """

    def __init__(self, fluxo: FluxoDeRun, fechar_no_fim: bool = True) -> None:
        self.fluxo = fluxo
        self.fechar_no_fim = fechar_no_fim
        self._ultimo: dict[str, Any] = {}
        self._mandou_snapshot = False
        self._saiu_do_ocioso = False
        # Vira `True` no primeiro `turno=...` que chegar, e a partir daí a
        # heurística de ocioso fica desligada para sempre neste run.
        self._explicito = False

    # ------------------------------------------------------------- estado

    def snapshot(self, estado: dict[str, Any]) -> None:
        """O estado inteiro. Vai uma vez, para quem acabou de conectar."""
        self._ultimo = dict(estado)
        self._mandou_snapshot = True
        self.fluxo.emitir(ev.state_snapshot(dict(estado)))

    def _patch(self, dados: dict[str, Any]) -> list[dict[str, Any]]:
        """JSON Patch para chaves planas — que é o que o barramento publica.

        `add` quando a chave é nova, `replace` quando muda. A distinção existe
        no RFC e alguns clientes a levam a sério; usar `replace` numa chave
        inexistente é erro por lá.
        """
        operacoes = []
        for chave, valor in dados.items():
            if chave in CHAVES_EFEMERAS:
                continue
            if self._ultimo.get(chave) == valor and chave in self._ultimo:
                continue          # não mudou: não vale um evento
            operacoes.append({
                "op": "replace" if chave in self._ultimo else "add",
                "path": f"/{chave}",
                "value": valor,
            })
            self._ultimo[chave] = valor
        return operacoes

    # -------------------------------------------------------- a tradução

    def publicar(self, dados: dict[str, Any]) -> None:
        """Recebe um `publish` do barramento e emite o que ele significa."""
        if not dados:
            return

        # 1. A marca explícita de turno tem precedência sobre qualquer
        #    heurística: quem sabe que o turno acabou é quem o executou.
        if "turno" in dados:
            # A PRESENÇA da marca já diz que quem publica é o orquestrador, e
            # portanto que o palpite não é mais necessário. Deixar os dois
            # ativos seria pior que só o palpite: um `estado=PRONTO` no meio do
            # turno fecharia o run antes da resposta sair.
            self._explicito = True
            if dados["turno"] == "fim":
                self.fluxo.concluir({"motivo": "turno_encerrado"})
                return

        # 2. Acontecimentos viram mensagens ou atividade.
        if "transcricao" in dados:
            self._mensagem(str(dados["transcricao"]), papel="user")
        if "resposta" in dados:
            self._mensagem(str(dados["resposta"]), papel="assistant")
        if "log" in dados:
            self.fluxo.emitir(ev.activity_snapshot(
                novo_id("activity"), "LOG", {"linha": str(dados["log"])},
            ))

        # 3. O resto é estado durável.
        if not self._mandou_snapshot:
            # Primeiro contato sem instantâneo: mandar delta seria pedir ao
            # cliente que aplicasse patch sobre um vazio que ele não tem.
            self.snapshot({k: v for k, v in dados.items() if k not in CHAVES_EFEMERAS})
            return

        operacoes = self._patch(dados)
        if operacoes:
            self.fluxo.emitir(ev.state_delta(operacoes))

        self._talvez_encerrar(dados)

    def _talvez_encerrar(self, dados: dict[str, Any]) -> None:
        if self._explicito or not self.fechar_no_fim or "estado" not in dados:
            return
        ocioso = str(dados["estado"]).upper() in ESTADOS_OCIOSOS
        if not ocioso:
            self._saiu_do_ocioso = True
            return
        if self._saiu_do_ocioso:
            self.fluxo.concluir({"estado": dados["estado"]})

    def _mensagem(self, texto: str, papel: str) -> None:
        if not texto:
            return
        mid = novo_id("msg")
        self.fluxo.emitir(ev.text_message_start(mid, role=papel))
        self.fluxo.emitir(ev.text_message_content(mid, texto))
        self.fluxo.emitir(ev.text_message_end(mid))


class AdaptadorDeFerramenta:
    """A chamada de uma ferramenta, na ordem que o James exige.

        TOOL_CALL_START
        TOOL_CALL_ARGS...
        TOOL_CALL_END
              |
        guard decide          <- CUSTOM jarvis.guard.decision
        confirmação, se for o caso
        ferramenta executa
              |
        TOOL_CALL_RESULT

    A posição do guard não é detalhe de apresentação. O AG-UI transporta a
    INTENÇÃO de chamar e o RESULTADO; ele não decide se pode. Emitir o
    `TOOL_CALL_RESULT` antes de o guard falar seria mostrar na tela um
    resultado que talvez nunca devesse existir — e, pior, sugeriria a quem lê o
    código que a execução acontece no protocolo, não no guard.

    O validador de ordem também recusa RESULT antes de END, então a regra não
    depende de ninguém lembrar dela.
    """

    def __init__(self, fluxo: FluxoDeRun) -> None:
        self.fluxo = fluxo

    def anunciar(self, nome: str, argumentos: str, parent_message_id=None) -> str:
        """Anuncia a intenção. Devolve o `toolCallId`."""
        tid = novo_id("tool")
        self.fluxo.emitir(ev.tool_call_start(tid, nome, parent_message_id))
        if argumentos:
            self.fluxo.emitir(ev.tool_call_args(tid, argumentos))
        self.fluxo.emitir(ev.tool_call_end(tid))
        return tid

    def decisao(self, tool_call_id: str, decisao: str, motivo: str = "") -> None:
        self.fluxo.emitir(ev.decisao_do_guard(tool_call_id, decisao, motivo))

    def resultado(self, tool_call_id: str, conteudo: str) -> None:
        self.fluxo.emitir(
            ev.tool_call_result(tool_call_id, novo_id("msg"), conteudo)
        )
