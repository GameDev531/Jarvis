"""OpenRouter — o cérebro que decide e escreve (papel de RACIOCÍNIO).

É aqui que o James pensa: entender a tarefa, planejar, redigir e escolher quais
ferramentas chamar. O catálogo do OpenRouter dá acesso a modelos de texto bem
maiores que o Flash, e a cota é independente da do Gemini — que fica com o que
só ele faz (ouvir e ver).

Expectativa calibrada sobre a cota: o limite dos modelos `:free` é POR CONTA e
compartilhado entre todos eles — 50 requisições por dia sem crédito, 1.000 por
dia com US$ 10 vitalícios, e 20 por minuto em qualquer caso. A lista de modelos
protege contra o 429 momentâneo de um modelo específico; ela NÃO multiplica a
cota diária.

Limitação real: estes modelos recebem texto, não áudio nem imagem. Quem cuida
disso é o Gemini, nos papéis de percepção e visão.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

from james.llm.base import (
    LLMResponse,
    ProviderError,
    QuotaExceeded,
    TextCallback,
    ToolSchema,
    looks_like_quota_error,
)
from james.llm.history import Conversation, ToolCall
from james.logs import get_logger

logger = get_logger("james.llm.openrouter")

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_DONE = "[DONE]"


class OpenRouterProvider:
    name = "openrouter"
    accepts_audio = False
    accepts_image = False

    def __init__(
        self,
        api_key: str,
        models: list[str],
        system_prompt: str = "",
        timeout_s: int = 60,
        cooldown_s: int = 60,
        temperature: float = 0.7,
        max_output_tokens: int = 1200,
    ) -> None:
        if not api_key:
            raise ProviderError("OPENROUTER_API_KEY ausente.")
        if not models:
            raise ProviderError("Nenhum modelo configurado em llm.openrouter.models.")
        try:
            import httpx
        except ImportError as exc:
            raise ProviderError("Pacote 'httpx' não instalado.") from exc

        self._httpx = httpx
        self.api_key = api_key
        self.models = list(models)
        self.system_prompt = system_prompt
        self.timeout_s = int(timeout_s)
        self.cooldown_s = int(cooldown_s)
        self.temperature = float(temperature)
        self.max_output_tokens = int(max_output_tokens)
        # modelo -> instante (monotônico) em que sai do resfriamento
        self._cooldown: dict[str, float] = {}

    # --------------------------------------------------------------- seleção

    def _available_models(self) -> list[str]:
        now = time.monotonic()
        ready = [model for model in self.models if self._cooldown.get(model, 0.0) <= now]
        # Se todos estão em resfriamento, tentar o menos recente é melhor que
        # desistir sem tentar: o provedor pode ter liberado antes do previsto.
        return ready or sorted(self.models, key=lambda m: self._cooldown.get(m, 0.0))

    def _mark_cooldown(self, model: str) -> None:
        self._cooldown[model] = time.monotonic() + self.cooldown_s
        logger.info("Modelo %s em resfriamento por %ds.", model, self.cooldown_s)

    # ------------------------------------------------------------ requisição

    def generate(
        self,
        conversation: Conversation,
        audio_wav: bytes | None = None,
        text: str | None = None,
        instruction: str | None = None,
        tools: list[ToolSchema] | None = None,
        on_text: TextCallback | None = None,
    ) -> LLMResponse:
        if audio_wav is not None and not text:
            raise ProviderError(
                "OpenRouter recebe apenas texto. O áudio precisa passar antes pela "
                "etapa de percepção."
            )
        if not text:
            raise ProviderError("Nada a enviar: sem texto.")

        payload: dict[str, Any] = {
            "messages": self._build_messages(conversation, text, instruction),
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = [tool.to_openai() for tool in tools]

        last_error: Exception | None = None
        for model in self._available_models():
            try:
                return self._stream_one(dict(payload, model=model), model, on_text)
            except QuotaExceeded as exc:
                self._mark_cooldown(model)
                last_error = exc
            except ProviderError as exc:
                logger.warning("Modelo %s falhou: %s", model, exc)
                last_error = exc

        raise QuotaExceeded(
            f"Nenhum modelo do OpenRouter respondeu. Último erro: {last_error}"
        )

    def _stream_one(
        self, payload: dict[str, Any], model: str, on_text: TextCallback | None
    ) -> LLMResponse:
        """Uma tentativa em streaming.

        O streaming importa aqui porque este provedor virou o respondedor
        principal: sem ele, o James só começaria a falar depois da resposta
        inteira pronta, e o que o usuário percebe como demora é o tempo até a
        PRIMEIRA palavra.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "James Assistant",
        }

        collected: list[str] = []
        # Acumulador de tool calls por índice: no streaming, nome e argumentos
        # chegam picotados em vários fragmentos.
        partial_calls: dict[int, dict[str, Any]] = {}
        started = time.monotonic()
        first_token_at: float | None = None

        try:
            with self._httpx.Client(timeout=self.timeout_s) as client:
                with client.stream(
                    "POST", _ENDPOINT, headers=headers, json=payload
                ) as response:
                    if response.status_code == 429:
                        raise QuotaExceeded(f"429 do OpenRouter para {model}")
                    if response.status_code >= 400:
                        response.read()
                        detail = response.text[:300]
                        error = ProviderError(
                            f"OpenRouter devolveu {response.status_code}: {detail}"
                        )
                        if looks_like_quota_error(error):
                            raise QuotaExceeded(str(error)) from error
                        raise error

                    for event in _iter_sse(response.iter_lines()):
                        piece, calls_delta = _read_event(event)
                        if piece:
                            if first_token_at is None:
                                first_token_at = time.monotonic()
                            collected.append(piece)
                            if on_text is not None:
                                on_text(piece)
                        _merge_tool_calls(partial_calls, calls_delta)
        except (QuotaExceeded, ProviderError):
            raise
        except Exception as exc:  # noqa: BLE001 — httpx tem vários tipos de erro
            raise ProviderError(f"Erro de rede no OpenRouter: {exc}") from exc

        elapsed = time.monotonic() - started
        ttfb = (first_token_at - started) if first_token_at else elapsed
        tool_calls = _finalize_tool_calls(partial_calls)
        logger.info(
            "OpenRouter (%s) respondeu em %.2fs (primeira palavra em %.2fs, %d tool call(s))",
            model,
            elapsed,
            ttfb,
            len(tool_calls),
        )

        text = "".join(collected).strip()
        if not text and not tool_calls:
            raise ProviderError(f"{model} devolveu resposta vazia.")

        return LLMResponse(
            text=text, tool_calls=tool_calls, provider=self.name, model=model
        )

    # -------------------------------------------------------------- montagem

    def _build_messages(
        self,
        conversation: Conversation,
        current_text: str,
        instruction: str | None = None,
    ) -> list[dict]:
        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        for turn in conversation.turns():
            if turn.role == "user" and turn.text:
                messages.append({"role": "user", "content": turn.text})
            elif turn.role == "model":
                entry: dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
                if turn.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.call_id or f"call_{index}",
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.args or {}, ensure_ascii=False),
                            },
                        }
                        for index, call in enumerate(turn.tool_calls)
                    ]
                messages.append(entry)
            elif turn.role == "tool" and turn.tool_name:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": turn.call_id or turn.tool_name,
                        "name": turn.tool_name,
                        "content": json.dumps(turn.tool_result, ensure_ascii=False, default=str),
                    }
                )

        if instruction:
            # Orientação do turno como mensagem de sistema separada: substituir
            # o texto do usuário por ela perderia o comando.
            messages.append({"role": "system", "content": instruction})
        messages.append({"role": "user", "content": current_text})
        return messages


# ----------------------------------------------------------------- streaming

def _iter_sse(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Extrai os objetos JSON de um fluxo server-sent events.

    O OpenRouter intercala comentários (linhas iniciadas por ':') para manter a
    conexão viva; eles não são JSON e precisam ser ignorados.
    """
    for line in lines:
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == _DONE:
            return
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            logger.debug("Fragmento SSE ilegível ignorado: %r", payload[:120])
            continue
        if isinstance(parsed, dict):
            yield parsed


def _read_event(event: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    choices = event.get("choices") or []
    if not choices:
        return "", []
    delta = choices[0].get("delta") or {}
    return (delta.get("content") or ""), (delta.get("tool_calls") or [])


def _merge_tool_calls(
    accumulator: dict[int, dict[str, Any]], deltas: list[dict[str, Any]]
) -> None:
    """Junta os pedaços de uma tool call que chegam em fragmentos separados.

    O índice é a chave, não o nome: numa mesma resposta o modelo pode chamar a
    mesma ferramenta duas vezes com argumentos diferentes.
    """
    for delta in deltas:
        index = delta.get("index", 0)
        slot = accumulator.setdefault(index, {"id": None, "name": "", "arguments": ""})
        if delta.get("id"):
            slot["id"] = delta["id"]
        function = delta.get("function") or {}
        if function.get("name"):
            slot["name"] = function["name"]
        if function.get("arguments"):
            slot["arguments"] += function["arguments"]


def _finalize_tool_calls(accumulator: dict[int, dict[str, Any]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index in sorted(accumulator):
        slot = accumulator[index]
        name = slot.get("name")
        if not name:
            continue
        calls.append(
            ToolCall(name=name, args=_parse_arguments(slot.get("arguments")), call_id=slot.get("id"))
        )
    return calls


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Argumentos chegam como string JSON; modelo pequeno às vezes erra o JSON."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Argumentos de tool ilegíveis do OpenRouter: %r", str(raw)[:200])
        return {}
    return parsed if isinstance(parsed, dict) else {}
