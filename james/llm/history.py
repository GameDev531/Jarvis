"""Histórico da conversa, com o pareamento de tool calls garantido (C10).

O problema que este módulo resolve: quando o James executa uma tool e responde
com frase pré-escrita SEM voltar à API (fire-and-forget, seção 20), fica um
`function_call` pendurado no histórico. Na requisição seguinte o Gemini rejeita
ou se confunde, porque a API exige que todo `function_call` tenha o
`function_response` correspondente.

A solução aqui é uma invariante em vez de disciplina: `close_pending_tool_calls`
fecha qualquer chamada sem resposta com um resultado sintético, e é chamada
antes de montar qualquer requisição. Assim o histórico é consistente
independentemente do caminho que a execução tomou.

O texto do usuário entra como marcador quando o comando foi por áudio, e é
preenchido depois pela transcrição local — que roda em segundo plano, enquanto
o James já está falando, sem custar latência.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

AUDIO_PLACEHOLDER = "[comando de voz]"
SYNTHETIC_RESULT = {"status": "ok"}


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None


@dataclass
class Turn:
    """Um item do histórico. `role` é 'user', 'model' ou 'tool'."""

    role: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_name: str | None = None
    tool_result: Any = None
    call_id: str | None = None
    is_audio: bool = False
    transcribed: bool = False
    synthetic: bool = False


class Conversation:
    """Histórico com poda que nunca quebra um par chamada/resposta."""

    def __init__(self, max_turns: int = 12) -> None:
        self.max_turns = max(2, int(max_turns))
        self._turns: list[Turn] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------- escrita

    def add_user_text(self, text: str) -> None:
        with self._lock:
            self._turns.append(Turn(role="user", text=text, transcribed=True))
            self._prune()

    def add_user_audio(self, placeholder: str = AUDIO_PLACEHOLDER) -> int:
        """Registra um comando falado ainda não transcrito.

        Devolve o índice do turno, para que a transcrição em segundo plano
        consiga preenchê-lo depois via `set_user_transcript`.
        """
        with self._lock:
            self._turns.append(Turn(role="user", text=placeholder, is_audio=True))
            self._prune()
            return len(self._turns) - 1

    def set_user_transcript(self, index: int, text: str) -> bool:
        """Preenche a transcrição de um turno de áudio, se ele ainda existir.

        Pode falhar legitimamente: a poda pode ter removido o turno enquanto a
        transcrição rodava. Isso não é erro, só significa que o contexto já saiu
        da janela.
        """
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return False
        with self._lock:
            if not 0 <= index < len(self._turns):
                return False
            turn = self._turns[index]
            if turn.role != "user" or not turn.is_audio or turn.transcribed:
                return False
            turn.text = cleaned
            turn.transcribed = True
            return True

    def add_model_response(self, text: str, tool_calls: list[ToolCall] | None = None) -> None:
        with self._lock:
            self._turns.append(
                Turn(role="model", text=text or "", tool_calls=list(tool_calls or []))
            )
            self._prune()

    def add_tool_result(self, name: str, result: Any, call_id: str | None = None) -> None:
        with self._lock:
            self._turns.append(
                Turn(role="tool", tool_name=name, tool_result=result, call_id=call_id)
            )
            self._prune()

    def pending_tool_calls(self) -> list[ToolCall]:
        """Chamadas ainda sem resposta, na ordem em que foram feitas.

        O pareamento é por `call_id` quando ele existe e, na falta dele, por
        ordem de chegada dentro do mesmo nome de tool. Parear só por nome
        quebraria quando a mesma tool é chamada duas vezes no mesmo turno —
        a segunda chamada ficaria eternamente "já respondida".
        """
        with self._lock:
            outstanding: list[ToolCall] = []
            for turn in self._turns:
                if turn.role == "model":
                    outstanding.extend(turn.tool_calls)
                elif turn.role == "tool":
                    self._match_and_remove(outstanding, turn)
            return outstanding

    @staticmethod
    def _match_and_remove(outstanding: list[ToolCall], result_turn: Turn) -> None:
        if result_turn.call_id:
            for index, call in enumerate(outstanding):
                if call.call_id == result_turn.call_id:
                    outstanding.pop(index)
                    return
        for index, call in enumerate(outstanding):
            if call.name == result_turn.tool_name:
                outstanding.pop(index)
                return

    def close_pending_tool_calls(self) -> int:
        """Fecha chamadas sem resposta com um resultado sintético (C10).

        Devolve quantas foram fechadas. Chamado antes de montar a requisição —
        não gasta requisição, é só consistência local do histórico.
        """
        with self._lock:
            pending = self.pending_tool_calls()
            for call in pending:
                self._turns.append(
                    Turn(
                        role="tool",
                        tool_name=call.name,
                        tool_result=dict(SYNTHETIC_RESULT),
                        call_id=call.call_id,
                        synthetic=True,
                    )
                )
            if pending:
                self._prune()
            return len(pending)

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()

    # ------------------------------------------------------------- leitura

    def __len__(self) -> int:
        with self._lock:
            return len(self._turns)

    def turns(self) -> list[Turn]:
        with self._lock:
            return list(self._turns)

    def _prune(self) -> None:
        """Mantém a janela, sem deixar resposta de tool órfã na frente.

        Um histórico que começa com um `function_response` sem o `function_call`
        correspondente é rejeitado pela API — então, ao cortar, avançamos até o
        primeiro turno que pode legitimamente abrir a conversa.
        """
        if len(self._turns) <= self.max_turns:
            return
        start = len(self._turns) - self.max_turns
        while start < len(self._turns) and self._turns[start].role == "tool":
            start += 1
        self._turns = self._turns[start:]
