"""Memória curada: markdown editável à mão, com limite por caracteres."""

import pytest

from james.memory import MemoryScope, MemoryStore
from james.memory.curated_store import MemoryFull


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memories", max_chars=1000)


def test_arquivos_sao_criados_com_cabecalho(store, tmp_path):
    pasta = tmp_path / "memories"
    assert (pasta / "MEMORY.md").exists()
    assert (pasta / "USER.md").exists()
    assert "Sobre o usuário" in (pasta / "USER.md").read_text(encoding="utf-8")


def test_guardar_e_ler_de_volta(store):
    assert store.add(MemoryScope.USER, "prefere respostas curtas") == "Guardado."
    assert "prefere respostas curtas" in store.entries(MemoryScope.USER)


def test_entrada_duplicada_nao_e_guardada_de_novo(store):
    store.add(MemoryScope.USER, "trabalha com jogos")
    assert store.add(MemoryScope.USER, "Trabalha Com Jogos") == "Isso eu já sabia."
    assert len(store.entries(MemoryScope.USER)) == 1


def test_quebras_de_linha_viram_uma_entrada_so(store):
    """Uma entrada é sempre uma linha — senão o markdown vira itens soltos."""
    store.add(MemoryScope.USER, "gosta de café\nsem açúcar")
    entradas = store.entries(MemoryScope.USER)
    assert len(entradas) == 1
    assert entradas[0] == "gosta de café sem açúcar"


def test_marcador_de_lista_do_modelo_nao_duplica(store):
    """O modelo às vezes já formata como lista; o marcador não pode dobrar."""
    store.add(MemoryScope.USER, "- usa dois monitores")
    assert store.entries(MemoryScope.USER) == ["usa dois monitores"]


def test_texto_vazio_nao_cria_entrada(store):
    for vazio in ("", "   ", "\n"):
        store.add(MemoryScope.USER, vazio)
    assert store.entries(MemoryScope.USER) == []


def test_escopos_sao_independentes(store):
    store.add(MemoryScope.USER, "prefere português")
    store.add(MemoryScope.MEMORY, "o Chrome está no disco D")
    assert store.entries(MemoryScope.USER) == ["prefere português"]
    assert store.entries(MemoryScope.MEMORY) == ["o Chrome está no disco D"]


# ------------------------------------------------------------ substituição

def test_substituir_pelo_trecho(store):
    store.add(MemoryScope.USER, "mora em São Paulo")
    assert store.replace(MemoryScope.USER, "São Paulo", "mora no Rio") == "Atualizado."
    assert store.entries(MemoryScope.USER) == ["mora no Rio"]


def test_trecho_ambiguo_nao_substitui_nada(store):
    """Trocar a entrada errada é pior que não trocar."""
    store.add(MemoryScope.USER, "gosta de café pela manhã")
    store.add(MemoryScope.USER, "gosta de café depois do almoço")
    resultado = store.replace(MemoryScope.USER, "café", "não gosta de café")
    assert "2 entradas" in resultado
    assert len(store.entries(MemoryScope.USER)) == 2


def test_substituir_o_que_nao_existe(store):
    assert "Não encontrei" in store.replace(MemoryScope.USER, "inexistente", "novo")


# ---------------------------------------------------------------- remoção

def test_remover_pelo_trecho(store):
    store.add(MemoryScope.USER, "tem um gato chamado Léo")
    assert store.remove(MemoryScope.USER, "gato") == "Esquecido."
    assert store.entries(MemoryScope.USER) == []


def test_remocao_ambigua_e_recusada(store):
    store.add(MemoryScope.USER, "gosta de rock")
    store.add(MemoryScope.USER, "gosta de jazz")
    assert "2 entradas" in store.remove(MemoryScope.USER, "gosta")
    assert len(store.entries(MemoryScope.USER)) == 2


def test_remover_o_que_nao_existe(store):
    assert "Não encontrei" in store.remove(MemoryScope.USER, "nada disso")


# ------------------------------------------------------------------ limite

def test_limite_por_caracteres_e_respeitado(tmp_path):
    """Um teto obriga a guardar só o que tem sinal alto."""
    pequeno = MemoryStore(tmp_path / "m", max_chars=300)
    with pytest.raises(MemoryFull):
        for i in range(200):
            pequeno.add(MemoryScope.USER, f"fato numero {i} com bastante texto junto")


def test_limite_minimo_e_forcado(tmp_path):
    minusculo = MemoryStore(tmp_path / "m", max_chars=1)
    assert minusculo.max_chars >= 200


# -------------------------------------------------------------- instantâneo

def test_instantaneo_traz_os_dois_escopos(store):
    store.add(MemoryScope.USER, "prefere respostas curtas")
    store.add(MemoryScope.MEMORY, "usa Windows 10")
    snapshot = store.snapshot()
    assert "prefere respostas curtas" in snapshot
    assert "usa Windows 10" in snapshot


def test_instantaneo_vazio_quando_nao_ha_nada(store):
    assert store.snapshot() == ""


def test_instantaneo_e_congelado_ate_haver_escrita(store):
    """O contexto não pode mudar sob os pés do modelo no meio da conversa."""
    store.add(MemoryScope.USER, "primeiro fato")
    primeiro = store.snapshot()
    assert store.snapshot() is primeiro or store.snapshot() == primeiro

    store.add(MemoryScope.USER, "segundo fato")
    assert "segundo fato" in store.snapshot()


# ---------------------------------------------------------------- busca

def test_busca_encontra_nos_dois_arquivos(store):
    store.add(MemoryScope.USER, "trabalha com Python")
    store.add(MemoryScope.MEMORY, "Python 3.11 instalado no PATH")
    assert len(store.search("python")) == 2


def test_busca_vazia_nao_devolve_nada(store):
    store.add(MemoryScope.USER, "qualquer coisa")
    assert store.search("") == []


# ------------------------------------------------------------- edição manual

def test_arquivo_editado_a_mao_e_lido_normalmente(store, tmp_path):
    """Poder editar à mão é uma funcionalidade, não um acidente."""
    arquivo = tmp_path / "memories" / "USER.md"
    arquivo.write_text("# Sobre\n\n- escrito à mão\n", encoding="utf-8")
    store.invalidate_snapshot()
    assert "escrito à mão" in store.entries(MemoryScope.USER)


def test_escopo_aceita_sinonimos_em_portugues():
    assert MemoryScope.parse("usuario") is MemoryScope.USER
    assert MemoryScope.parse("usuário") is MemoryScope.USER
    assert MemoryScope.parse("ambiente") is MemoryScope.MEMORY
    with pytest.raises(ValueError):
        MemoryScope.parse("inventado")
