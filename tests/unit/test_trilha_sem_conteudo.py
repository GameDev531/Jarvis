"""A trilha não pode guardar o que o usuário disse — por nenhum caminho.

A política de privacidade por schema resolveu o argumento de ferramenta: quem
passa pelo `ToolRegistry` sai redigido. Mas a camada de baixo escreve as
próprias linhas na trilha, e essas não passam por schema nenhum. O resultado
eram duas linhas seguidas se contradizendo:

    {"event": "fato_add",       "texto": "João tem depressão"}      <- em claro
    {"event": "tool_executada", "args": {"texto": "<redacted:19 chars>"}}

Redigir a segunda não serve de nada enquanto a primeira derrama. E o pior era
justamente o mais sensível: `fato_add` gravava o fato inteiro, `memoria_add` a
anotação inteira, `ver_tela` a pergunta falada.

A verificação aqui é por CANÁRIO: um valor improvável é empurrado pelos
caminhos de verdade e depois procurado na trilha como texto cru. É de
propósito que ela não olhe o código — assim continua valendo para o caminho
que alguém escrever amanhã, inclusive um que ninguém pensou em anotar.

O que NÃO é canário, e por escolha: caminho de arquivo e URL. A trilha existe
para responder "o que ele mexeu nos meus arquivos" e "o que ele foi buscar" —
sem o caminho e sem a URL ela não responde nem uma coisa nem outra. O acordo é
diferente: conteúdo que a pessoa disse ou guardou vira digest; recurso que a
ação tocou fica nomeado.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from james.logs import logger as logger_mod

# Improvável de aparecer por acaso, e fácil de achar num arquivo grande.
CANARIO = "ZQXJ_CONTEUDO_PRIVADO_9713"


@pytest.fixture
def trilha(tmp_path, monkeypatch) -> Path:
    destino = tmp_path / "audit.jsonl"
    monkeypatch.setattr(logger_mod, "_audit_path", destino)
    destino.touch()
    return destino


def vazou(trilha: Path) -> list[str]:
    """Os eventos cujo texto cru contém o canário."""
    culpados = []
    for linha in trilha.read_text(encoding="utf-8").splitlines():
        if CANARIO in linha:
            culpados.append(json.loads(linha).get("event", "?"))
    return culpados


def tem_evento(trilha: Path, evento: str) -> bool:
    return any(
        json.loads(l).get("event") == evento
        for l in trilha.read_text(encoding="utf-8").splitlines()
    )


# ------------------------------------------------------------ memória profunda


def test_registrar_fato_nao_grava_o_fato(trilha, tmp_path):
    from james.memory.fact_store import FactStore

    FactStore(tmp_path / "f.db").add(f"João tem {CANARIO}", entidades=["João"])

    assert tem_evento(trilha, "fato_add"), "o evento sumiu junto com o conteúdo"
    assert not vazou(trilha), f"o fato foi para a trilha em claro: {vazou(trilha)}"


def test_o_nome_da_entidade_tambem_e_conteudo(trilha, tmp_path):
    """Uma entidade é um nome próprio: da pessoa, do médico, da empresa."""
    from james.memory.fact_store import FactStore

    FactStore(tmp_path / "f.db").add("um fato qualquer", entidades=[CANARIO])
    assert not vazou(trilha), vazou(trilha)


# -------------------------------------------------------------- memória curada


def test_anotar_na_memoria_nao_grava_a_anotacao(trilha, tmp_path):
    from james.memory.curated_store import MemoryScope, MemoryStore

    loja = MemoryStore(tmp_path)
    loja.add(MemoryScope.USER, f"prefere {CANARIO}")
    assert tem_evento(trilha, "memoria_add")
    assert not vazou(trilha), vazou(trilha)


def test_trocar_e_remover_anotacao_tambem_nao(trilha, tmp_path):
    """Substituir vaza duas vezes se ninguém olhar: o de antes e o de depois."""
    from james.memory.curated_store import MemoryScope, MemoryStore

    loja = MemoryStore(tmp_path)
    loja.add(MemoryScope.USER, "gosta de café pela manhã")
    loja.replace(MemoryScope.USER, "café", f"chá de {CANARIO}")
    assert not vazou(trilha), vazou(trilha)

    loja.remove(MemoryScope.USER, CANARIO)
    assert not vazou(trilha), vazou(trilha)


# --------------------------------------------------------------------- o grafo


@pytest.fixture
def catalogo(tmp_path):
    from james.config import Config
    from james.memory.fact_store import FactStore
    from james.permissions.guard import Guard
    from james.tools import knowledge
    from james.tools.registry import ToolRegistry

    config = Config({})
    registro = ToolRegistry()
    knowledge.register_facts(registro, config, Guard(config), FactStore(tmp_path / "f.db"))
    return registro


def test_ligar_duas_entidades_nao_grava_os_nomes(trilha, catalogo):
    """`relacionar("João", "tem", "depressão")` é um diagnóstico em três campos.

    Separado em argumentos parece inofensivo; junto na mesma linha da trilha é
    exatamente o dado que a política existe para não guardar.
    """
    resultado = catalogo.execute(
        "relacionar", {"origem": "João", "relacao": "tem", "destino": CANARIO}
    )
    assert resultado.ok is True
    assert tem_evento(trilha, "relacao_criada")
    assert not vazou(trilha), vazou(trilha)


# ---------------------------------------------------------------------- visão


def test_a_pergunta_sobre_a_tela_nao_vai_para_a_trilha(trilha):
    """"O que diz naquele e-mail?" descreve o que está na tela sem mostrá-la."""
    from james.config import Config
    from james.permissions.guard import Guard
    from james.tools import vision
    from james.tools.registry import ToolRegistry

    class TelaFalsa:
        def grab_png(self):
            return b"\x89PNG" * 64

    class LLMFalso:
        def describe_image(self, *a, **k):
            return "descrição qualquer"

    config = Config({})
    registro = ToolRegistry()
    registro.screen_grabber = TelaFalsa()
    registro.llm = LLMFalso()
    vision.register(registro, config, Guard(config))

    resultado = registro.execute("ver_tela", {"pergunta": CANARIO})
    assert resultado.ok is True, resultado.message
    assert tem_evento(trilha, "ver_tela")
    assert not vazou(trilha), vazou(trilha)


# ------------------------------------------------- o que continua sendo gravado


def test_a_trilha_continua_util(trilha, tmp_path):
    """Privacidade que apaga o evento inteiro não é privacidade, é cegueira.

    O acordo é: dá para dizer QUE um fato foi guardado, QUANDO, e se é o mesmo
    conteúdo de outra linha (pelo digest). Não dá para dizer QUAL.
    """
    from james.memory.fact_store import FactStore

    # Dois bancos, porque um só recusa o fato repetido — e é essa recusa que
    # torna o digest interessante: ele liga linhas que o banco não liga.
    FactStore(tmp_path / "a.db").add(f"segredo {CANARIO}", entidades=["João"])
    FactStore(tmp_path / "b.db").add(f"segredo {CANARIO}", entidades=["João"])

    linhas = [
        json.loads(l)
        for l in trilha.read_text(encoding="utf-8").splitlines()
        if json.loads(l).get("event") == "fato_add"
    ]
    assert len(linhas) == 2

    for linha in linhas:
        assert linha.get("texto_chars") == len(f"segredo {CANARIO}")
        assert linha.get("texto_hash")

    # Mesmo conteúdo, mesmo digest: dá para correlacionar sem ler.
    assert linhas[0]["texto_hash"] == linhas[1]["texto_hash"]
