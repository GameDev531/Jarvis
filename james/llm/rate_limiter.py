"""Controle de taxa local para nunca estourar a cota do provedor.

Duas janelas, porque os provedores cobram nas duas:
  - por minuto: janela deslizante em memória (o Gemini free tier limita RPM);
  - por dia: contador persistido em disco, que sobrevive a reinício do James.

Quando a janela por minuto fecha, a resposta certa é esperar (o James diz "só
um momento"); quando a cota do dia acaba, esperar não resolve — aí o
orquestrador cai para o modo degradado.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from james.logs import get_logger

logger = get_logger("james.llm.rate_limiter")


@dataclass(frozen=True)
class RateLimitVerdict:
    allowed: bool
    reason: str = ""
    retry_after_s: float = 0.0
    daily_used: int = 0
    daily_limit: int = 0

    @property
    def daily_exhausted(self) -> bool:
        """Distingue "espere um pouco" de "acabou por hoje"."""
        return not self.allowed and self.retry_after_s <= 0


class RateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        requests_per_day: int,
        state_path: str | Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.rpm = max(1, int(requests_per_minute))
        self.rpd = max(1, int(requests_per_day))
        self.state_path = Path(state_path) if state_path else None
        self._clock = clock
        self._today = today
        self._minute_window: deque[float] = deque()
        self._lock = threading.Lock()
        self._day = self._today()
        self._daily_used = 0
        self._load()

    # ------------------------------------------------------------ persistência

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            stored_day = date.fromisoformat(str(data.get("date", "")))
            if stored_day == self._day:
                self._daily_used = max(0, int(data.get("count", 0)))
                logger.info("Cota do dia já usada: %d/%d", self._daily_used, self.rpd)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            # Contador corrompido: recomeçar do zero é melhor que travar o James.
            logger.warning("Contador de uso ilegível (%s); recomeçando o dia.", exc)

    def _save(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"date": self._day.isoformat(), "count": self._daily_used})
            # Escrita atômica: um desligamento no meio não deixa arquivo pela metade.
            temp = self.state_path.with_suffix(".tmp")
            temp.write_text(payload, encoding="utf-8")
            temp.replace(self.state_path)
        except OSError as exc:
            logger.warning("Não consegui gravar o contador de uso: %s", exc)

    def _roll_day_if_needed(self) -> None:
        current = self._today()
        if current != self._day:
            logger.info("Virada de dia: cota reiniciada (%d usadas ontem).", self._daily_used)
            self._day = current
            self._daily_used = 0
            self._save()

    # -------------------------------------------------------------- decisão

    def _trim_minute_window(self, now: float) -> None:
        while self._minute_window and now - self._minute_window[0] >= 60.0:
            self._minute_window.popleft()

    def check(self) -> RateLimitVerdict:
        """Consulta sem consumir."""
        with self._lock:
            return self._check_locked()

    def _check_locked(self) -> RateLimitVerdict:
        self._roll_day_if_needed()
        now = self._clock()
        self._trim_minute_window(now)

        if self._daily_used >= self.rpd:
            return RateLimitVerdict(
                allowed=False,
                reason=f"cota diária esgotada ({self._daily_used}/{self.rpd})",
                retry_after_s=0.0,
                daily_used=self._daily_used,
                daily_limit=self.rpd,
            )

        if len(self._minute_window) >= self.rpm:
            wait = 60.0 - (now - self._minute_window[0])
            return RateLimitVerdict(
                allowed=False,
                reason=f"limite por minuto atingido ({self.rpm}/min)",
                retry_after_s=max(0.01, wait),
                daily_used=self._daily_used,
                daily_limit=self.rpd,
            )

        return RateLimitVerdict(
            allowed=True, daily_used=self._daily_used, daily_limit=self.rpd
        )

    def acquire(self) -> RateLimitVerdict:
        """Consome uma requisição se houver espaço."""
        with self._lock:
            verdict = self._check_locked()
            if verdict.allowed:
                self._minute_window.append(self._clock())
                self._daily_used += 1
                self._save()
                return RateLimitVerdict(
                    allowed=True, daily_used=self._daily_used, daily_limit=self.rpd
                )
            return verdict

    def refund(self) -> None:
        """Devolve uma requisição que nunca chegou a sair (ex: erro de rede).

        Um 429 NÃO deve ser devolvido: do ponto de vista do provedor ela contou.
        """
        with self._lock:
            if self._minute_window:
                self._minute_window.pop()
            if self._daily_used > 0:
                self._daily_used -= 1
                self._save()

    @property
    def daily_remaining(self) -> int:
        with self._lock:
            self._roll_day_if_needed()
            return max(0, self.rpd - self._daily_used)
