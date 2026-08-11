"""Ponto ÚNICO de chamada de LLM do projeto.

Esta regra vem de uma falha real encontrada no estudo do Mark-XXXIX-OR: lá, as
ações periféricas passavam por um cliente com fallback, mas o planner — o
"cérebro" — chamava o Gemini direto. Resultado: a peça que mais precisava de
proteção era a única sem proteção, e o assistente inteiro travava quando a cota
estourava.

Aqui, TODA chamada de LLM passa por `LLMClient.respond`, sem exceção — inclusive
usos internos como resumo ou análise de erro, se um dia existirem. Se você
estiver escrevendo código novo que chama um SDK de modelo diretamente, ele está
no lugar errado.
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Callable

from james.config import Config, get_secret
from james.llm.base import (
    LLMResponse,
    NoProviderAvailable,
    ProviderError,
    QuotaExceeded,
    TextCallback,
    ToolSchema,
)
from james.llm.history import Conversation
from james.llm.rate_limiter import RateLimiter
from james.logs import audit, get_logger

logger = get_logger("james.llm.client")

# Espera aceitável pela janela por minuto. Acima disso, responder degradado é
# melhor que deixar o usuário no vácuo.
_MAX_INLINE_WAIT_S = 3.0


class ServiceState(Enum):
    OK = "ok"
    QUOTA = "quota"        # cota esgotada: só roteador local
    OFFLINE = "offline"    # sem rede: só roteador local

    @property
    def degraded(self) -> bool:
        return self is not ServiceState.OK


class LLMClient:
    def __init__(
        self,
        config: Config,
        system_prompt: str,
        tools: list[ToolSchema],
        rate_limiter: RateLimiter,
        transcribe_fallback: Callable[[], str | None] | None = None,
    ) -> None:
        self.config = config
        self.tools = tools
        self.rate_limiter = rate_limiter
        self.transcribe_fallback = transcribe_fallback
        self._state = ServiceState.OK
        self._lock = threading.Lock()

        self.primary = self._build_gemini(system_prompt)
        self.fallback = self._build_openrouter(system_prompt)

        if self.primary is None and self.fallback is None:
            logger.error("Nenhum provedor de LLM disponível — James inicia em modo degradado.")
            self._state = ServiceState.OFFLINE

    # ------------------------------------------------------------ construção

    def _build_gemini(self, system_prompt: str):
        api_key = get_secret("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY ausente — provedor principal desabilitado.")
            return None
        try:
            from james.llm.gemini_provider import GeminiProvider

            return GeminiProvider(
                api_key=api_key,
                model=str(self.config.get("llm.gemini.model", "gemini-2.5-flash")),
                system_prompt=system_prompt,
                thinking_budget=int(self.config.get("llm.gemini.thinking_budget", 0)),
                temperature=float(self.config.get("llm.gemini.temperature", 0.7)),
                max_output_tokens=int(self.config.get("llm.gemini.max_output_tokens", 800)),
                timeout_s=int(self.config.get("llm.gemini.timeout_s", 30)),
            )
        except ProviderError as exc:
            logger.error("Gemini indisponível: %s", exc)
            return None

    def _build_openrouter(self, system_prompt: str):
        if not self.config.get("llm.openrouter.enabled", True):
            return None
        api_key = get_secret("OPENROUTER_API_KEY")
        if not api_key:
            logger.info("OPENROUTER_API_KEY ausente — sem fallback secundário.")
            return None
        try:
            from james.llm.openrouter_provider import OpenRouterProvider

            return OpenRouterProvider(
                api_key=api_key,
                models=list(self.config.get("llm.openrouter.models", []) or []),
                system_prompt=system_prompt,
                timeout_s=int(self.config.get("llm.openrouter.timeout_s", 30)),
                cooldown_s=int(self.config.get("llm.openrouter.cooldown_s", 60)),
                temperature=float(self.config.get("llm.gemini.temperature", 0.7)),
                max_output_tokens=int(self.config.get("llm.gemini.max_output_tokens", 800)),
            )
        except ProviderError as exc:
            logger.error("OpenRouter indisponível: %s", exc)
            return None

    # ---------------------------------------------------------------- estado

    @property
    def state(self) -> ServiceState:
        with self._lock:
            return self._state

    def _set_state(self, state: ServiceState) -> bool:
        """Atualiza o estado. Devolve True se mudou (para anunciar só uma vez)."""
        with self._lock:
            if self._state is state:
                return False
            logger.info("Estado do serviço: %s -> %s", self._state.value, state.value)
            self._state = state
            return True

    @property
    def available(self) -> bool:
        return self.primary is not None or self.fallback is not None

    # ------------------------------------------------------------- requisição

    def respond(
        self,
        conversation: Conversation,
        audio_wav: bytes | None = None,
        text: str | None = None,
        instruction: str | None = None,
        on_text: TextCallback | None = None,
    ) -> LLMResponse:
        """Uma rodada de conversa. Levanta NoProviderAvailable no pior caso.

        `text` é o que o usuário disse; `instruction` é uma orientação do
        próprio James para este turno (por exemplo, cumprimentar). São campos
        separados de propósito: se a instrução ocupasse o lugar do texto, o
        caminho de fallback enviaria a orientação e perderia o comando.

        Fecha as tool calls pendentes antes de montar a requisição — é o que
        mantém o histórico consistente depois de um fire-and-forget (C10).
        """
        closed = conversation.close_pending_tool_calls()
        if closed:
            logger.debug("%d tool call(s) fechada(s) com resultado sintético.", closed)

        self._respect_rate_limit()

        errors: list[str] = []

        if self.primary is not None:
            try:
                response = self._call(
                    self.primary, conversation, audio_wav, text, instruction, on_text
                )
                self._set_state(ServiceState.OK)
                return response
            except QuotaExceeded as exc:
                # Um 429 já foi contado pelo provedor: não devolvemos a cota.
                errors.append(f"gemini: {exc}")
                logger.warning("Gemini sem cota, tentando fallback.")
            except ProviderError as exc:
                self.rate_limiter.refund()
                errors.append(f"gemini: {exc}")
                logger.warning("Gemini falhou (%s), tentando fallback.", exc)

        if self.fallback is not None:
            # O comando do usuário: o texto direto, ou a transcrição local do
            # áudio. A instrução do turno viaja separada e não o substitui.
            fallback_text = text or self._transcribe_for_fallback(audio_wav)
            if fallback_text:
                try:
                    response = self._call(
                        self.fallback, conversation, None, fallback_text, instruction, on_text
                    )
                    self._set_state(ServiceState.OK)
                    return response
                except QuotaExceeded as exc:
                    errors.append(f"openrouter: {exc}")
                except ProviderError as exc:
                    errors.append(f"openrouter: {exc}")
            else:
                errors.append(
                    "openrouter: sem transcrição local disponível para o áudio"
                )

        changed = self._set_state(
            ServiceState.QUOTA
            if any("cota" in item or "429" in item for item in errors)
            else ServiceState.OFFLINE
        )
        audit(
            "llm_indisponivel",
            erros=errors,
            estado=self.state.value,
            mudou_de_estado=changed,
        )
        raise NoProviderAvailable("; ".join(errors) or "nenhum provedor configurado")

    def _call(
        self,
        provider,
        conversation: Conversation,
        audio_wav: bytes | None,
        text: str | None,
        instruction: str | None,
        on_text: TextCallback | None,
    ) -> LLMResponse:
        return provider.generate(
            conversation=conversation,
            audio_wav=audio_wav,
            text=text,
            instruction=instruction,
            tools=self.tools,
            on_text=on_text,
        )

    def _transcribe_for_fallback(self, audio_wav: bytes | None) -> str | None:
        """O fallback só entende texto — aqui o STT local justifica sua existência."""
        if audio_wav is None or self.transcribe_fallback is None:
            return None
        try:
            return self.transcribe_fallback()
        except Exception as exc:  # noqa: BLE001 — transcrição é auxiliar, não crítica
            logger.error("Transcrição local para fallback falhou: %s", exc)
            return None

    def _respect_rate_limit(self) -> None:
        """Segura a requisição ou desiste, conforme o tipo de limite."""
        verdict = self.rate_limiter.acquire()
        if verdict.allowed:
            return

        if verdict.daily_exhausted:
            self._set_state(ServiceState.QUOTA)
            audit("cota_diaria_esgotada", usadas=verdict.daily_used, limite=verdict.daily_limit)
            raise NoProviderAvailable(verdict.reason)

        if verdict.retry_after_s <= _MAX_INLINE_WAIT_S:
            logger.info("Limite por minuto: aguardando %.1fs.", verdict.retry_after_s)
            time.sleep(verdict.retry_after_s)
            retry = self.rate_limiter.acquire()
            if retry.allowed:
                return

        audit("limite_por_minuto", espera_s=round(verdict.retry_after_s, 2))
        raise NoProviderAvailable(verdict.reason)
