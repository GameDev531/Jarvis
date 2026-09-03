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

Sobre imagem: parte do catálogo `:free` enxerga imagem, e esses modelos ficam
em `vision_models` — o que permite à visão ter um segundo caminho em vez de
depender só do Gemini. Áudio, não: a percepção continua sendo do Gemini, e o
comando falado precisa passar por ela antes de chegar aqui.
"""

from __future__ import annotations

import base64
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
from james.llm.message_builder import (
    FERRAMENTA,
    MODELO,
    SISTEMA,
    USUARIO,
    LlmContext,
    TurnoAtual,
    build_llm_context,
)
from james.logs import get_logger

logger = get_logger("james.llm.openrouter")

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_DONE = "[DONE]"

# Um modelo removido do catálogo devolve 404 e vai devolver 404 para sempre.
# Sem isto ele seria retentado a cada requisição, custando uma viagem de rede
# inteira antes de chegar ao primeiro modelo vivo — e o catálogo `:free` do
# OpenRouter muda de mês para mês, então isso não é hipótese.
_MODELO_INEXISTENTE_S = 24 * 60 * 60


class ModeloInexistente(ProviderError):
    """404: o ID saiu do catálogo. Continua sendo `ProviderError` de propósito.

    Quem chama e não conhece esta classe segue tratando como falha comum e
    passa para o próximo modelo — o comportamento correto de qualquer jeito.
    """


class OpenRouterProvider:
    name = "openrouter"
    accepts_audio = False
    # Aceita imagem quando há modelos de visão configurados. Vários modelos
    # `:free` do catálogo enxergam imagem — o que torna a visão resiliente em
    # vez de depender só do Gemini.
    accepts_image = True

    def __init__(
        self,
        api_key: str,
        models: list[str],
        system_prompt: str = "",
        timeout_s: int = 60,
        cooldown_s: int = 60,
        temperature: float = 0.7,
        max_output_tokens: int = 1200,
        vision_models: list[str] | None = None,
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
        self.vision_models = list(vision_models or [])
        # modelo -> instante (monotônico) em que sai do resfriamento
        self._cooldown: dict[str, float] = {}

    # --------------------------------------------------------------- seleção

    def _available_from(self, models: list[str]) -> list[str]:
        now = time.monotonic()
        ready = [model for model in models if self._cooldown.get(model, 0.0) <= now]
        # Se todos estão em resfriamento, tentar o menos recente é melhor que
        # desistir sem tentar: o provedor pode ter liberado antes do previsto.
        return ready or sorted(models, key=lambda m: self._cooldown.get(m, 0.0))

    def _available_models(self) -> list[str]:
        return self._available_from(self.models)

    def _mark_cooldown(self, model: str, seconds: float | None = None) -> None:
        espera = self.cooldown_s if seconds is None else float(seconds)
        self._cooldown[model] = time.monotonic() + espera
        logger.info("Modelo %s em resfriamento por %.0fs.", model, espera)

    def _descartar(self, model: str, motivo: str) -> None:
        """Tira de circulação um modelo que não existe mais no catálogo.

        Diferente do 429, que é temporário, um 404 é permanente: o ID saiu do
        catálogo. Retentá-lo a cada requisição gastaria uma viagem de rede
        inteira por vez — e numa conexão de ~2 s isso sozinho estoura a meta de
        3,5 s do caminho de voz antes mesmo de um modelo vivo ser consultado.
        """
        self._cooldown[model] = time.monotonic() + _MODELO_INEXISTENTE_S
        logger.error(
            "Modelo %s não existe no catálogo do OpenRouter (%s). Fora de uso "
            "nesta sessão — remova do config.yaml.",
            model, motivo,
        )

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
        # Uma orientação sozinha é entrada legítima: é assim que a segunda
        # rodada pede o relato do resultado sem reenviar o comando original,
        # que já está consolidado no histórico.
        if not text and not instruction:
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
            except ModeloInexistente as exc:
                self._descartar(model, str(exc))
                last_error = exc
            except ProviderError as exc:
                logger.warning("Modelo %s falhou: %s", model, exc)
                last_error = exc

        raise QuotaExceeded(
            f"Nenhum modelo do OpenRouter respondeu. Último erro: {last_error}"
        )

    def generate_with_image(
        self,
        conversation: Conversation,
        image: bytes,
        mime_type: str = "image/png",
        text: str = "Descreva o que aparece nesta imagem.",
        instruction: str | None = None,
        tools: list[ToolSchema] | None = None,
        on_text: TextCallback | None = None,
    ) -> LLMResponse:
        """Papel de VISÃO pelo OpenRouter.

        A imagem viaja embutida como data URL, no formato multimodal da API de
        chat. Só os modelos listados em `vision_models` enxergam imagem: mandar
        para um modelo de texto devolve erro ou, pior, uma alucinação sobre uma
        imagem que ele não viu.
        """
        if not image:
            raise ProviderError("Nenhuma imagem para analisar.")
        if not self.vision_models:
            raise ProviderError(
                "Nenhum modelo de visão configurado em llm.openrouter.vision_models."
            )

        data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
        mensagens: list[dict[str, Any]] = []
        if self.system_prompt:
            mensagens.append({"role": "system", "content": self.system_prompt})
        if instruction:
            mensagens.append({"role": "system", "content": instruction})
        mensagens.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        )

        payload_base: dict[str, Any] = {
            "messages": mensagens,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "stream": True,
        }

        last_error: Exception | None = None
        for model in self._available_from(self.vision_models):
            try:
                return self._stream_one(dict(payload_base, model=model), model, on_text)
            except QuotaExceeded as exc:
                self._mark_cooldown(model)
                last_error = exc
            except ModeloInexistente as exc:
                self._descartar(model, str(exc))
                last_error = exc
            except ProviderError as exc:
                logger.warning("Modelo de visão %s falhou: %s", model, exc)
                last_error = exc

        raise QuotaExceeded(
            f"Nenhum modelo de visão do OpenRouter respondeu. Último erro: {last_error}"
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
                        if response.status_code == 404:
                            raise ModeloInexistente(str(error)) from error
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
        conversation: Conversation | None,
        current_text: str | TurnoAtual | None = None,
        instruction: str | None = None,
    ) -> list[dict]:
        """Traduz o contexto lógico para o formato de chat da OpenAI.

        Quem decide O QUE entra é `build_llm_context`; aqui só se traduz. Foi
        a decisão duplicada ("o histórico manda o comando E eu mando de novo")
        que fazia o turno do usuário aparecer duas vezes.
        """
        contexto = build_llm_context(
            conversation,
            current_text,
            instruction=instruction,
            system_prompt=self.system_prompt,
        )
        return self._serializar(contexto)

    @staticmethod
    def _serializar(contexto: LlmContext) -> list[dict]:
        messages: list[dict[str, Any]] = []
        for mensagem in contexto:
            if mensagem.role == SISTEMA:
                messages.append({"role": "system", "content": mensagem.text})
            elif mensagem.role == USUARIO:
                if mensagem.audio_wav is not None:
                    raise ProviderError(
                        "OpenRouter recebe apenas texto. O áudio precisa passar antes "
                        "pela etapa de percepção."
                    )
                messages.append({"role": "user", "content": mensagem.text})
            elif mensagem.role == MODELO:
                entry: dict[str, Any] = {"role": "assistant", "content": mensagem.text or ""}
                if mensagem.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.call_id or f"call_{index}",
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.args or {}, ensure_ascii=False),
                            },
                        }
                        for index, call in enumerate(mensagem.tool_calls)
                    ]
                messages.append(entry)
            elif mensagem.role == FERRAMENTA and mensagem.tool_name:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": mensagem.call_id or mensagem.tool_name,
                        "name": mensagem.tool_name,
                        "content": json.dumps(
                            mensagem.tool_result, ensure_ascii=False, default=str
                        ),
                    }
                )
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
