"""OpenRouter — fallback quando o Gemini recusa por cota.

Expectativa calibrada (correção C1, confirmada na documentação): o limite dos
modelos `:free` é POR CONTA e compartilhado entre todos eles — 50 requisições
por dia sem crédito, 1.000 por dia com US$ 10 vitalícios, e 20 por minuto em
qualquer caso. Ou seja: a lista de modelos protege contra o 429 momentâneo de
um provedor específico, mas NÃO multiplica a cota diária. Isso é uma rede de
segurança, não uma fonte inesgotável.

Limitação real: estes modelos recebem texto, não áudio. Quando o fallback
entra, o comando precisa ter sido transcrito localmente pelo whisper.cpp — é
por isso que o STT local continua no projeto mesmo fora do caminho comum.
"""

from __future__ import annotations

import json
import time
from typing import Any

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


class OpenRouterProvider:
    name = "openrouter"

    def __init__(
        self,
        api_key: str,
        models: list[str],
        system_prompt: str = "",
        timeout_s: int = 30,
        cooldown_s: int = 60,
        temperature: float = 0.7,
        max_output_tokens: int = 800,
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
        if not text:
            raise ProviderError(
                "OpenRouter recebe apenas texto. Sem transcrição local disponível, "
                "este fallback não pode ser usado."
            )

        messages = self._build_messages(conversation, text, instruction)
        payload_base: dict[str, Any] = {
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }
        if tools:
            payload_base["tools"] = [tool.to_openai() for tool in tools]

        last_error: Exception | None = None
        for model in self._available_models():
            payload = dict(payload_base, model=model)
            try:
                response = self._post(payload)
            except QuotaExceeded as exc:
                self._mark_cooldown(model)
                last_error = exc
                continue
            except ProviderError as exc:
                last_error = exc
                continue

            parsed = self._parse(response, model)
            if on_text is not None and parsed.text:
                # A resposta chega inteira; entregamos de uma vez para manter o
                # mesmo contrato do caminho em streaming.
                on_text(parsed.text)
            return parsed

        raise QuotaExceeded(
            f"Nenhum modelo do OpenRouter respondeu. Último erro: {last_error}"
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # Identificação recomendada pelo OpenRouter para apps próprios.
            "X-Title": "James Assistant",
        }
        try:
            with self._httpx.Client(timeout=self.timeout_s) as client:
                response = client.post(_ENDPOINT, headers=headers, json=payload)
        except Exception as exc:  # noqa: BLE001 — httpx tem vários tipos de erro
            raise ProviderError(f"Erro de rede no OpenRouter: {exc}") from exc

        if response.status_code == 429:
            raise QuotaExceeded(f"429 do OpenRouter para {payload.get('model')}")
        if response.status_code >= 400:
            detail = response.text[:300]
            error = ProviderError(
                f"OpenRouter devolveu {response.status_code}: {detail}"
            )
            if looks_like_quota_error(error):
                raise QuotaExceeded(str(error)) from error
            raise error

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError(f"Resposta ilegível do OpenRouter: {exc}") from exc

    def _parse(self, data: dict[str, Any], model: str) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("OpenRouter devolveu resposta sem 'choices'.")
        message = choices[0].get("message") or {}

        tool_calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            name = function.get("name")
            if not name:
                continue
            arguments = function.get("arguments")
            tool_calls.append(
                ToolCall(name=name, args=_parse_arguments(arguments), call_id=raw.get("id"))
            )

        return LLMResponse(
            text=(message.get("content") or "").strip(),
            tool_calls=tool_calls,
            provider=self.name,
            model=model,
        )

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


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Argumentos chegam como string JSON; modelo pequeno às vezes erra o JSON."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Argumentos de tool ilegíveis do OpenRouter: %r", raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}
