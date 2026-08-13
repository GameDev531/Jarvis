"""PIN local para confirmar ações de risco.

Existe por um motivo prático, não por teatro de segurança: a confirmação por
voz depende do whisper.cpp, e sem ele TODA ação de Nível 2 era recusada. Com o
PIN, a confirmação continua possível — digitada, que aliás é mais privada que
falar "sim" em voz alta num ambiente com outras pessoas.

O que ele é: uma barreira contra alguém que passa pelo seu computador
desbloqueado e fala com o James. Não é um cofre — o Windows já tem o login de
verdade na frente disto.

O PIN nunca é guardado em texto: fica um hash scrypt com sal aleatório, e a
comparação usa tempo constante para não vazar informação pelo tempo de resposta.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from pathlib import Path

from james.logs import audit, get_logger

logger = get_logger("james.security.pin")

# Parâmetros do scrypt. Custam ~100 ms por verificação, o que é imperceptível
# para quem digita e caro para quem tenta força bruta.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LENGTH = 32
_SALT_BYTES = 16

MIN_PIN_LENGTH = 4
MAX_PIN_LENGTH = 32


class PinError(RuntimeError):
    """PIN inválido ou armazenamento corrompido."""


class PinStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.path.exists()

    def set_pin(self, pin: str) -> None:
        """Define ou troca o PIN. Levanta PinError se o valor for fraco demais."""
        cleaned = str(pin or "").strip()
        if len(cleaned) < MIN_PIN_LENGTH:
            raise PinError(f"O PIN precisa de pelo menos {MIN_PIN_LENGTH} caracteres.")
        if len(cleaned) > MAX_PIN_LENGTH:
            raise PinError(f"O PIN pode ter no máximo {MAX_PIN_LENGTH} caracteres.")

        salt = os.urandom(_SALT_BYTES)
        digest = self._derive(cleaned, salt)
        payload = {
            "algoritmo": "scrypt",
            "n": _SCRYPT_N,
            "r": _SCRYPT_R,
            "p": _SCRYPT_P,
            "sal": salt.hex(),
            "hash": digest.hex(),
        }

        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix(".tmp")
                temp.write_text(json.dumps(payload), encoding="utf-8")
                temp.replace(self.path)
            except OSError as exc:
                raise PinError(f"Não consegui gravar o PIN: {exc}") from exc

        audit("pin_definido")
        logger.info("PIN configurado.")

    def verify(self, pin: str) -> bool:
        """Confere o PIN. False para qualquer problema, sem dizer qual."""
        if not self.configured:
            return False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            salt = bytes.fromhex(payload["sal"])
            expected = bytes.fromhex(payload["hash"])
            derived = self._derive(
                str(pin or "").strip(),
                salt,
                n=int(payload.get("n", _SCRYPT_N)),
                r=int(payload.get("r", _SCRYPT_R)),
                p=int(payload.get("p", _SCRYPT_P)),
            )
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            logger.error("Armazenamento do PIN ilegível: %s", exc)
            return False

        # compare_digest evita vazar quantos caracteres bateram pelo tempo gasto.
        return hmac.compare_digest(derived, expected)

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Não consegui remover o PIN: %s", exc)
        audit("pin_removido")

    @staticmethod
    def _derive(pin: str, salt: bytes, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P) -> bytes:
        return hashlib.scrypt(
            pin.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_KEY_LENGTH
        )
