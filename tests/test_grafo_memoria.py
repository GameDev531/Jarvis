"""Grafo de memória — a inferência que uma lista de fatos não faz.

A ideia veio do Graft, e a correção veio junto: o Graft indexa CÓDIGO com
tree-sitter, que analisa sintaxe. Rodar isso sobre um `MEMORY.md` de poucos
kilobytes devolveria uma árvore de nós markdown — título, lista, parágrafo —
que descreve a formatação do arquivo, não o significado.

As arestas de um grafo de memória vêm de ENTIDADES E RELAÇÕES, não de nós de
sintaxe. E o `FactStore` já tinha entidades; o que faltava era a ligação entre
elas.

O que isso compra, concretamente:

    "João trabalha na Acme"        fato 1
    "A Acme fica em São Paulo"     fato 2

Nenhuma busca textual por "João" e "São Paulo" liga os dois. Percorrer as
arestas liga — e é o que separa memória de arquivo de anotações.
"""

from __future__ import annotations

import pytest

from james.memory.fact_store import FactStore


@pytest.fixture
def grafo(tmp_path):
    fs = FactStore(tmp_path / "f.db")
    f1 = fs.add("João trabalha na Acme", entidades=["João", "Acme"])
    f2 = fs.add("A Acme fica em São Paulo", entidades=["Acme", "São Paulo"])
    f3 = fs.add("Maria é irmã de João", entidades=["Maria", "João"])
    fs.relacionar("João", "trabalha em", "Acme", fato_id=f1.id)
    fs.relacionar("Acme", "fica em", "São Paulo", fato_id=f2.id)
    fs.relacionar("Maria", "é irmã de", "João", fato_id=f3.id)
    fs.fatos = {"joao_acme": f1.id, "acme_sp": f2.id, "maria_joao": f3.id}
    return fs


# ---------------------------------------------------------------- arestas


def test_a_aresta_e_criada_e_contada(grafo):
    assert grafo.grafo_stats() == {"nos": 4, "arestas": 3}


def test_aresta_repetida_nao_e_erro(grafo):
    """Repetir não é falha. Levantar exceção faria o modelo tratar redundância
    como problema e tentar contornar."""
    assert grafo.relacionar("João", "trabalha em", "Acme") is False
    assert grafo.grafo_stats()["arestas"] == 3


def test_aresta_de_uma_entidade_para_ela_mesma_e_recusada(grafo):
    """Não acrescenta caminho e faz a travessia andar em círculo."""
    assert grafo.relacionar("João", "é", "joão") is False


def test_acento_nao_cria_no_duplicado(grafo):
    """'São Paulo' e 'sao paulo' são o mesmo lugar. Sem normalizar, o grafo
    teria dois nós e nenhum caminho entre eles."""
    grafo.relacionar("sao paulo", "fica no", "Brasil")
    caminho = grafo.caminho("João", "Brasil")
    assert caminho is not None and len(caminho) == 3


def test_relacoes_saem_e_chegam(grafo):
    """Quem pergunta "o que você sabe sobre João" quer tanto "trabalha na X"
    quanto "Maria é irmã dele"."""
    sentidos = {r["sentido"] for r in grafo.relacoes_de("João")}
    assert sentidos == {"saindo", "chegando"}


# --------------------------------------------------------------- travessia


def test_o_caminho_atravessa_fatos_separados(grafo):
    """A inferência que nenhuma busca textual faz."""
    caminho = grafo.caminho("João", "São Paulo")
    assert caminho is not None
    assert [s["tipo"] for s in caminho] == ["trabalha em", "fica em"]


def test_o_caminho_anda_contra_a_seta_quando_precisa(grafo):
    """"Maria é irmã de João" aponta de Maria para João. Para chegar de Maria
    a São Paulo, o primeiro salto é direto e o resto segue — mas a busca
    precisa saber andar nos dois sentidos, senão metade do grafo fica
    inalcançável."""
    caminho = grafo.caminho("Maria", "São Paulo")
    assert caminho is not None and len(caminho) == 3


def test_sem_ligacao_devolve_none(grafo):
    """`None` e `[]` são coisas diferentes: um é "não há caminho", o outro é
    "são a mesma entidade"."""
    assert grafo.caminho("João", "Marte") is None


def test_mesma_entidade_devolve_caminho_vazio(grafo):
    assert grafo.caminho("João", "joão") == []


def test_o_caminho_mais_curto_vence(grafo):
    """Cada salto é uma chance de a inferência escorregar; o mais curto é o
    mais confiável. Por isso busca em LARGURA, não em profundidade."""
    grafo.relacionar("João", "mora em", "São Paulo")
    caminho = grafo.caminho("João", "São Paulo")
    assert len(caminho) == 1


def test_o_teto_de_saltos_e_respeitado(grafo):
    """A partir de uns quatro saltos a conclusão deixa de ser conhecimento e
    vira jogo de seis graus de separação."""
    grafo.relacionar("São Paulo", "fica no", "Brasil")
    grafo.relacionar("Brasil", "fica na", "América")
    grafo.relacionar("América", "fica na", "Terra")
    assert grafo.caminho("Maria", "Terra", max_saltos=3) is None
    assert grafo.caminho("Maria", "Terra", max_saltos=6) is not None


def test_ciclo_nao_trava_a_busca(grafo):
    """Grafo com ciclo é normal; busca sem controle de visitados não termina."""
    grafo.relacionar("São Paulo", "emprega", "João")
    assert grafo.caminho("Maria", "Marte") is None      # termina, e sem achar


# --------------------------------------------------- procedência das arestas


def test_a_aresta_lembra_de_qual_fato_veio(grafo):
    ligacao = [r for r in grafo.relacoes_de("Acme") if r["tipo"] == "fica em"][0]
    assert ligacao["fato_id"] == grafo.fatos["acme_sp"]


def test_refutar_o_fato_derruba_a_aresta(grafo):
    """O que separa grafo de conhecimento de grafo qualquer.

    Sem isso, o usuário diria "João não trabalha mais lá", o fato perderia
    confiança, e o James continuaria concluindo que ele está em São Paulo —
    apoiado numa aresta sustentada por um fato que ele já sabe que é falso.
    """
    assert grafo.caminho("João", "São Paulo") is not None
    grafo.esquecer_relacoes_do_fato(grafo.fatos["joao_acme"])
    assert grafo.caminho("João", "São Paulo") is None


def test_apagar_o_fato_leva_a_aresta_junto(grafo):
    """`ON DELETE CASCADE` no banco — mas vale ter o teste, porque a regra é do
    esquema e um `ALTER TABLE` desatento a perderia em silêncio."""
    grafo.remove(grafo.fatos["acme_sp"])
    assert grafo.caminho("João", "São Paulo") is None


# ------------------------------------------------------------ persistência


def test_o_grafo_sobrevive_ao_processo_morrer(tmp_path):
    caminho_db = tmp_path / "f.db"
    fs = FactStore(caminho_db)
    fs.relacionar("A", "liga em", "B")
    fs.relacionar("B", "liga em", "C")
    fs.close()

    outro = FactStore(caminho_db)
    assert outro.caminho("A", "C") is not None


def test_banco_antigo_ganha_a_tabela_sem_perder_fatos(tmp_path):
    """Quem já usava o James tem fatos guardados. A migração não pode custar
    nada a eles."""
    caminho_db = tmp_path / "f.db"
    antigo = FactStore(caminho_db)
    antigo.add("um fato de antes", entidades=["coisa"])
    antigo.close()

    novo = FactStore(caminho_db)
    assert novo.search("fato de antes")
    assert novo.relacionar("coisa", "vira", "outra coisa") is True
