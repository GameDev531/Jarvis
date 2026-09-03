"""`audit()` só protege o que ela consegue registrar.

Achado ao trabalhar na privacidade da trilha: `james/tools/knowledge.py`
chamava `audit(...)` em dois pontos sem NUNCA ter importado o nome. As duas
chamadas levantavam `NameError` — e o `except Exception` do `ToolRegistry`, que
existe para uma tool não derrubar o James, engolia o erro e devolvia "Não
consegui completar essa ação".

O efeito era duplo e silencioso: `relacionar` e a parte de `revisar_fato` que
derruba relações falhavam para o usuário, e nem uma linha ia para a trilha.
Nenhum teste pegava, porque a falha parecia uma tool que simplesmente não deu
certo.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from james.logs import logger as logger_mod

RAIZ = Path(__file__).resolve().parent.parent.parent


# ------------------------------------------------------------- a regressão


@pytest.fixture
def fatos(tmp_path):
    from james.memory.fact_store import FactStore

    return FactStore(tmp_path / "fatos.db")


@pytest.fixture
def catalogo_de_conhecimento(fatos):
    from james.config import Config
    from james.permissions.guard import Guard
    from james.tools import knowledge
    from james.tools.registry import ToolRegistry

    config = Config({})
    registry = ToolRegistry()
    knowledge.register_facts(registry, config, Guard(config), fatos)
    return registry


def test_relacionar_funciona_e_deixa_rastro(catalogo_de_conhecimento, tmp_path, monkeypatch):
    """Era um NameError disfarçado de "não consegui completar essa ação"."""
    destino = tmp_path / "audit.jsonl"
    monkeypatch.setattr(logger_mod, "_audit_path", destino)

    resultado = catalogo_de_conhecimento.execute(
        "relacionar", {"origem": "João", "relacao": "trabalha em", "destino": "Acme"}
    )

    assert resultado.ok is True
    assert resultado.data["criada"] is True
    eventos = [json.loads(linha)["event"] for linha in destino.read_text("utf-8").splitlines()]
    assert "relacao_criada" in eventos


def test_derrubar_relacoes_de_um_fato_refutado_funciona(
    catalogo_de_conhecimento, fatos, tmp_path, monkeypatch
):
    """O segundo ponto do mesmo bug, no caminho que mais importa.

    Refutar "João trabalha na Acme" tem que derrubar as relações que aquele
    fato sustentava. Com o `NameError`, a exceção estourava no meio — e a
    conclusão continuava de pé, apoiada num fato que o usuário já negou.
    """
    monkeypatch.setattr(logger_mod, "_audit_path", tmp_path / "audit.jsonl")

    registro = catalogo_de_conhecimento.execute(
        "registrar_fato", {"texto": "João trabalha na Acme", "entidades": ["João", "Acme"]}
    )
    fato_id = registro.data["id"]
    fatos.relacionar("João", "trabalha em", "Acme", fato_id=fato_id)
    assert fatos.relacoes_de("João")

    resultado = catalogo_de_conhecimento.execute(
        "revisar_fato", {"id": fato_id, "acao": "refutar"}
    )
    assert resultado.ok is True
    assert resultado.data["relacoes_removidas"] >= 1
    assert not fatos.relacoes_de("João")


# ------------------------------------------------ a trava contra a repetição


def _modulos_do_projeto() -> list[Path]:
    return [
        caminho
        for caminho in (RAIZ / "james").rglob("*.py")
        if "__pycache__" not in caminho.parts
    ]


def test_todo_modulo_que_chama_audit_importa_audit():
    """A regra que faltava, e que vale para o módulo que alguém escrever amanhã.

    Um `audit()` sem import não quebra a importação nem os testes: quebra só na
    linha em que é chamado, e só quando aquele caminho roda. Aqui a conferência
    é estática — a AST diz quem chama e quem importa.
    """
    faltando = []
    for caminho in _modulos_do_projeto():
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))

        chama = any(
            isinstance(no, ast.Call)
            and isinstance(no.func, ast.Name)
            and no.func.id == "audit"
            for no in ast.walk(arvore)
        )
        if not chama:
            continue

        importa = any(
            isinstance(no, ast.ImportFrom)
            and any(alias.name == "audit" or alias.asname == "audit" for alias in no.names)
            for no in ast.walk(arvore)
        ) or caminho.name == "logger.py"        # é onde `audit` nasce

        if not importa:
            faltando.append(caminho.relative_to(RAIZ).as_posix())

    assert not faltando, f"chamam audit() sem importar: {faltando}"


def test_todo_modulo_que_chama_audit_text_importa_audit_text():
    """A mesma trava para a função nova — que é justamente a que protege a
    frase do usuário, e falhar em silêncio nela seria pior."""
    faltando = []
    for caminho in _modulos_do_projeto():
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        chama = any(
            isinstance(no, ast.Call)
            and isinstance(no.func, ast.Name)
            and no.func.id == "audit_text"
            for no in ast.walk(arvore)
        )
        if not chama:
            continue
        importa = any(
            isinstance(no, ast.ImportFrom)
            and any(alias.name == "audit_text" for alias in no.names)
            for no in ast.walk(arvore)
        ) or caminho.name == "privacy.py"
        if not importa:
            faltando.append(caminho.relative_to(RAIZ).as_posix())

    assert not faltando, f"chamam audit_text() sem importar: {faltando}"
