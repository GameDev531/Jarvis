"""O ZIP distribuído leva o projeto, e só o projeto.

O ZIP anterior levou junto bancos SQLite (com `-wal` e `-shm`), o
`runtime_state`, contadores de uso, logs, a trilha de auditoria e
`__pycache__`. A causa não foi esquecimento: era a forma da pergunta.
"A árvore toda, menos o que eu lembrar de excluir" deixa entrar todo formato
de artefato que ainda não existia quando a lista foi escrita.

Estes testes trancam a inversão — allowlist — e as três armadilhas que ela
mesma criou.
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from james.distribuicao import (
    PADROES_DE_SEGREDO,
    montar,
    motivo_de_recusa,
    varrer_segredos,
)

RAIZ = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def pacote():
    return montar(RAIZ)


def caminhos(pacote) -> set[str]:
    return {p.as_posix() for p in pacote.incluidos}


# ---------------------------------------- os dois testes pedidos na auditoria


def test_distribution_contains_no_runtime_data(pacote):
    """Nada de banco, log, estado, cota ou cache no pacote."""
    proibidos = (
        ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
        ".log", ".jsonl", ".pyc", ".pyo",
    )
    for caminho in caminhos(pacote):
        assert not caminho.endswith(proibidos), caminho
        assert "__pycache__" not in caminho, caminho
        for pasta in ("logs/", "state/", "memories/", "browser_profiles/"):
            assert not caminho.startswith(pasta), caminho

    nomes = {Path(c).name for c in caminhos(pacote)}
    for nome in (".env", ".coverage", "hardware_report.json", "runtime_state.json",
                 "usage_gemini.json", "james.lock", "pin.json"):
        assert nome not in nomes, nome


def test_distribution_contains_no_secret_patterns(pacote):
    """O scanner lê o CONTEÚDO dos arquivos aprovados, não só o nome deles."""
    assert pacote.segredos == [], [str(a) for a in pacote.segredos]
    assert pacote.pode_gerar is True


# -------------------------------------------- o pacote continua sendo o projeto

def _tem_git() -> bool:
    if not (RAIZ / ".git").exists():
        return False
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _tem_git(), reason="sem git: nada a comparar")
def test_todo_arquivo_rastreado_pelo_git_entra_no_pacote(pacote):
    """Uma allowlist apertada demais quebra o pacote em silêncio.

    O erro aqui não aparece nos testes de quem construiu: aparece como
    ModuleNotFoundError na máquina de quem recebeu. Comparar com o git é o que
    transforma isso em vermelho aqui.
    """
    saida = subprocess.run(
        ["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, timeout=30
    ).stdout
    rastreados = {linha for linha in saida.splitlines() if linha}
    faltando = sorted(rastreados - caminhos(pacote))
    assert not faltando, (
        "arquivos versionados que a allowlist deixaria de fora: "
        + ", ".join(f"{f} ({motivo_de_recusa(Path(f))})" for f in faltando)
    )


def test_os_pacotes_python_de_nome_perigoso_entram(pacote):
    """`james/state/` e `james/logs/` são código, não pastas de runtime.

    Este é o bug do `.gitignore` de novo, noutra roupa: um padrão `state/` sem
    âncora casa em qualquer profundidade e engole o pacote Python. Lá o
    sintoma foi um módulo que nunca chegou ao GitHub.
    """
    incluidos = caminhos(pacote)
    for modulo in (
        "james/state/ipc.py",
        "james/state/runtime_state.py",
        "james/logs/logger.py",
        "james/logs/privacy.py",
    ):
        assert modulo in incluidos, modulo


def test_o_javascript_de_terceiro_que_a_interface_importa_entra(pacote):
    """`ui/web/index.html` importa o three.js de `vendor/`. Sem ele, tela preta."""
    incluidos = caminhos(pacote)
    assert "ui/web/vendor/three.module.js" in incluidos
    assert "ui/web/vendor/GLTFLoader.js" in incluidos


# ------------------------------------------------------------- as regras cruas


@pytest.mark.parametrize(
    "caminho",
    [
        ".env",
        ".coverage",
        "hardware_report.json",
        "logs/james.log",
        "logs/audit.jsonl",
        "state/fatos.db",
        "state/fatos.db-wal",
        "state/fatos.db-shm",
        "state/runtime_state.json",
        "state/usage_gemini.json",
        "state/browser_profiles/jarvis-default/Cookies",
        "memories/USER.md",
        "james/__pycache__/config.cpython-311.pyc",
        "james.lock",
        "credentials.json",
        "voices/pt_BR-faber.onnx",
    ],
)
def test_dado_de_execucao_e_recusado(caminho):
    assert motivo_de_recusa(Path(caminho)) is not None, f"{caminho} entraria no ZIP"


@pytest.mark.parametrize(
    "caminho",
    [
        "james/config.py",
        "james/logs/privacy.py",
        "james/state/runtime_state.py",
        "README.md",
        "config.yaml",
        "ui/web/app.js",
        "ui/web/index.html",
        ".env.example",
        "tests/unit/test_distribuicao.py",
        "skills/planilhas/SKILL.md",
    ],
)
def test_codigo_do_projeto_passa(caminho):
    assert motivo_de_recusa(Path(caminho)) is None, f"{caminho} ficaria de fora"


def test_formato_novo_de_artefato_nao_entra_por_padrao():
    """O ponto da inversão: não é preciso prever o artefato de amanhã.

    Nenhuma destas extensões está em lista de proibidos — elas simplesmente
    não estão na allowlist, e isso basta.
    """
    for caminho in ("state/cache.pkl", "james/modelo.safetensors", "perfil.har"):
        assert motivo_de_recusa(Path(caminho)) is not None, caminho


def test_a_armadilha_do_ponto_inicial_continua_documentada():
    """`Path(".coverage").suffix` é VAZIO — o ponto faz o nome todo virar stem.

    Filtrar por extensão deixava este passar, e foi assim que ele entrou no
    repositório uma vez.
    """
    assert Path(".coverage").suffix == ""
    assert motivo_de_recusa(Path(".coverage")) is not None


# ------------------------------------------------------------------ o scanner


# As credenciais falsas abaixo são o material de teste do próprio scanner, e
# por isso cada linha carrega o marcador de exemplo — senão o scanner
# reprovaria a distribuição por causa dos seus testes. Ver MARCADOR_DE_EXEMPLO.
@pytest.mark.parametrize(
    "conteudo",
    [
        'GEMINI_API_KEY = "AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"',  # pragma: exemplo-de-segredo
        'chave = "sk-proj-abcdefghijklmnopqrstuvwxyz0123"',  # pragma: exemplo-de-segredo
        'OPENROUTER = "sk-or-v1-0123456789abcdef0123456789abcdef"',  # pragma: exemplo-de-segredo
        'token = "ghp_0123456789abcdefghijklmnopqrstuvwxyz"',  # pragma: exemplo-de-segredo
        'aws = "AKIAIOSFODNN7EXAMPLE"',  # pragma: exemplo-de-segredo
        "-----BEGIN RSA PRIVATE KEY-----",  # pragma: exemplo-de-segredo
        'password = "correcthorsebatterystaple"',  # pragma: exemplo-de-segredo
        'HA_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJhYmMifQ.assin"',  # pragma: exemplo-de-segredo
    ],
)
def test_o_scanner_reconhece_credencial(conteudo):
    assert varrer_segredos(Path("falso.py"), conteudo), conteudo


def test_o_marcador_de_exemplo_vale_por_LINHA():
    """A saída é estreita de propósito: uma linha, não um arquivo.

    Um `# noqa` de arquivo inteiro esconderia o segredo de verdade que
    aparecesse ali depois.
    """
    from james.distribuicao import MARCADOR_DE_EXEMPLO

    texto = (
        f'exemplo = "AKIAIOSFODNN7EXAMPLE"  # {MARCADOR_DE_EXEMPLO}\n'  # pragma: exemplo-de-segredo
        'de_verdade = "AKIAIOSFODNN7EXAMPLE"\n'  # pragma: exemplo-de-segredo
    )
    achados = varrer_segredos(Path("x.py"), texto)
    assert len(achados) == 1 and "linha 2" in achados[0].detalhe


def test_as_excecoes_do_scanner_sao_poucas_e_localizadas():
    """Uma exceção que ninguém consegue listar não é exceção, é buraco.

    Duas ocorrências no projeto inteiro, e as duas explicáveis: o módulo que
    DEFINE o marcador, e o arquivo de teste que precisa de credencial falsa
    para provar que o scanner reconhece credencial. Qualquer terceira precisa
    ser discutida — é para isso que este teste falha.
    """
    from james.distribuicao import MARCADOR_DE_EXEMPLO

    com_marcador = sorted(
        caminho.relative_to(RAIZ).as_posix()
        for caminho in RAIZ.rglob("*.py")
        if "__pycache__" not in caminho.parts
        and MARCADOR_DE_EXEMPLO in caminho.read_text(encoding="utf-8", errors="replace")
    )
    assert com_marcador == [
        "james/distribuicao.py",
        "tests/unit/test_distribuicao.py",
    ], com_marcador


@pytest.mark.parametrize(
    "conteudo",
    [
        "GEMINI_API_KEY=",                       # é o formato do .env.example
        "# defina OPENROUTER_API_KEY no seu .env",
        'get_secret("OPENROUTER_API_KEY")',
        'assert "password" in campos',
        "sk-",
    ],
)
def test_o_scanner_nao_grita_com_codigo_honesto(conteudo):
    """Falso positivo aborta a construção — é caro, e treina a ignorar o aviso."""
    assert varrer_segredos(Path("falso.py"), conteudo) == [], conteudo


def test_o_env_de_exemplo_passa_pelo_scanner():
    exemplo = RAIZ / ".env.example"
    assert varrer_segredos(exemplo) == []


def test_o_scanner_aponta_a_linha():
    achados = varrer_segredos(
        Path("x.py"),
        "linha um\nlinha dois\nchave = 'AKIAIOSFODNN7EXAMPLE'",  # pragma: exemplo-de-segredo
    )
    assert achados and "linha 3" in achados[0].detalhe


def test_ha_padrao_para_cada_credencial_que_o_projeto_usa():
    """`.env.example` é o inventário: cada chave de lá tem que ser reconhecível."""
    nomes = " ".join(p.nome.lower() for p in PADROES_DE_SEGREDO)
    for provedor in ("gemini", "openrouter", "elevenlabs"):
        assert provedor in nomes, provedor


# ---------------------------------------------------------------- fail-closed


def test_segredo_encontrado_impede_o_zip(tmp_path):
    """A construção ABORTA — não avisa e continua."""
    (tmp_path / "james").mkdir()
    (tmp_path / "james" / "config.py").write_text(
        'CHAVE = "AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p"',  # pragma: exemplo-de-segredo
        encoding="utf-8",
    )
    pacote = montar(tmp_path)
    assert pacote.segredos and pacote.pode_gerar is False


def test_arvore_limpa_pode_gerar(tmp_path):
    (tmp_path / "james").mkdir()
    (tmp_path / "james" / "config.py").write_text("VALOR = 1\n", encoding="utf-8")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "james.log").write_text(
        "segredo AKIAIOSFODNN7EXAMPLE",  # pragma: exemplo-de-segredo
        encoding="utf-8",
    )

    pacote = montar(tmp_path)
    assert pacote.pode_gerar is True
    # O log tinha um segredo dentro — e não foi escaneado porque nem chegou a
    # ser candidato. A allowlist barra antes.
    assert [p.as_posix() for p in pacote.incluidos] == ["james/config.py"]


def test_a_cli_recusa_gerar_o_zip_quando_ha_segredo(tmp_path, monkeypatch, capsys):
    import distribuir

    (tmp_path / "james").mkdir()
    (tmp_path / "james" / "x.py").write_text(
        'k = "AKIAIOSFODNN7EXAMPLE"',  # pragma: exemplo-de-segredo
        encoding="utf-8",
    )
    monkeypatch.setattr(distribuir, "RAIZ", tmp_path)

    destino = tmp_path / "saida.zip"
    assert distribuir.main(["--saida", str(destino)]) == 1
    assert not destino.exists(), "o ZIP não pode existir depois de abortar"
    assert "ABORTADO" in capsys.readouterr().out


def test_a_cli_gera_o_zip_de_verdade(tmp_path, monkeypatch):
    import distribuir

    (tmp_path / "james").mkdir()
    (tmp_path / "james" / "x.py").write_text("VALOR = 1\n", encoding="utf-8")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "fatos.db").write_bytes(b"SQLite format 3\x00")
    monkeypatch.setattr(distribuir, "RAIZ", tmp_path)

    destino = tmp_path / "saida.zip"
    assert distribuir.main(["--saida", str(destino)]) == 0
    with zipfile.ZipFile(destino) as zf:
        assert zf.namelist() == ["james/x.py"]


def test_a_funcao_antiga_continua_valendo():
    """`_suspeito` era o que os testes de auditoria interrogavam.

    Trocar a implementação por dentro não pode apagar a cobertura que já
    existia — o contrato do nome é mantido de propósito.
    """
    from distribuir import _suspeito

    assert _suspeito(Path("logs/james.log")) is not None
    assert _suspeito(Path("james/config.py")) is None
