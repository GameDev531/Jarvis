"""Carregamento de configuração e de segredos.

O `.env` é o primeiro obstáculo de quem instala o James: as chaves estão lá, e
se o carregamento falhar calado a mensagem que aparece ("GEMINI_API_KEY
ausente") aponta para o lugar errado. Estes testes existem para isso não
acontecer de novo.
"""

import pytest

from james.config import get_secret, load_env


# ============================================================ .env

def test_env_carrega_as_chaves(tmp_path, monkeypatch):
    """O caminho que todo mundo percorre no primeiro dia."""
    (tmp_path / ".env").write_text(
        "GEMINI_API_KEY=abc123\nOPENROUTER_API_KEY=sk-or-xyz\n", encoding="utf-8"
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    load_env(tmp_path)
    assert get_secret("GEMINI_API_KEY") == "abc123"
    assert get_secret("OPENROUTER_API_KEY") == "sk-or-xyz"


def test_env_sem_dotenv_instalado_ainda_carrega(tmp_path, monkeypatch):
    """A regressão que originou o leitor próprio.

    Com o python-dotenv faltando, a versão antiga voltava calada: as chaves
    estavam no arquivo, o James dizia "ausente", e nada ligava uma coisa à
    outra. Agora o arquivo é lido de qualquer jeito.
    """
    import builtins

    (tmp_path / ".env").write_text("GEMINI_API_KEY=sem-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    real_import = builtins.__import__

    def sem_dotenv(nome, *args, **kwargs):
        if nome == "dotenv":
            raise ImportError("simulando ausência do python-dotenv")
        return real_import(nome, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sem_dotenv)
    load_env(tmp_path)
    assert get_secret("GEMINI_API_KEY") == "sem-dotenv"


def test_env_aceita_aspas_export_e_comentario():
    from james.config import _parse_env

    lido = _parse_env(
        '# comentário\n'
        '\n'
        'SIMPLES=valor\n'
        'COM_ASPAS="entre aspas"\n'
        "COM_APOSTROFE='apostrofe'\n"
        'export COM_EXPORT=  espacos  \n'
        'linha sem igual\n'
        '=sem_chave\n'
    )
    assert lido == {
        "SIMPLES": "valor",
        "COM_ASPAS": "entre aspas",
        "COM_APOSTROFE": "apostrofe",
        "COM_EXPORT": "espacos",
    }


def test_valor_com_igual_no_meio_e_preservado():
    """Chave de API pode ter '=' (base64 costuma terminar assim)."""
    from james.config import _parse_env

    assert _parse_env("K=a=b=c")["K"] == "a=b=c"


def test_ambiente_real_ganha_do_arquivo(tmp_path, monkeypatch):
    """Permite trocar uma chave por um turno sem editar o .env."""
    (tmp_path / ".env").write_text("GEMINI_API_KEY=do-arquivo\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "do-ambiente")
    load_env(tmp_path)
    assert get_secret("GEMINI_API_KEY") == "do-ambiente"


def test_sem_arquivo_env_nao_quebra(tmp_path):
    load_env(tmp_path)          # pasta vazia: modo degradado, sem exceção


def test_chave_vazia_conta_como_ausente(tmp_path, monkeypatch):
    """`GEMINI_API_KEY=` no arquivo é o padrão do .env.example ainda em branco."""
    (tmp_path / ".env").write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    load_env(tmp_path)
    assert get_secret("GEMINI_API_KEY") is None


# ================================================ isolamento da suíte

def test_a_suite_nao_enxerga_chave_de_api():
    """A fixture `sem_credenciais` (autouse, em conftest.py) está de pé?

    Um teste de "sem provedor disponível" passava em quem não tinha as chaves
    configuradas e falhava em quem tinha — porque isolava a config e esquecia o
    ambiente. Se esta fixture sumir, essa classe de falha volta, e volta como
    "funciona na minha máquina".

    Vale também como trava de segurança: com a chave visível, um erro na suíte
    gastaria cota real ou mandaria dados para a nuvem sem ninguém pedir.
    """
    import os

    for nome in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "PORCUPINE_ACCESS_KEY"):
        assert os.environ.get(nome) is None, (
            f"{nome} está visível para a suíte. A fixture autouse "
            "`sem_credenciais` em tests/conftest.py deveria ter limpado."
        )


def test_um_teste_pode_definir_a_propria_chave(monkeypatch):
    """O isolamento não pode impedir quem precisa de uma chave de verdade."""
    monkeypatch.setenv("GEMINI_API_KEY", "chave-do-proprio-teste")
    assert get_secret("GEMINI_API_KEY") == "chave-do-proprio-teste"
