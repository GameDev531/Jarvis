import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from james.config import Config  # noqa: E402
from james.permissions.guard import Guard  # noqa: E402


@pytest.fixture
def config_data():
    """Config mínima mas realista — espelha o config.yaml de produção."""
    return {
        "persona": {"tratamento": "senhor"},
        "audio": {
            "sample_rate": 16000,
            "channels": 1,
            "sample_width": 2,
            "frame_ms": 30,
            "vad": {"aggressiveness": 2},
        },
        "permissions": {
            "apps": {
                "chrome": "chrome.exe",
                "Bloco de Notas": "notepad.exe",
                "VSCode": "code",
            },
            "folders": [],
            "risky_url_patterns": [
                "checkout",
                "pagamento",
                "carrinho",
                "/cart",
                "cartao",
                "banco",
            ],
            "require_https": True,
            "blocked_domains": ["localhost", "127.0.0.1", "interno.local"],
            "blocked_url_schemes": ["file", "javascript", "data", "vbscript", "about", "ftp"],
            "confirm": {
                "yes_words": ["sim", "confirmo", "pode", "pode sim", "positivo", "autorizo", "ok"],
                "no_words": ["nao", "não", "negativo", "cancela", "para", "melhor nao"],
            },
        },
        "llm": {"rate_limit": {"requests_per_minute": 10, "requests_per_day": 240}},
        "security": {"max_external_chars": 4000, "external_tag": "resultado_externo"},
    }


@pytest.fixture
def config(config_data):
    return Config(config_data)


@pytest.fixture
def guard(config):
    return Guard(config)
