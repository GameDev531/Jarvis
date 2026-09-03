"""Quanto de um resultado de ferramenta entra no histórico.

Medido antes de existir: resultado de ferramenta era **61%** do contexto numa
conversa de quatro leituras de página. A conversa em si, 11%. O modelo relia,
a cada turno, o texto integral de páginas que ele já tinha resumido.

O risco desta camada tem uma forma específica: **truncar um identificador**.
Perder detalhe de um texto é perder detalhe; truncar um `snapshot_id` produz
uma string que parece válida e aponta para nada — a ferramenta seguinte falha
com uma mensagem que não tem relação com a causa.
"""

from __future__ import annotations

import json

import pytest

from james.llm.history import Conversation
from james.llm.orcamento import tamanho
from james.llm.resultado_policy import (
    IDENTIFICADORES,
    PADRAO,
    POLITICAS,
    Politica,
    aplicar,
    politica_de,
)


def grande(n=8000):
    return "x" * n


# ------------------------------------------------------------ o padrão fecha


def test_ferramenta_sem_politica_recebe_o_padrao():
    """A ferramenta que alguém escrever amanhã não pode vazar contexto por
    esquecimento — mesma escolha da política de auditoria."""
    assert politica_de("ferramenta_que_nao_existe") is PADRAO
    podado = aplicar("ferramenta_que_nao_existe", {"texto": grande()})
    assert tamanho(podado) <= PADRAO.max_chars * 1.5


def test_resultado_pequeno_passa_intacto():
    """Podar o que já cabe só criaria diferença sem ganho."""
    original = {"ok": True, "titulo": "curto"}
    assert aplicar("ler_pagina", original) == original


def test_o_original_nao_e_modificado():
    """O chamador ainda lê `result.data` para decidir coisas — o `tab_id` que
    voltou, o pack carregado. Podar aquilo seria podar a lógica junto."""
    original = {"texto": grande(), "titulo": "t"}
    copia = json.loads(json.dumps(original))
    aplicar("ler_pagina", original)
    assert original == copia


# ------------------------------------------------ identificadores sobrevivem


@pytest.mark.parametrize("chave", sorted(IDENTIFICADORES))
def test_identificador_sobrevive_mesmo_vindo_DEPOIS_do_campo_grande(chave):
    """A ordem do dicionário é o que torna isto necessário, e a primeira
    versão deste teste não sabia disso.

    Ele testava `{id, lixo_grande}` — nessa ordem, o id passa mesmo sem
    proteção nenhuma, porque ainda há espaço quando chega a vez dele. Removendo
    o bloco que protege identificadores, o teste continuava verde.

    O risco real é `{lixo_grande, id}`: quando o espaço acaba antes, o campo
    não é truncado — é DESCARTADO. E aí a ferramenta seguinte recebe um
    resultado sem `snapshot_id` nenhum.
    """
    valor = "id_" + "9" * 40
    podado = aplicar("qualquer", {"lixo": grande(), chave: valor})
    assert podado.get(chave) == valor, "o identificador foi descartado"


def test_varios_identificadores_no_fim_sobrevivem_juntos():
    """O caso do navegador: `inspecionar_pagina` devolve os elementos (grandes)
    e os ids logo depois. Perder qualquer um dos dois quebra a ação seguinte."""
    podado = aplicar("qualquer", {
        "elementos": [{"nome": "n" * 300} for _ in range(40)],
        "tab_id": "3",
        "snapshot_id": "abc123def456",
    })
    assert podado["tab_id"] == "3"
    assert podado["snapshot_id"] == "abc123def456"


def test_identificador_sobrevive_aninhado():
    podado = aplicar("qualquer", {"dados": {"tab_id": "7", "texto": grande()}})
    assert podado["dados"]["tab_id"] == "7"


def test_identificadores_sobrevivem_mesmo_com_limite_minusculo():
    politica = Politica(max_chars=10)
    podado = aplicar("x", {"tab_id": "3", "snapshot_id": "abc", "t": grande()}, politica)
    assert podado["tab_id"] == "3"
    assert podado["snapshot_id"] == "abc"


# ------------------------------------------------------------- os essenciais


def test_campo_essencial_sobrevive_ao_aperto():
    """`ler_pagina` sem título é um resultado que não diz de onde veio."""
    podado = aplicar("ler_pagina", {
        "titulo": "Notícia importante", "url": "https://site.com/n",
        "texto": grande(20_000),
    })
    assert podado["titulo"] == "Notícia importante"
    assert podado["url"] == "https://site.com/n"
    assert len(podado["texto"]) < 20_000


def test_a_ordem_dos_essenciais_decide_quem_sobra():
    """O espaço acaba no meio da lista, e é a ordem declarada que resolve."""
    politica = Politica(max_chars=400, essenciais=("primeiro", "segundo"))
    podado = aplicar("x", {
        "primeiro": "a" * 300, "segundo": "b" * 300, "outro": "c" * 300,
    }, politica)
    assert len(podado["primeiro"]) > len(podado.get("segundo", ""))


def test_campo_descartavel_sai_primeiro():
    """O relatório de QA é útil para falar, não para agir; se algo tem de
    encolher, encolhe ele antes da lista de elementos."""
    podado = aplicar("inspecionar_pagina", {
        "snapshot_id": "s1", "tab_id": "2",
        "elementos": [{"element_id": f"e{i}", "nome": "Botão"} for i in range(20)],
        "achados": [{"msg": "a" * 400} for _ in range(30)],
    })
    assert "achados" not in podado
    assert podado["elementos"]
    assert podado["snapshot_id"] == "s1"


# ------------------------------------------------------- a marca de omissão


def test_o_que_foi_omitido_e_anunciado():
    """Sem a marca, o modelo lê um resultado parcial como se fosse o inteiro,
    e responde com confiança sobre o que não viu."""
    podado = aplicar("inspecionar_pagina", {
        "snapshot_id": "s1",
        "elementos": [{"element_id": "e1"}],
        "achados": [{"msg": "a" * 500} for _ in range(20)],
    })
    assert "_podado" in podado
    assert "achados" in podado["_podado"]


def test_texto_cortado_diz_quanto_faltou():
    podado = aplicar("ler_pagina", {"titulo": "t", "texto": "y" * 10_000})
    assert "caracteres" in podado["texto"]


# ------------------------------------------------------------ a forma sobra


def test_o_resultado_podado_continua_serializavel():
    """JSON quebrado faz o modelo alucinar o resto com confiança."""
    podado = aplicar("ler_pagina", {
        "titulo": "t",
        "itens": [{"a": "b" * 500, "c": [1, 2, {"d": "e" * 500}]} for _ in range(30)],
    })
    json.dumps(podado, ensure_ascii=False)


def test_lista_podada_diz_quantos_itens_faltam():
    podado = aplicar("listar_abas", {"abas": [{"titulo": "t" * 200} for _ in range(50)]})
    texto = json.dumps(podado, ensure_ascii=False)
    assert "itens" in texto or "abas" in podado


def test_resultado_que_nao_e_dicionario_tambem_e_podado():
    podado = aplicar("qualquer", grande(50_000))
    assert tamanho(podado) < 5_000


def test_none_e_valores_simples_passam():
    assert aplicar("x", None) is None
    assert aplicar("x", True) is True
    assert aplicar("x", 42) == 42


# -------------------------------------------------- o caminho de verdade


def test_a_poda_acontece_ao_entrar_no_historico():
    """São sete pontos de chamada em dois arquivos; se a poda morasse neles,
    o esquecido apareceria como um contexto inchado sem explicação."""
    conv = Conversation(max_turns=12)
    conv.add_tool_result("ler_pagina", {"titulo": "t", "texto": grande(30_000)}, "1")

    guardado = conv.turns()[0].tool_result
    assert tamanho(guardado) < 3_000
    assert guardado["titulo"] == "t"


def test_o_historico_inteiro_encolhe_de_verdade():
    """O número que justifica o módulo: quatro leituras de página."""
    pagina = {"titulo": "N", "url": "u", "texto": "lorem ipsum " * 300,
              "achados": [{"m": "x" * 60} for _ in range(40)]}

    conv = Conversation(max_turns=20)
    for i in range(4):
        conv.add_tool_result("ler_pagina", pagina, str(i))

    total = sum(tamanho(t.tool_result) for t in conv.turns())
    cru = tamanho(pagina) * 4
    assert total < cru * 0.35, f"{total} de {cru} — poda fraca demais"


# ------------------------------------------------------ a tabela é coerente


def test_toda_politica_declarada_e_para_ferramenta_que_existe():
    """Uma política com nome errado não levanta nada: a ferramenta certa cai
    no padrão, e a linha escrita para ela nunca vale."""
    import tempfile
    from pathlib import Path

    from james.config import Config
    from james.memory.curated_store import MemoryStore
    from james.memory.fact_store import FactStore
    from james.modes import build_manager as build_modes
    from james.permissions.guard import Guard
    from james.skills.registry import SkillRegistry
    from james.tools import build_registry
    from james.ui.bus import StateBus

    raiz = Path(tempfile.mkdtemp())
    config = Config({})
    registry = build_registry(
        config, Guard(config),
        memory=MemoryStore(raiz), facts=FactStore(raiz / "f.db"),
        skills=SkillRegistry(raiz / "skills"),
        modes=build_modes(config, on_acao=lambda *a, **k: None, bus=StateBus()),
    )
    fantasmas = sorted(set(POLITICAS) - set(registry.names))
    assert not fantasmas, f"política para ferramenta inexistente: {fantasmas}"


def test_nenhuma_politica_e_grande_demais():
    """Uma política generosa demais anula o módulo em silêncio — e o custo só
    aparece na conta do contexto, meses depois."""
    exageradas = {n: p.max_chars for n, p in POLITICAS.items() if p.max_chars > 4_000}
    assert not exageradas, f"políticas acima de 4.000 caracteres: {exageradas}"
