"""Gemini — provedor principal, com áudio direto no caminho comum (C5).

O ganho central: o Gemini 2.5 Flash é multimodal e aceita o áudio bruto na
mesma requisição que devolve a resposta ou a chamada de tool. Isso remove o STT
local do caminho crítico (que, numa CPU sem AVX, custa vários segundos) sem
gastar requisição extra — é a mesma requisição que a versão em texto gastaria.

Duas configurações que definem a latência percebida:
  - `thinking_budget=0`: o modo "thinking" vem ligado por padrão e cobra tempo
    em todo turno; num assistente de voz ele não compensa;
  - streaming: o texto é entregue em pedaços, então o Piper começa a sintetizar
    a primeira sentença enquanto o resto ainda chega.
"""

from __future__ import annotations

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

logger = get_logger("james.llm.gemini")

# Um modelo aposentado devolve 404 para sempre. Retentá-lo a cada requisição
# custaria uma viagem de rede por vez — a mesma correção já feita no OpenRouter.
_MODELO_MORTO_S = 24 * 60 * 60

_SINAIS_DE_MODELO_MORTO = (
    "not found",
    "not supported",
    "no longer available",
    "is not available",
    "404",
)


def _parece_modelo_inexistente(exc: Exception) -> bool:
    texto = str(exc).lower()
    return any(sinal in texto for sinal in _SINAIS_DE_MODELO_MORTO)


class GeminiProvider:
    name = "gemini"
    accepts_audio = True
    accepts_image = True

    def __init__(
        self,
        api_key: str,
        model: "str | list[str]" = "gemini-3.5-flash",
        system_prompt: str = "",
        thinking_budget: int = 0,
        temperature: float = 0.7,
        max_output_tokens: int = 800,
        timeout_s: int = 30,
    ) -> None:
        if not api_key:
            raise ProviderError("GEMINI_API_KEY ausente.")
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderError(
                "Pacote 'google-genai' não instalado (pip install google-genai)."
            ) from exc

        self._genai = genai
        # Uma LISTA, não um ID, e a razão é cicatriz: o `gemini-2.5-flash`
        # fixo aqui virou 404 quando o Google o aposentou, e como a percepção
        # de áudio só tinha o Gemini, o caminho de voz inteiro parou. A mesma
        # coisa já tinha acontecido com o catálogo :free do OpenRouter.
        #
        # Um ID de modelo é uma dependência que se move sozinha. Errar isso
        # duas vezes é o que transforma um conserto pontual numa cadeia.
        self.models = [model] if isinstance(model, str) else [str(m) for m in model]
        if not self.models:
            raise ProviderError("Nenhum modelo do Gemini configurado.")
        # modelo -> instante em que sai do descarte (404 é permanente)
        self._mortos: dict[str, float] = {}
        self.system_prompt = system_prompt
        self.thinking_budget = int(thinking_budget)
        self.temperature = float(temperature)
        self.max_output_tokens = int(max_output_tokens)
        self.timeout_ms = max(1000, int(timeout_s) * 1000)

        try:
            from google.genai import types

            self._types = types
            self._client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=self.timeout_ms),
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Falha ao inicializar o cliente Gemini: {exc}") from exc

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
        """Uma rodada. `audio_wav` e `text` são as duas formas de entrada."""
        if audio_wav is None and not text and not instruction:
            raise ProviderError("Nada a enviar: sem áudio e sem texto.")

        contents = self._build_contents(conversation, audio_wav, text, instruction)
        config = self._build_config(tools)
        started = time.monotonic()

        collected_text: list[str] = []
        tool_calls: list[ToolCall] = []
        first_chunk_at: float | None = None

        # O corpo inteiro por modelo, e não só a criação do stream: o SDK
        # monta o gerador de forma preguiçosa, então um modelo aposentado
        # levanta 404 na PRIMEIRA ITERAÇÃO, não na chamada. Envolver só a
        # criação deixaria o erro escapar por fora da troca de modelo.
        def _rodar(nome: str):
            nonlocal first_chunk_at
            collected_text.clear()
            tool_calls.clear()
            first_chunk_at = None
            try:
                stream = self._client.models.generate_content_stream(
                    model=nome, contents=contents, config=config
                )
                for chunk in stream:
                    if first_chunk_at is None:
                        first_chunk_at = time.monotonic()
                    piece, calls = self._read_chunk(chunk)
                    if piece:
                        collected_text.append(piece)
                        if on_text is not None:
                            on_text(piece)
                    tool_calls.extend(calls)
            except Exception as exc:  # noqa: BLE001 — o SDK levanta tipos variados
                if looks_like_quota_error(exc):
                    raise QuotaExceeded(f"Gemini recusou por cota: {exc}") from exc
                raise ProviderError(f"Falha na chamada ao Gemini: {exc}") from exc

        self._com_modelos(_rodar)

        elapsed = time.monotonic() - started
        ttfb = (first_chunk_at - started) if first_chunk_at else elapsed
        logger.info(
            "Gemini respondeu em %.2fs (primeiro fragmento em %.2fs, %d tool call(s))",
            elapsed,
            ttfb,
            len(tool_calls),
        )

        return LLMResponse(
            text="".join(collected_text).strip(),
            tool_calls=tool_calls,
            provider=self.name,
            model=self.model,
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
        """Papel de VISÃO — analisa uma captura de tela ou foto da câmera."""
        if not image:
            raise ProviderError("Nenhuma imagem para analisar.")

        types = self._types
        parts = [types.Part.from_text(text=text)]
        parts.append(types.Part.from_bytes(data=image, mime_type=mime_type))
        contents = [types.Content(role="user", parts=parts)]

        started = time.monotonic()
        collected: list[str] = []
        def _rodar(nome: str):
            collected.clear()
            try:
                stream = self._client.models.generate_content_stream(
                    model=nome, contents=contents, config=self._build_config(tools)
                )
                for chunk in stream:
                    piece, _ = self._read_chunk(chunk)
                    if piece:
                        collected.append(piece)
                        if on_text is not None:
                            on_text(piece)
            except Exception as exc:  # noqa: BLE001 — o SDK levanta tipos variados
                if looks_like_quota_error(exc):
                    raise QuotaExceeded(f"Gemini recusou por cota: {exc}") from exc
                raise ProviderError(f"Falha ao analisar imagem: {exc}") from exc

        self._com_modelos(_rodar)

        logger.info(
            "Gemini analisou imagem de %d KB em %.2fs",
            len(image) // 1024,
            time.monotonic() - started,
        )
        return LLMResponse(
            text="".join(collected).strip(), provider=self.name, model=self.model
        )

    def _read_chunk(self, chunk: Any) -> tuple[str, list[ToolCall]]:
        """Extrai texto e chamadas de tool de um fragmento do stream.

        Lê as `parts` diretamente em vez de usar `chunk.text`: o atalho levanta
        exceção quando o fragmento contém apenas uma chamada de função.
        """
        text_parts: list[str] = []
        calls: list[ToolCall] = []

        candidates = getattr(chunk, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    text_parts.append(part_text)
                function_call = getattr(part, "function_call", None)
                if function_call is not None and getattr(function_call, "name", None):
                    calls.append(
                        ToolCall(
                            name=function_call.name,
                            args=dict(getattr(function_call, "args", None) or {}),
                            call_id=getattr(function_call, "id", None),
                        )
                    )
        return "".join(text_parts), calls

    # -------------------------------------------------------------- montagem

    @property
    def model(self) -> str:
        """O primeiro modelo ainda vivo. Se todos caíram, o primeiro da lista.

        Voltar ao primeiro quando todos falharam é deliberado: um erro
        transitório não pode aposentar a lista inteira para sempre.
        """
        agora = time.monotonic()
        for nome in self.models:
            if self._mortos.get(nome, 0.0) <= agora:
                return nome
        return self.models[0]

    def _descartar(self, nome: str, motivo: str) -> None:
        """404 é permanente: o ID saiu do catálogo do Google."""
        self._mortos[nome] = time.monotonic() + _MODELO_MORTO_S
        logger.error(
            "Modelo %s não existe mais no Gemini (%s). Fora de uso nesta sessão "
            "— rode `python check_modelos.py` e atualize o config.yaml.",
            nome, motivo,
        )

    def _com_modelos(self, chamada):
        """Roda `chamada(modelo)` descendo a lista até um modelo responder."""
        ultimo: Exception | None = None
        for nome in self.models:
            if self._mortos.get(nome, 0.0) > time.monotonic():
                continue
            try:
                return chamada(nome)
            except QuotaExceeded:
                raise                      # cota é do provedor, não do modelo
            except ProviderError as exc:
                if _parece_modelo_inexistente(exc):
                    self._descartar(nome, str(exc)[:120])
                    ultimo = exc
                    continue
                raise
        raise ProviderError(
            f"Nenhum modelo do Gemini respondeu. Último erro: {ultimo}"
        )

    def _build_config(self, tools: list[ToolSchema] | None) -> Any:
        types = self._types
        kwargs: dict[str, Any] = {
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
        }
        if self.system_prompt:
            kwargs["system_instruction"] = self.system_prompt

        # thinking_budget=0 desliga o raciocínio interno. Modelos sem suporte a
        # esse campo rejeitam a requisição inteira, então a configuração é
        # aplicada de forma tolerante.
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=self.thinking_budget
            )
        except (AttributeError, TypeError) as exc:
            logger.debug("ThinkingConfig indisponível nesta versão do SDK: %s", exc)

        if tools:
            kwargs["tools"] = [
                types.Tool(function_declarations=[tool.to_gemini() for tool in tools])
            ]
            # O James executa as tools ele mesmo, passando pelo guard. A execução
            # automática do SDK pularia essa camada inteira.
            try:
                kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
                    disable=True
                )
            except (AttributeError, TypeError) as exc:
                logger.debug("AutomaticFunctionCallingConfig indisponível: %s", exc)

        return types.GenerateContentConfig(**kwargs)

    def _build_contents(
        self,
        conversation: Conversation,
        audio_wav: bytes | None,
        text: str | None,
        instruction: str | None = None,
    ) -> list[Any]:
        types = self._types
        contents: list[Any] = []

        for turn in conversation.turns():
            if turn.role == "user":
                if turn.text:
                    contents.append(
                        types.Content(role="user", parts=[types.Part.from_text(text=turn.text)])
                    )
            elif turn.role == "model":
                parts = []
                if turn.text:
                    parts.append(types.Part.from_text(text=turn.text))
                for call in turn.tool_calls:
                    parts.append(
                        types.Part.from_function_call(name=call.name, args=call.args or {})
                    )
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
            elif turn.role == "tool" and turn.tool_name:
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=turn.tool_name,
                                response=_as_response_dict(turn.tool_result),
                            )
                        ],
                    )
                )

        current_parts = []
        if instruction:
            # Orientação do próprio James para este turno (ex: cumprimentar).
            # Vem antes do comando para o modelo tratá-la como enquadramento.
            current_parts.append(
                types.Part.from_text(text=f"[orientação para esta resposta] {instruction}")
            )
        if text:
            current_parts.append(types.Part.from_text(text=text))
        if audio_wav:
            current_parts.append(
                types.Part.from_bytes(data=audio_wav, mime_type="audio/wav")
            )
        contents.append(types.Content(role="user", parts=current_parts))
        return contents


def _as_response_dict(result: Any) -> dict[str, Any]:
    """A API exige um objeto no function_response; escalares vão embrulhados."""
    if isinstance(result, dict):
        return result
    return {"result": result if result is not None else ""}
