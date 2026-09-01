"""Eventos do protocolo AG-UI — o formato de fio, e as regras de ordem.

O AG-UI é uma união discriminada por `type`, serializada em JSON **camelCase**.
Este arquivo produz eventos já no formato de fio, em vez de criar uma camada de
objetos Python que depois seria traduzida: o contrato É o JSON, e um andar de
tradução no meio só adiciona lugar para divergir.

O que as funções abaixo dão de segurança é a assinatura — `run_started` exige
`thread_id` e `run_id`, e não deixa esquecer. O que elas NÃO conseguem garantir
sozinhas é a ordem, que é onde os erros de verdade moram; para isso existe a
`Sequencia`, mais abaixo.

## Por que a ordem importa tanto

O frontend concatena os `delta` de `TEXT_MESSAGE_CONTENT` na ordem em que
chegam. Um `CONTENT` sem `START` antes não tem onde ser colado; um `CONTENT`
depois do `END` chega tarde demais. Nos dois casos o navegador não quebra — ele
mostra uma frase **incompleta**, e ninguém percebe que faltou pedaço.

Erro que não aparece é o mais caro de achar. Validar na emissão transforma isso
numa exceção com nome, na linha que causou.

## Nomes

Os campos vão em camelCase porque é o que o protocolo define na rede. Os
argumentos das funções vão em snake_case porque é Python. A tradução acontece
num lugar só: aqui.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any


class TipoEvento(str, Enum):
    """Os tipos que o James emite. Herda `str` para serializar direto."""

    # --- ciclo de execução ---
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"

    # --- mensagens ---
    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"

    # --- ferramentas ---
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"

    # --- estado ---
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    MESSAGES_SNAPSHOT = "MESSAGES_SNAPSHOT"

    # --- atividades (só interface; não voltam ao contexto do modelo) ---
    ACTIVITY_SNAPSHOT = "ACTIVITY_SNAPSHOT"
    ACTIVITY_DELTA = "ACTIVITY_DELTA"

    # --- extensão nossa ---
    CUSTOM = "CUSTOM"


def _agora_ms() -> int:
    return int(time.time() * 1000)


def _evento(tipo: TipoEvento, **campos: Any) -> dict[str, Any]:
    """Monta o envelope comum. `None` é omitido — campo opcional ausente."""
    saida: dict[str, Any] = {"type": tipo.value, "timestamp": _agora_ms()}
    saida.update({k: v for k, v in campos.items() if v is not None})
    return saida


# ------------------------------------------------------------ ciclo de vida


def run_started(thread_id: str, run_id: str, parent_run_id: str | None = None):
    return _evento(
        TipoEvento.RUN_STARTED,
        threadId=thread_id, runId=run_id, parentRunId=parent_run_id,
    )


def run_finished(thread_id: str, run_id: str, result: Any = None):
    return _evento(
        TipoEvento.RUN_FINISHED, threadId=thread_id, runId=run_id, result=result
    )


def run_error(message: str, code: str | None = None):
    return _evento(TipoEvento.RUN_ERROR, message=message, code=code)


def step_started(nome: str):
    return _evento(TipoEvento.STEP_STARTED, stepName=nome)


def step_finished(nome: str):
    return _evento(TipoEvento.STEP_FINISHED, stepName=nome)


# ---------------------------------------------------------------- mensagens


def text_message_start(message_id: str, role: str = "assistant"):
    return _evento(TipoEvento.TEXT_MESSAGE_START, messageId=message_id, role=role)


def text_message_content(message_id: str, delta: str):
    """Um pedaço de texto. `delta` vazio é recusado de propósito.

    Delta vazio não transporta informação e ainda assim gasta um evento no
    fio — e, pior, esconde um bug do lado de quem produz (um `on_text` chamado
    com string vazia é quase sempre engano).
    """
    if not delta:
        raise ValueError("TEXT_MESSAGE_CONTENT sem texto — delta vazio é engano.")
    return _evento(TipoEvento.TEXT_MESSAGE_CONTENT, messageId=message_id, delta=delta)


def text_message_end(message_id: str):
    return _evento(TipoEvento.TEXT_MESSAGE_END, messageId=message_id)


# -------------------------------------------------------------- ferramentas


def tool_call_start(tool_call_id: str, nome: str, parent_message_id: str | None = None):
    return _evento(
        TipoEvento.TOOL_CALL_START,
        toolCallId=tool_call_id, toolCallName=nome, parentMessageId=parent_message_id,
    )


def tool_call_args(tool_call_id: str, delta: str):
    """Argumentos chegam como STRING JSON picotada.

    Só depois de concatenar tudo e receber o END é que o JSON é interpretado —
    um pedaço isolado quase nunca é JSON válido, e tentar interpretá-lo cedo é
    o erro clássico de quem implementa este protocolo pela primeira vez.
    """
    return _evento(TipoEvento.TOOL_CALL_ARGS, toolCallId=tool_call_id, delta=delta)


def tool_call_end(tool_call_id: str):
    return _evento(TipoEvento.TOOL_CALL_END, toolCallId=tool_call_id)


def tool_call_result(tool_call_id: str, message_id: str, content: str, role: str = "tool"):
    return _evento(
        TipoEvento.TOOL_CALL_RESULT,
        toolCallId=tool_call_id, messageId=message_id, content=content, role=role,
    )


# ------------------------------------------------------------------- estado


def state_snapshot(estado: dict[str, Any]):
    return _evento(TipoEvento.STATE_SNAPSHOT, snapshot=estado)


def state_delta(patch: list[dict[str, Any]]):
    """JSON Patch (RFC 6902), aplicado sobre o último instantâneo, em ordem."""
    return _evento(TipoEvento.STATE_DELTA, delta=patch)


def messages_snapshot(mensagens: list[dict[str, Any]]):
    return _evento(TipoEvento.MESSAGES_SNAPSHOT, messages=mensagens)


# --------------------------------------------------------------- atividades


def activity_snapshot(message_id: str, tipo_atividade: str, conteudo: Any):
    """Painel estruturado — plano, progresso, fontes.

    O ganho que faz isto valer mais que parece: atividades são **só de
    interface**. Não voltam para o contexto do modelo. Um painel rico com o
    plano inteiro e o progresso de cada passo custa ZERO token, o que num
    projeto que conta requisição muda o que dá para mostrar.
    """
    return _evento(
        TipoEvento.ACTIVITY_SNAPSHOT,
        messageId=message_id, activityType=tipo_atividade, content=conteudo,
    )


def activity_delta(message_id: str, tipo_atividade: str, patch: list[dict[str, Any]]):
    return _evento(
        TipoEvento.ACTIVITY_DELTA,
        messageId=message_id, activityType=tipo_atividade, patch=patch,
    )


# ---------------------------------------------------------------- extensão


def custom(nome: str, valor: Any):
    """Evento nosso, dentro do protocolo. Prefixo `jarvis.` por convenção."""
    return _evento(TipoEvento.CUSTOM, name=nome, value=valor)


def decisao_do_guard(tool_call_id: str, decisao: str, motivo: str = ""):
    """O guard aparecendo na interface — sem deixar de ser o guard.

    O AG-UI transporta a INTENÇÃO de chamar uma ferramenta e o RESULTADO dela.
    Ele não decide se pode: quem decide é o guard, em Python, entre o
    TOOL_CALL_END e a execução. Este evento é o relato dessa decisão para a
    tela, nunca a decisão em si.
    """
    return custom(
        "jarvis.guard.decision",
        {"toolCallId": tool_call_id, "decision": decisao, "reason": motivo},
    )
