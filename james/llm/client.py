"""Ponto ÚNICO de chamada de LLM do projeto, com roteamento por papel.

Esta regra vem de uma falha real encontrada no estudo do Mark-XXXIX-OR: lá, as
ações periféricas passavam por um cliente com fallback, mas o planner — o
"cérebro" — chamava o Gemini direto. Resultado: a peça que mais precisava de
proteção era a única sem proteção, e o assistente inteiro travava quando a cota
estourava.

Aqui, TODA chamada de LLM passa por este cliente. Se você estiver escrevendo
código novo que chama um SDK de modelo diretamente, ele está no lugar errado.

Cada papel (ver roles.py) tem sua própria cadeia de provedores e cada provedor
tem seu próprio controle de cota — as cotas do Gemini e do OpenRouter são
independentes, e tratá-las como um balde só desperdiçaria metade da capacidade
diária do James.
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Any

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
from james.llm.roles import (
    DEFAULT_CHAINS,
    NO_SPEECH_MARKER,
    TRANSCRIPTION_INSTRUCTION,
    Role,
)
from james.logs import audit, get_logger

logger = get_logger("james.llm.client")

# Espera aceitável pela janela por minuto. Acima disso, responder degradado é
# melhor que deixar o usuário no vácuo.
_MAX_INLINE_WAIT_S = 3.0

# Limites padrão por provedor. O do OpenRouter é conservador de propósito: a
# cota dos modelos :free é por CONTA e compartilhada entre todos eles — 50/dia
# sem crédito, 1.000/dia com US$ 10 vitalícios, 20/min sempre.
_DEFAULT_LIMITS = {
    "gemini": {"requests_per_minute": 10, "requests_per_day": 240},
    "openrouter": {"requests_per_minute": 15, "requests_per_day": 45},
}


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
        state_dir: Any = None,
    ) -> None:
        self.config = config
        self.tools = tools
        self.system_prompt = system_prompt
        self._state = ServiceState.OK
        self._lock = threading.Lock()

        self.providers: dict[str, Any] = {}
        self.limiters: dict[str, RateLimiter] = {}
        self._state_dir = state_dir or (config.root / "state")

        self._build_providers()
        self.chains = self._build_chains()

        if not self.providers:
            logger.error("Nenhum provedor de LLM disponível — James inicia em modo degradado.")
            self._state = ServiceState.OFFLINE

    # ------------------------------------------------------------ construção

    def _limiter_for(self, name: str) -> RateLimiter:
        defaults = _DEFAULT_LIMITS.get(name, {"requests_per_minute": 10, "requests_per_day": 100})
        section = self.config.section(f"llm.rate_limit.{name}") or {}
        return RateLimiter(
            requests_per_minute=int(
                section.get("requests_per_minute", defaults["requests_per_minute"])
            ),
            requests_per_day=int(section.get("requests_per_day", defaults["requests_per_day"])),
            state_path=self._state_dir / f"usage_{name}.json",
        )

    def _build_providers(self) -> None:
        gemini = self._build_gemini()
        if gemini is not None:
            self.providers["gemini"] = gemini
            self.limiters["gemini"] = self._limiter_for("gemini")

        openrouter = self._build_openrouter()
        if openrouter is not None:
            self.providers["openrouter"] = openrouter
            self.limiters["openrouter"] = self._limiter_for("openrouter")

    def _build_gemini(self):
        api_key = get_secret("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY ausente — sem percepção nem visão na nuvem.")
            return None
        try:
            from james.llm.gemini_provider import GeminiProvider

            return GeminiProvider(
                api_key=api_key,
                model=str(self.config.get("llm.gemini.model", "gemini-2.5-flash")),
                system_prompt=self.system_prompt,
                thinking_budget=int(self.config.get("llm.gemini.thinking_budget", 0)),
                temperature=float(self.config.get("llm.gemini.temperature", 0.7)),
                max_output_tokens=int(self.config.get("llm.gemini.max_output_tokens", 800)),
                timeout_s=int(self.config.get("llm.gemini.timeout_s", 30)),
            )
        except ProviderError as exc:
            logger.error("Gemini indisponível: %s", exc)
            return None

    def _build_openrouter(self):
        if not self.config.get("llm.openrouter.enabled", True):
            return None
        api_key = get_secret("OPENROUTER_API_KEY")
        if not api_key:
            logger.warning(
                "OPENROUTER_API_KEY ausente — o raciocínio cairá para o Gemini."
            )
            return None
        try:
            from james.llm.openrouter_provider import OpenRouterProvider

            return OpenRouterProvider(
                api_key=api_key,
                models=list(self.config.get("llm.openrouter.models", []) or []),
                system_prompt=self.system_prompt,
                timeout_s=int(self.config.get("llm.openrouter.timeout_s", 60)),
                cooldown_s=int(self.config.get("llm.openrouter.cooldown_s", 60)),
                temperature=float(self.config.get("llm.openrouter.temperature", 0.7)),
                max_output_tokens=int(
                    self.config.get("llm.openrouter.max_output_tokens", 1200)
                ),
                vision_models=list(
                    self.config.get("llm.openrouter.vision_models", []) or []
                ),
            )
        except ProviderError as exc:
            logger.error("OpenRouter indisponível: %s", exc)
            return None

    def _build_chains(self) -> dict[Role, list[str]]:
        chains: dict[Role, list[str]] = {}
        for role in Role:
            configured = self.config.get(f"llm.roles.{role.value}", None)
            names = list(configured) if configured else list(DEFAULT_CHAINS[role])
            available = [name for name in names if name in self.providers]

            missing = [name for name in names if name not in self.providers]
            if missing:
                logger.info(
                    "Papel '%s': %s indisponível(is), usando %s",
                    role.value,
                    ", ".join(missing),
                    ", ".join(available) or "nenhum",
                )
            chains[role] = available
        return chains

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
        return bool(self.providers)

    def can(self, role: Role) -> bool:
        return bool(self.chains.get(role))

    def quota_summary(self) -> dict[str, int]:
        return {name: limiter.daily_remaining for name, limiter in self.limiters.items()}

    # ------------------------------------------------------------ papéis

    def transcribe(self, audio_wav: bytes) -> str | None:
        """PERCEPÇÃO — áudio para texto. None quando não há fala utilizável.

        Devolve só a transcrição: a decisão do que fazer com ela é da etapa de
        raciocínio. Manter as duas separadas é o que permite ao roteador local
        interceptar comandos frequentes antes de gastar a requisição cara.
        """
        if not audio_wav:
            return None
        try:
            response = self._call_chain(
                Role.PERCEPTION,
                conversation=Conversation(max_turns=2),
                audio_wav=audio_wav,
                instruction=TRANSCRIPTION_INSTRUCTION,
                tools=None,
            )
        except NoProviderAvailable as exc:
            logger.info("Percepção na nuvem indisponível: %s", exc)
            return None

        text = (response.text or "").strip().strip('"').strip()
        if not text or text.lower().startswith(NO_SPEECH_MARKER.lower()):
            return None
        return text

    def reason(
        self,
        conversation: Conversation,
        text: str,
        instruction: str | None = None,
        on_text: TextCallback | None = None,
    ) -> LLMResponse:
        """RACIOCÍNIO — decide, responde e chama ferramentas.

        Fecha as tool calls pendentes antes de montar a requisição: é o que
        mantém o histórico consistente depois de um fire-and-forget (C10).
        """
        closed = conversation.close_pending_tool_calls()
        if closed:
            logger.debug("%d tool call(s) fechada(s) com resultado sintético.", closed)

        return self._call_chain(
            Role.REASONING,
            conversation=conversation,
            text=text,
            instruction=instruction,
            tools=self.tools,
            on_text=on_text,
        )

    def describe_image(
        self,
        image: bytes,
        mime_type: str = "image/png",
        question: str = "Descreva o que aparece nesta imagem.",
    ) -> str:
        """VISÃO — descreve uma imagem (tela ou câmera).

        A descrição volta como texto para a etapa de raciocínio decidir o que
        fazer com ela. O conteúdo é externo e precisa ser saneado pelo chamador
        antes de entrar no histórico.
        """
        response = self._call_chain(
            Role.VISION,
            conversation=Conversation(max_turns=2),
            image=image,
            image_mime=mime_type,
            text=question,
            tools=None,
        )
        return (response.text or "").strip()

    # ------------------------------------------------------------- execução

    def _call_chain(self, role: Role, **kwargs) -> LLMResponse:
        """Percorre a cadeia do papel até um provedor responder."""
        chain = self.chains.get(role) or []
        if not chain:
            self._set_state(ServiceState.OFFLINE)
            raise NoProviderAvailable(f"nenhum provedor configurado para '{role.value}'")

        errors: list[str] = []
        quota_hit = False

        for name in chain:
            provider = self.providers.get(name)
            limiter = self.limiters.get(name)
            if provider is None or limiter is None:
                continue

            try:
                self._respect_rate_limit(name, limiter)
            except NoProviderAvailable as exc:
                quota_hit = True
                errors.append(f"{name}: {exc}")
                continue

            try:
                response = self._invoke(provider, role, **kwargs)
                self._set_state(ServiceState.OK)
                return response
            except QuotaExceeded as exc:
                # Um 429 já foi contado pelo provedor: não devolvemos a cota.
                quota_hit = True
                errors.append(f"{name}: {exc}")
                logger.warning("[%s] %s sem cota, tentando o próximo.", role.value, name)
            except ProviderError as exc:
                limiter.refund()
                errors.append(f"{name}: {exc}")
                logger.warning("[%s] %s falhou (%s), tentando o próximo.", role.value, name, exc)

        self._set_state(ServiceState.QUOTA if quota_hit else ServiceState.OFFLINE)
        audit("llm_indisponivel", papel=role.value, erros=errors, estado=self.state.value)
        raise NoProviderAvailable(f"[{role.value}] " + "; ".join(errors))

    def _invoke(self, provider, role: Role, **kwargs) -> LLMResponse:
        image = kwargs.pop("image", None)
        image_mime = kwargs.pop("image_mime", "image/png")

        if role is Role.VISION:
            if not hasattr(provider, "generate_with_image"):
                raise ProviderError(
                    f"{provider.name} não aceita imagem — configure o Gemini para o papel de visão."
                )
            return provider.generate_with_image(image=image, mime_type=image_mime, **kwargs)

        if role is Role.PERCEPTION and kwargs.get("audio_wav") is not None:
            if not getattr(provider, "accepts_audio", False):
                raise ProviderError(
                    f"{provider.name} não aceita áudio — configure o Gemini para o papel de percepção."
                )
        return provider.generate(**kwargs)

    def _respect_rate_limit(self, name: str, limiter: RateLimiter) -> None:
        """Segura a requisição ou desiste, conforme o tipo de limite."""
        verdict = limiter.acquire()
        if verdict.allowed:
            return

        if verdict.daily_exhausted:
            audit(
                "cota_diaria_esgotada",
                provedor=name,
                usadas=verdict.daily_used,
                limite=verdict.daily_limit,
            )
            raise NoProviderAvailable(verdict.reason)

        if verdict.retry_after_s <= _MAX_INLINE_WAIT_S:
            logger.info("[%s] limite por minuto: aguardando %.1fs.", name, verdict.retry_after_s)
            time.sleep(verdict.retry_after_s)
            if limiter.acquire().allowed:
                return

        audit("limite_por_minuto", provedor=name, espera_s=round(verdict.retry_after_s, 2))
        raise NoProviderAvailable(verdict.reason)
