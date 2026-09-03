"""Contrato ÚNICO de serialização do contexto para qualquer provedor de LLM.

## O bug que este arquivo elimina

O turno do usuário aparecia DUAS vezes em toda requisição de raciocínio:

    orchestrator._process_transcript
        self.conversation.add_user_text(transcript)      # (1) entra no histórico
        self._reason_turn(transcript)                    # (2) segue como texto atual

    openrouter_provider._build_messages
        for turn in conversation.turns(): ...            # emite (1)
        messages.append({"role": "user", "content": current_text})   # emite (2)

    gemini_provider._build_contents
        for turn in conversation.turns(): ...            # emite (1)
        contents.append(Content(role="user", parts=[... text ...]))  # emite (2)

O modelo recebia o comando repetido lado a lado. Custa tokens em todo turno,
e — pior — é ambíguo: "apaga o arquivo / apaga o arquivo" lido por um modelo
pequeno parece insistência, e insistência muda a resposta.

A causa raiz não é nenhuma das três linhas isoladamente. É que NÃO EXISTIA um
contrato dizendo quem é dono do turno atual: o orquestrador achava que era o
histórico, o provedor achava que era o parâmetro. Consertar um dos lados
deixaria o outro livre para regredir na próxima mudança.

## O contrato

    O `Conversation` guarda apenas turnos JÁ CONSOLIDADOS.
    O turno em andamento viaja separado, em `TurnoAtual`, e só é consolidado
    depois que a chamada ao provedor volta.

`build_llm_context` é o único lugar que transforma as duas coisas numa
sequência de mensagens. Gemini e OpenRouter consomem a MESMA lista lógica e só
traduzem para o formato de rede de cada um — nenhum dos dois decide mais o que
entra.

Ordem produzida, sempre a mesma:

    [sistema]        prompt de sistema, quando o provedor o manda no corpo
    [histórico]      turnos consolidados, na ordem
    [orientação]     instrução deste turno (não é fala do usuário)
    [atual]          o turno em andamento — texto e/ou áudio

## A rede de segurança

O contrato acima é estrutural, mas um chamador novo pode regredir. Por isso
`build_llm_context` também DETECTA a duplicação: se o último turno do histórico
já for exatamente o texto atual, ele não emite o texto de novo e registra um
aviso. A invariante "o texto atual aparece exatamente uma vez" passa a valer
mesmo quando alguém erra — e o aviso aponta para o erro em vez de escondê-lo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from james.llm.history import Conversation, ToolCall, Turn
from james.logs import get_logger

logger = get_logger("james.llm.contexto")

# Papéis lógicos. Não são os nomes de rede de nenhum provedor de propósito: a
# tradução é trabalho de quem serializa.
SISTEMA = "system"
USUARIO = "user"
MODELO = "model"
FERRAMENTA = "tool"

# De onde a mensagem veio. Serve para o provedor decidir a tradução (uma
# orientação vira `system` no OpenRouter e um prefixo de texto no Gemini) e
# para os testes afirmarem a invariante sem depender do formato de rede.
ORIGEM_SISTEMA = "sistema"
ORIGEM_HISTORICO = "historico"
ORIGEM_ORIENTACAO = "orientacao"
ORIGEM_ATUAL = "atual"


@dataclass(frozen=True)
class TurnoAtual:
    """O turno em andamento — ainda NÃO está no histórico.

    Texto e áudio convivem porque o Gemini aceita o áudio bruto na mesma
    requisição que responde: nesse caminho o texto pode nem existir.
    """

    text: str = ""
    audio_wav: bytes | None = None
    audio_mime: str = "audio/wav"

    @property
    def vazio(self) -> bool:
        return not (self.text or "").strip() and not self.audio_wav


@dataclass
class Mensagem:
    """Uma mensagem lógica, independente de provedor."""

    role: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_name: str | None = None
    tool_result: Any = None
    call_id: str | None = None
    audio_wav: bytes | None = None
    audio_mime: str = "audio/wav"
    origem: str = ORIGEM_HISTORICO


@dataclass
class LlmContext:
    """A sequência que vai para o modelo, mais o que se pode afirmar sobre ela."""

    mensagens: list[Mensagem] = field(default_factory=list)
    # True quando a rede de segurança precisou agir: o chamador consolidou o
    # turno atual no histórico ANTES da chamada. O contexto sai correto, mas
    # há um bug a montante.
    duplicacao_evitada: bool = False

    def __iter__(self) -> Iterator[Mensagem]:
        return iter(self.mensagens)

    def __len__(self) -> int:
        return len(self.mensagens)

    def por_origem(self, origem: str) -> list[Mensagem]:
        return [m for m in self.mensagens if m.origem == origem]

    def textos_de_usuario(self) -> list[str]:
        """Todo texto que o modelo vai ler como fala do usuário.

        É sobre esta lista que a invariante do turno único é verificada.
        """
        return [m.text for m in self.mensagens if m.role == USUARIO and m.text]

    def ocorrencias(self, texto: str) -> int:
        alvo = _normalizar(texto)
        if not alvo:
            return 0
        return sum(1 for t in self.textos_de_usuario() if _normalizar(t) == alvo)


def _normalizar(texto: str) -> str:
    return " ".join(str(texto or "").split())


def _do_historico(turn: Turn) -> Mensagem | None:
    """Traduz um turno consolidado. `None` quando não há o que enviar."""
    if turn.role == "user":
        if not turn.text:
            return None
        return Mensagem(role=USUARIO, text=turn.text, origem=ORIGEM_HISTORICO)

    if turn.role == "model":
        if not turn.text and not turn.tool_calls:
            return None
        return Mensagem(
            role=MODELO,
            text=turn.text or "",
            tool_calls=list(turn.tool_calls),
            origem=ORIGEM_HISTORICO,
        )

    if turn.role == "tool" and turn.tool_name:
        return Mensagem(
            role=FERRAMENTA,
            tool_name=turn.tool_name,
            tool_result=turn.tool_result,
            call_id=turn.call_id,
            origem=ORIGEM_HISTORICO,
        )

    return None


def build_llm_context(
    conversation: Conversation | None,
    current_turn: TurnoAtual | str | None = None,
    *,
    instruction: str | None = None,
    system_prompt: str | None = None,
) -> LlmContext:
    """Monta a sequência lógica que os provedores traduzem para a rede.

    `current_turn` aceita string por conveniência dos chamadores antigos; o
    tipo canônico é `TurnoAtual`.
    """
    if isinstance(current_turn, str):
        current_turn = TurnoAtual(text=current_turn)

    contexto = LlmContext()

    if system_prompt:
        contexto.mensagens.append(
            Mensagem(role=SISTEMA, text=system_prompt, origem=ORIGEM_SISTEMA)
        )

    turnos = list(conversation.turns()) if conversation is not None else []
    for turn in turnos:
        mensagem = _do_historico(turn)
        if mensagem is not None:
            contexto.mensagens.append(mensagem)

    if instruction:
        contexto.mensagens.append(
            Mensagem(role=SISTEMA, text=instruction, origem=ORIGEM_ORIENTACAO)
        )

    if current_turn is None or current_turn.vazio:
        return contexto

    texto_atual = current_turn.text or ""
    if texto_atual and _ja_consolidado(turnos, texto_atual):
        # Rede de segurança: alguém adicionou o turno ao histórico antes da
        # chamada. O histórico já carrega o texto — emiti-lo de novo é
        # exatamente o bug que este módulo existe para impedir.
        logger.warning(
            "Turno atual já estava consolidado no histórico; não vou repeti-lo. "
            "Quem chamou deve consolidar SÓ DEPOIS da requisição."
        )
        contexto.duplicacao_evitada = True
        texto_atual = ""

    if not texto_atual and current_turn.audio_wav is None:
        return contexto

    contexto.mensagens.append(
        Mensagem(
            role=USUARIO,
            text=texto_atual,
            audio_wav=current_turn.audio_wav,
            audio_mime=current_turn.audio_mime,
            origem=ORIGEM_ATUAL,
        )
    )
    return contexto


def _ja_consolidado(turnos: list[Turn], texto: str) -> bool:
    """O último turno de usuário do histórico já é este texto?

    Olha o ÚLTIMO turno de usuário, não qualquer um: repetir um comando duas
    vezes na mesma sessão ("de novo", "de novo") é legítimo, e apagar a segunda
    ocorrência apagaria informação verdadeira. O que nunca é legítimo é o
    histórico terminar no mesmo texto que está sendo enviado como turno atual.
    """
    alvo = _normalizar(texto)
    if not alvo:
        return False
    for turn in reversed(turnos):
        if turn.role == "user":
            return _normalizar(turn.text) == alvo
        if turn.role in ("model", "tool"):
            # Já houve resposta depois do último comando: o texto atual é novo.
            return False
    return False
