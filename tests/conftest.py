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
    # As duas de voz entraram depois e a tupla não acompanhou. A falha aqui é
    # mais cara que nos outros: as chaves de LLM cobram por requisição, mas as
    # de voz cobram por CARACTERE — uma suíte que sintetizasse por engano
    # comeria a cota do mês inteira sem ninguém perceber.
    "ELEVENLABS_API_KEY",
    "LMNT_API_KEY",
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
def dns_falso(monkeypatch):
    """Substitui `socket.getaddrinfo` por uma tabela declarada pelo teste.

    Existe porque um teste de auditoria resolvia `example.com` DE VERDADE:
    numa máquina sem DNS ele reprovava código correto, e teste que reprova por
    causa da rede ensina a ignorar teste vermelho. Além disso, o resolvedor
    real não é previsível — há provedor de DNS que responde NXDOMAIN com a
    página de busca dele, e aí o teste do "nome que não existe" passa a
    testar o provedor.

    Uso:

        dns_falso("exemplo.com", ["93.184.216.34"])
        dns_falso("caiu.com", erro=True)

    Nome não declarado levanta `gaierror`, como um domínio inexistente — o
    padrão fecha, e um teste que esqueceu de declarar falha em vez de sair
    para a rede.
    """
    import socket

    tabela: dict[str, list[str] | None] = {}

    def declarar(host: str, ips: list[str] | None = None, *, erro: bool = False):
        tabela[host.strip().strip("[]").lower()] = None if erro else list(ips or [])

    def _getaddrinfo(host, port, *args, **kwargs):
        chave = str(host).strip().strip("[]").lower()
        enderecos = tabela.get(chave, None)
        if enderecos is None:
            raise socket.gaierror(f"[dns_falso] '{host}' não foi declarado no teste")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0)) for ip in enderecos]

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    return declarar


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch, request):
    """Nenhum teste unitário abre soquete para fora. Nem por engano.

    A trava é no `socket.socket.connect`: seja `httpx`, `requests` ou o SDK de
    um provedor, todos passam por ali. Um teste que precise de rede de verdade
    declara `@pytest.mark.network` (e mora em tests/integration/).

    Isto não é purismo. Um teste que alcança a internet falha quando a
    internet falha, deixa rastro em serviço de terceiro e — no caso de um
    provedor de LLM ou de voz — gasta cota de verdade.
    """
    if request.node.get_closest_marker("network"):
        return

    import socket

    real = socket.socket.connect

    def _recusar(self, endereco, *args, **kwargs):
        # Soquete local (o IPC e a interface holográfica usam) continua valendo:
        # é dentro da própria máquina e não depende de nada externo.
        host = endereco[0] if isinstance(endereco, tuple) else endereco
        if isinstance(host, str) and (
            host.startswith("127.") or host in ("::1", "localhost", "0.0.0.0")
        ):
            return real(self, endereco, *args, **kwargs)
        raise RuntimeError(
            f"Teste unitário tentou conectar em {endereco!r}. Use um dublê, ou "
            "marque o teste com @pytest.mark.network e mova para tests/integration/."
        )

    monkeypatch.setattr(socket.socket, "connect", _recusar)


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
