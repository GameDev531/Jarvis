"""O perfil do navegador, e a trava que impede um `rmtree` fora do lugar.

`limpar()` chama `shutil.rmtree`. Um nome de perfil vira caminho de pasta, e
caminho aceita `..` — então o nome precisa ser validado antes de virar caminho,
não depois. Sem isso, "limpa o perfil ../../.." apagaria o repositório.

É a mesma classe de erro do `.gitignore` sem âncora e da whitelist de arquivos:
um texto que parece nome mas se comporta como caminho.
"""

from __future__ import annotations

import pytest

from james.browser.perfil import PERFIL_PADRAO, GerenteDePerfis, PerfilInvalido


@pytest.fixture
def gerente(tmp_path):
    return GerenteDePerfis(tmp_path / "browser_profiles")


# ------------------------------------------------------------------ o normal


def test_o_perfil_padrao_nasce_dentro_da_raiz(gerente):
    p = gerente.preparar()
    assert p.nome == PERFIL_PADRAO
    assert p.existe
    assert gerente.raiz.resolve() in p.caminho.parents


def test_preparar_duas_vezes_nao_estoura(gerente):
    a = gerente.preparar()
    b = gerente.preparar()
    assert a.caminho == b.caminho


def test_perfis_diferentes_ficam_em_pastas_diferentes(gerente):
    a = gerente.preparar("trabalho")
    b = gerente.preparar("pessoal")
    assert a.caminho != b.caminho
    assert {p.nome for p in gerente.listar()} == {"trabalho", "pessoal"}


def test_listar_sem_pasta_nenhuma_devolve_vazio(gerente):
    assert gerente.listar() == []


# ---------------------------------------------------------- a trava de nome


@pytest.mark.parametrize(
    "nome",
    [
        "..",
        ".",
        "../outro",
        "../../repositorio",
        "..\\..\\windows",
        "/etc",
        "C:\\Windows",
        "perfil/../../fora",
        ".oculto",
        "",
        "   ",
        "com espaço",
        "nome;rm -rf",
        "a\x00b",
    ],
)
def test_nome_que_escapa_da_pasta_e_recusado(gerente, nome):
    """Um nome que vira caminho é a mesma armadilha do `.gitignore` sem
    âncora: parece nome, se comporta como caminho."""
    with pytest.raises(PerfilInvalido):
        gerente.perfil(nome)


@pytest.mark.parametrize(
    "nome",
    [
        "jarvis-default",
        "trabalho",
        "perfil_2",
        "a1",
        # Acento passa, e deve passar: `isalnum()` é Unicode em Python, e
        # "faculdade" ou "casa-da-mãe" são nomes normais para quem escreve em
        # português. Acento não é travessia de caminho — o que a trava recusa é
        # separador e `..`, não letra estrangeira.
        "pesquisação",
        "trabalho-avô",
    ],
)
def test_nome_comum_passa(gerente, nome):
    assert gerente.perfil(nome).nome == nome
    assert gerente.preparar(nome).existe


def test_limpar_com_nome_ruim_nao_apaga_nada(gerente, tmp_path):
    """O teste que importa: a validação tem que acontecer ANTES do rmtree."""
    vitima = tmp_path / "nao_apague"
    vitima.mkdir()
    (vitima / "importante.txt").write_text("dados")

    with pytest.raises(PerfilInvalido):
        gerente.limpar("../nao_apague")

    assert (vitima / "importante.txt").exists()


# ------------------------------------------------------------------ apagar


def test_limpar_apaga_o_perfil_e_diz_quanto(gerente):
    """Um perfil persistente sem botão de apagar é acúmulo de sessão que
    ninguém controla."""
    p = gerente.preparar()
    (p.caminho / "Cookies").write_bytes(b"x" * 2048)

    frase = gerente.limpar()
    assert not p.existe
    assert "cookies" in frase.lower() or "apaguei" in frase.lower()


def test_limpar_perfil_que_nao_existe_nao_e_erro(gerente):
    """Pedir para esquecer o que já está esquecido é sucesso, não falha."""
    frase = gerente.limpar("nunca-usado")
    assert "vazio" in frase.lower()


def test_limpar_um_perfil_nao_toca_no_outro(gerente):
    a = gerente.preparar("trabalho")
    b = gerente.preparar("pessoal")
    (a.caminho / "x").write_text("1")
    (b.caminho / "y").write_text("2")

    gerente.limpar("trabalho")
    assert not a.existe
    assert (b.caminho / "y").exists()


def test_o_tamanho_e_relatado(gerente):
    p = gerente.preparar()
    (p.caminho / "grande").write_bytes(b"x" * (2 * 1024 * 1024))
    assert p.tamanho_mb >= 1.9


def test_perfil_inexistente_tem_tamanho_zero(gerente):
    assert gerente.perfil("nao-criado").tamanho_mb == 0.0
