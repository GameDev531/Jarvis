"""Memória profunda: fatos, entidades, busca e grau de confiança."""

import pytest

from james.memory.fact_store import (
    CONFIANCA_INICIAL,
    PISO_DE_CONFIANCA,
    FactStore,
    FactStoreFull,
    _sobreposicao,
)


@pytest.fixture
def store(tmp_path):
    banco = FactStore(tmp_path / "fatos.db")
    yield banco
    banco.close()


# ------------------------------------------------------------------ escrita

def test_guardar_e_encontrar(store):
    fato = store.add("O projeto James roda em Windows", ["James", "Windows"])
    assert fato is not None and fato.id > 0
    assert store.search("James")[0].texto == "O projeto James roda em Windows"


def test_confianca_inicial(store):
    assert store.add("qualquer fato aqui").confianca == CONFIANCA_INICIAL


def test_texto_vazio_nao_guarda(store):
    for vazio in ("", "   ", "\n"):
        assert store.add(vazio) is None


def test_quebras_de_linha_viram_uma_linha(store):
    fato = store.add("primeira linha\nsegunda linha")
    assert "\n" not in fato.texto


def test_texto_gigante_e_truncado(store):
    fato = store.add("a" * 900)
    assert len(fato.texto) <= 505


def test_duplicata_exata_vira_confirmacao(store):
    """Repetir um fato é evidência de que ele é verdade, não lixo."""
    primeiro = store.add("o gato se chama Léo")
    assert store.add("O GATO SE CHAMA LÉO") is None

    encontrados = store.search("gato")
    assert len(encontrados) == 1
    assert encontrados[0].confianca > primeiro.confianca


def test_limite_de_fatos(tmp_path):
    banco = FactStore(tmp_path / "f.db", max_fatos=12)
    try:
        with pytest.raises(FactStoreFull):
            for i in range(50):
                banco.add(f"fato numero {i}")
    finally:
        banco.close()


def test_remover(store):
    fato = store.add("fato temporário")
    assert store.remove(fato.id) is True
    assert store.search("temporário") == []
    assert store.remove(fato.id) is False


# ------------------------------------------------------------------- busca

def test_busca_ignora_acento(store):
    """Em português isso não é conveniência, é requisito."""
    store.add("prefere café sem açúcar")
    assert store.search("cafe") != []
    assert store.search("acucar") != []


def test_busca_ignora_maiusculas(store):
    store.add("Trabalha com Python")
    assert store.search("PYTHON") != []


def test_busca_vazia_nao_devolve_nada(store):
    store.add("qualquer coisa")
    assert store.search("") == []
    assert store.search("   ") == []


def test_operadores_do_fts_nao_quebram_a_busca(store):
    """A consulta vem do usuário e não pode virar sintaxe do motor de busca."""
    store.add("reunião sobre orçamento")
    for consulta in ('reuniao NEAR/2 x', 'orcamento*', '-reuniao', 'a:b', '"aspas"'):
        store.search(consulta)   # não pode levantar exceção


def test_fato_refutado_some_da_busca(store):
    fato = store.add("mora em São Paulo")
    store.refute(fato.id)
    store.refute(fato.id)
    assert store.search("São Paulo") == []


def test_fato_refutado_nao_e_apagado(store):
    """Refutar por engano não deveria destruir a informação."""
    fato = store.add("mora em São Paulo")
    store.refute(fato.id)
    store.refute(fato.id)
    assert store.search("São Paulo", incluir_duvidosos=True) != []


def test_mais_confiavel_vem_primeiro(store):
    baixo = store.add("café é ruim pela manhã")
    alto = store.add("café é ótimo pela manhã")
    for _ in range(4):
        store.confirm(alto.id)
    store.refute(baixo.id)

    resultados = store.search("café manhã")
    assert resultados[0].id == alto.id


def test_limite_de_resultados(store):
    for i in range(20):
        store.add(f"nota sobre projeto numero {i}")
    assert len(store.search("projeto", limite=5)) == 5


# -------------------------------------------------------------- confiança

def test_confirmar_aproxima_de_um_sem_chegar(store):
    """Certeza absoluta não existe — a curva é assintótica."""
    fato = store.add("fato repetido")
    valor = fato.confianca
    for _ in range(50):
        valor = store.confirm(fato.id)
    assert 0.99 < valor < 1.0


def test_refutar_corta_pela_metade(store):
    fato = store.add("fato duvidoso")
    assert store.refute(fato.id) == pytest.approx(CONFIANCA_INICIAL / 2)


def test_duas_refutacoes_tiram_do_piso(store):
    fato = store.add("fato errado")
    store.refute(fato.id)
    assert store.refute(fato.id) < PISO_DE_CONFIANCA


def test_ajustar_fato_inexistente(store):
    assert store.confirm(9999) is None
    assert store.refute(9999) is None


# -------------------------------------------------------------- entidades

def test_probe_traz_tudo_sobre_a_entidade(store):
    store.add("Ana trabalha com design", ["Ana"])
    store.add("Ana mora no Rio", ["Ana", "Rio"])
    store.add("Bruno trabalha com dados", ["Bruno"])

    textos = [f.texto for f in store.probe("Ana")]
    assert len(textos) == 2
    assert all("Ana" in t for t in textos)


def test_probe_ignora_acento_e_caixa(store):
    store.add("fato sobre a entidade", ["São Paulo"])
    assert store.probe("sao paulo") != []
    assert store.probe("SÃO PAULO") != []


def test_probe_de_entidade_inexistente(store):
    assert store.probe("ninguém") == []
    assert store.probe("") == []


def test_entidade_repetida_nao_duplica(store):
    store.add("primeiro fato", ["Ana"])
    store.add("segundo fato", ["ana"])   # mesma entidade, caixa diferente
    assert store.stats()["entidades"] == 1


def test_related_encontra_quem_aparece_junto(store):
    store.add("Ana e Bruno trabalham no projeto X", ["Ana", "Bruno", "Projeto X"])
    store.add("Ana lidera o projeto X", ["Ana", "Projeto X"])

    relacionadas = dict(store.related("Ana"))
    assert relacionadas["Projeto X"] == 2
    assert relacionadas["Bruno"] == 1


def test_related_nao_inclui_a_propria_entidade(store):
    store.add("fato", ["Ana", "Bruno"])
    assert "Ana" not in dict(store.related("Ana"))


def test_fato_sem_entidade_e_valido(store):
    fato = store.add("uma observação solta")
    assert fato.entidades == []
    assert store.search("observação") != []


# ------------------------------------------------------------ contradições

def test_sobreposicao_de_textos():
    assert _sobreposicao("mora no Rio", "mora no Rio") == pytest.approx(1.0)
    assert _sobreposicao("mora no Rio", "gosta de pizza") == pytest.approx(0.0)
    assert 0 < _sobreposicao("Ana mora no Rio", "Ana mora em Recife") < 1


def test_candidatos_a_contradicao(store):
    """SQL acha os candidatos; julgar se contradizem é do modelo."""
    store.add("Ana mora no Rio de Janeiro", ["Ana"])
    store.add("Ana mora em Recife hoje", ["Ana"])
    store.add("Bruno gosta de jazz", ["Bruno"])

    pares = store.contradictions()
    assert len(pares) >= 1
    textos = {p[0].texto for p in pares} | {p[1].texto for p in pares}
    assert "Bruno gosta de jazz" not in textos


def test_fatos_identicos_nao_sao_contradicao(store):
    """Sobreposição altíssima é repetição, não conflito."""
    store.add("Ana mora no Rio", ["Ana"])
    store.add("Ana mora no Rio de Janeiro sim", ["Ana"])
    for par in store.contradictions():
        assert _sobreposicao(par[0].texto, par[1].texto) < 0.95


def test_fatos_sem_entidade_comum_nao_sao_comparados(store):
    store.add("Ana mora no Rio", ["Ana"])
    store.add("Bruno mora no Rio", ["Bruno"])
    assert store.contradictions() == []


# ----------------------------------------------------------- persistência

def test_dados_sobrevivem_ao_reinicio(tmp_path):
    caminho = tmp_path / "persistente.db"
    primeiro = FactStore(caminho)
    primeiro.add("fato que precisa durar", ["Teste"])
    primeiro.close()

    segundo = FactStore(caminho)
    try:
        assert segundo.search("durar") != []
        assert segundo.probe("Teste") != []
    finally:
        segundo.close()


def test_remover_fato_limpa_os_vinculos(tmp_path):
    banco = FactStore(tmp_path / "f.db")
    try:
        fato = banco.add("fato com entidade", ["Alvo"])
        banco.remove(fato.id)
        assert banco.probe("Alvo") == []
    finally:
        banco.close()


def test_estatisticas(store):
    store.add("primeiro", ["A"])
    store.add("segundo", ["A", "B"])
    estatisticas = store.stats()
    assert estatisticas["fatos"] == 2
    assert estatisticas["entidades"] == 2
    assert estatisticas["busca_textual"] is True


def test_listar_recentes_vem_do_mais_novo(store):
    store.add("mais antigo")
    store.add("mais novo")
    assert store.list_recent()[0].texto == "mais novo"
