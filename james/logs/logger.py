"""Logging do James + trilha de auditoria de ações executadas.

Duas saídas distintas:
  - log de aplicação (texto, rotativo) para depuração;
  - trilha de auditoria (JSONL, append-only) com TODA ação que tocou o sistema,
    que é o requisito de segurança da seção 2.8 da pesquisa.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from james.logs.privacy import PrivacyMode, get_privacy_mode, set_privacy_mode

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"
_DATE_FORMAT = "%H:%M:%S"

# Chaves cujo valor nunca deve ir para disco, mesmo que apareçam num payload.
_REDACT_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_key",
        "authorization",
        "token",
        "password",
        "senha",
        "secret",
        "gemini_api_key",
        "openrouter_api_key",
        "porcupine_access_key",
    }
)
_REDACTED = "***"

_audit_lock = threading.Lock()
_audit_path: Path | None = None
_configured = False


def _redact(value: Any, _depth: int = 0) -> Any:
    """Remove segredos recursivamente antes de escrever no disco."""
    if _depth > 6:
        return "<profundo demais>"
    if isinstance(value, dict):
        return {
            key: (_REDACTED if str(key).lower() in _REDACT_KEYS else _redact(item, _depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, _depth + 1) for item in value]
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def setup_logging(
    log_dir: str | Path = "logs",
    level: str = "INFO",
    audit_file: str | Path = "logs/audit.jsonl",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
    process_name: str = "james",
    privacy: str = PrivacyMode.STANDARD.value,
) -> None:
    """Configura logging de arquivo + console. Idempotente.

    `privacy` define quanto CONTEÚDO a trilha pode gravar (ver
    james/logs/privacy.py). O padrão nunca grava a frase do usuário; subir para
    `debug_explicit` é uma decisão consciente e fica registrada no log.
    """
    global _audit_path, _configured

    modo = set_privacy_mode(privacy)

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    _audit_path = Path(audit_file)
    _audit_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if _configured:
        root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
        return

    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    file_handler = RotatingFileHandler(
        directory / f"{process_name}.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # No Windows o console pode não aceitar UTF-8; reconfigure quando possível
    # para não perder acento nem quebrar o processo com UnicodeEncodeError.
    stream = sys.stderr
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass
    console = logging.StreamHandler(stream)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Bibliotecas de rede são muito verbosas em DEBUG e poluem a trilha.
    for noisy in ("httpx", "httpcore", "urllib3", "google_genai", "google.genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if modo is PrivacyMode.DEBUG_EXPLICIT:
        # Alto e claro: a trilha vai conter o que a pessoa falou e digitou.
        # Quem ligar isso e esquecer precisa esbarrar no aviso.
        logging.getLogger("james.audit").warning(
            "PRIVACIDADE EM MODO DE DEPURAÇÃO: a trilha vai gravar comandos e "
            "argumentos em texto puro. Volte logs.privacy para 'standard' "
            "quando terminar."
        )

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def audit(event: str, **fields: Any) -> None:
    """Grava um evento na trilha de auditoria (JSONL, uma linha por evento).

    Falha de escrita nunca derruba o James — a ação em si é mais importante que
    o registro dela — mas é reportada no log de aplicação.
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "monotonic": round(time.monotonic(), 3),
        "pid": os.getpid(),
        "event": event,
    }
    # A redação por NOME de chave continua, como segunda camada: ela pega o
    # segredo que vaza por um caminho sem schema (uma exceção, um dict de
    # config). O que decide sobre argumento de ferramenta é a política do
    # schema, aplicada por quem chama (ver james/logs/privacy.py).
    record.update(_redact(fields))
    record["privacidade"] = get_privacy_mode().value

    if _audit_path is None:
        logging.getLogger("james.audit").info("%s | %s", event, record)
        return

    line = json.dumps(record, ensure_ascii=False, default=str)
    try:
        with _audit_lock:
            with _audit_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError as exc:
        logging.getLogger("james.audit").error(
            "Falha ao gravar auditoria (%s): %s", event, exc
        )
