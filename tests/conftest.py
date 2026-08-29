import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from james.config import Config  # noqa: E402
from james.permissions.guard import Guard  # noqa: E402

# Segredos que o James lê do ambiente. A suíte nunca deve enxergá-los.
_CREDENCIAIS = (
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "PORCUPINE_ACCESS_KEY",
)


@pytest.fixture(autouse=True)
def sem_credenciais(monkeypatch):
    """Nenhum teste enxerga chave de API de verdade. Por dois motivos.

    **Determinismo.** Um teste como "sem provedor, o estado nasce degradado"
    passava na máquina de quem escreveu e falhava na de quem tinha as chaves
    configuradas — porque ele isolava a config e esquecia o ambiente. O
    resultado dependia de quem rodava, que é o oposto de um teste.

    **Segurança.** Um erro na suíte com a chave visível gastaria a cota diária
    de quem está só rodando os testes, ou pior, mandaria dados para a nuvem sem
    ninguém pedir. Testes não têm por que alcançar credencial de produção.

    Quem precisa de uma chave define a sua com `monkeypatch.setenv` — o
    monkeypatch do próprio teste roda depois deste e ganha.
    """
    for nome in _CREDENCIAIS:
        monkeypatch.delenv(nome, raising=False)


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
