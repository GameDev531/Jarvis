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


# ---------------------------------------------- modelos do OpenRouter no config

# O catálogo `:free` muda sozinho e o config não fica sabendo. Estes testes não
# conseguem (nem devem) falar com a rede — quem confere se um ID ainda existe é
# o `check_modelos.py`. O que dá para garantir aqui é a forma da lista, que é
# onde mora o erro caro.

# Grátis, mas sem o sufixo `:free` — são roteadores, não modelos. A exceção é
# NOMEADA em vez de a regra ser afrouxada: "termina em :free" continua valendo
# para todo o resto, e é ela que impede um ID pago de entrar em silêncio.
#
# Cuidado com o vizinho: `openrouter/auto` PODE rotear para modelo pago, e a
# diferença entre ele e este aqui é uma palavra.
GRATIS_SEM_SUFIXO = frozenset({"openrouter/free"})


def _openrouter_do_config():
    from james.config import load_config
    return load_config().section("llm.openrouter")


def test_todo_modelo_do_openrouter_e_gratuito():
    """Um ID sem `:free` é um modelo PAGO, e ele não avisa.

    O sufixo é a única diferença entre `z-ai/glm-5.2` e `z-ai/glm-5.2:free`.
    Esquecê-lo não quebra nada, não gera erro e não aparece em log nenhum —
    só aparece na fatura. A regra do projeto é custo zero, e ela precisa de
    uma trava, não de atenção.
    """
    secao = _openrouter_do_config()
    todos = list(secao.get("models") or []) + list(secao.get("vision_models") or [])
    pagos = [m for m in todos if not str(m).endswith(":free") and m not in GRATIS_SEM_SUFIXO]
    assert not pagos, f"modelos pagos no config: {pagos}"


def test_ha_modelo_para_os_dois_papeis():
    """Lista vazia derruba o papel inteiro em toda requisição."""
    secao = _openrouter_do_config()
    assert secao.get("models"), "llm.openrouter.models está vazio"
    assert secao.get("vision_models"), "llm.openrouter.vision_models está vazio"


def test_sem_modelo_repetido_na_mesma_lista():
    """Repetido não dá erro — só desperdiça uma posição da cadeia de reserva."""
    secao = _openrouter_do_config()
    for nome in ("models", "vision_models"):
        lista = [str(m) for m in (secao.get(nome) or [])]
        assert len(lista) == len(set(lista)), f"{nome} tem modelo repetido"


def test_modelos_removidos_do_catalogo_nao_voltam():
    """Regressão de agosto/2026: o OpenRouter removeu o tier grátis inteiro da
    Meta e da Qwen, e estes IDs ficaram no config devolvendo 404 em silêncio."""
    secao = _openrouter_do_config()
    todos = {str(m) for m in
             list(secao.get("models") or []) + list(secao.get("vision_models") or [])}
    mortos = {
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen-2.5-72b-instruct:free",
        "meta-llama/llama-3.2-11b-vision-instruct:free",
    }
    assert not (todos & mortos), f"IDs removidos do catálogo: {todos & mortos}"


def test_o_roteador_gratis_fica_por_ultimo():
    """Ele escolhe um modelo ao ACASO. Como primeiro, seria o James trocando de
    personalidade a cada turno — o mesmo defeito que a troca de voz tinha.

    Como último presta: enquanto existir um modelo grátis no OpenRouter, esta
    linha responde. É a única que não pode virar 404.
    """
    modelos = [str(m) for m in _openrouter_do_config().get("models") or []]
    assert modelos[-1] == "openrouter/free"
    assert "openrouter/free" not in modelos[:-1]


def test_openrouter_auto_nunca_entra():
    """`openrouter/auto` roteia para modelos PAGOS. Uma palavra de diferença
    para o `openrouter/free`, e a conta chega no fim do mês."""
    secao = _openrouter_do_config()
    todos = list(secao.get("models") or []) + list(secao.get("vision_models") or [])
    assert "openrouter/auto" not in [str(m) for m in todos]


def test_nenhum_modelo_pequeno_no_raciocinio():
    """Uma cadeia de reserva vale o que vale o seu pior membro alcançável.

    Modelo de 9B responde — como atendente. No dia em que a cadeia descer até
    ele, a persona que a gente ajustou vai junto. Estes ficam de fora por
    escolha, não por esquecimento.
    """
    pequenos = {
        "nvidia/nemotron-nano-9b-v2:free",
        "openai/gpt-oss-20b:free",
    }
    modelos = {str(m) for m in _openrouter_do_config().get("models") or []}
    assert not (modelos & pequenos), f"modelo pequeno no raciocínio: {modelos & pequenos}"
