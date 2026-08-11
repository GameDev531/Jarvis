"""Validação da whitelist de pastas — os bypasses clássicos de caminho."""

import os

import pytest

from james.permissions.paths import PathGuard, PathNotAllowed


@pytest.fixture
def sandbox(tmp_path):
    """Uma pasta permitida, uma proibida e um irmão de prefixo parecido."""
    allowed = tmp_path / "permitida"
    allowed.mkdir()
    (allowed / "sub").mkdir()
    (allowed / "sub" / "arquivo.txt").write_text("ok", encoding="utf-8")

    secret = tmp_path / "segredos"
    secret.mkdir()
    (secret / "senhas.txt").write_text("segredo", encoding="utf-8")

    # Prefixo igual ao da raiz permitida: comparação de string liberaria isto.
    sibling = tmp_path / "permitida-extra"
    sibling.mkdir()
    (sibling / "x.txt").write_text("x", encoding="utf-8")

    return tmp_path, allowed, secret, sibling


@pytest.fixture
def path_guard(sandbox):
    _, allowed, _, _ = sandbox
    return PathGuard([allowed])


def test_arquivo_dentro_da_raiz_e_permitido(path_guard, sandbox):
    _, allowed, _, _ = sandbox
    assert path_guard.is_allowed(allowed / "sub" / "arquivo.txt")


def test_a_propria_raiz_e_permitida(path_guard, sandbox):
    _, allowed, _, _ = sandbox
    assert path_guard.is_allowed(allowed)


def test_arquivo_ainda_inexistente_dentro_da_raiz_e_permitido(path_guard, sandbox):
    """Destino de um 'mover' ainda não existe — o que importa é onde ficaria."""
    _, allowed, _, _ = sandbox
    assert path_guard.is_allowed(allowed / "sub" / "novo.txt")


def test_travessia_com_ponto_ponto_e_barrada(path_guard, sandbox):
    _, allowed, _, _ = sandbox
    assert not path_guard.is_allowed(allowed / ".." / "segredos" / "senhas.txt")


def test_travessia_profunda_e_barrada(path_guard, sandbox):
    _, allowed, _, _ = sandbox
    escapada = allowed.joinpath(*([".."] * 6), "etc", "passwd")
    assert not path_guard.is_allowed(escapada)


def test_travessia_no_meio_do_caminho_e_barrada(path_guard, sandbox):
    _, allowed, _, _ = sandbox
    disfarcado = allowed / "sub" / ".." / ".." / "segredos" / "senhas.txt"
    assert not path_guard.is_allowed(disfarcado)


def test_caminho_absoluto_fora_da_raiz_e_barrado(path_guard, sandbox):
    _, _, secret, _ = sandbox
    assert not path_guard.is_allowed(secret / "senhas.txt")


def test_irmao_com_prefixo_igual_e_barrado(path_guard, sandbox):
    """'permitida' não pode liberar 'permitida-extra'."""
    _, _, _, sibling = sandbox
    assert not path_guard.is_allowed(sibling / "x.txt")


@pytest.mark.skipif(os.name == "nt", reason="symlink exige privilégio no Windows")
def test_link_simbolico_apontando_para_fora_e_barrado(path_guard, sandbox):
    """Link dentro da whitelist apontando para fora não pode virar uma porta."""
    _, allowed, secret, _ = sandbox
    link = allowed / "atalho"
    link.symlink_to(secret, target_is_directory=True)
    assert not path_guard.is_allowed(link / "senhas.txt")


def test_caminho_relativo_e_barrado(path_guard):
    """Resolver contra o CWD seria um vetor de escape."""
    for relativo in ("arquivo.txt", "sub/arquivo.txt", "./x", "../x"):
        assert not path_guard.is_allowed(relativo)


def test_caminho_vazio_e_barrado(path_guard):
    for vazio in ("", "   ", "\t"):
        assert not path_guard.is_allowed(vazio)


def test_byte_nulo_e_barrado(path_guard, sandbox):
    _, allowed, _, _ = sandbox
    assert not path_guard.is_allowed(f"{allowed}\x00.txt")


def test_sem_raiz_configurada_nada_e_permitido(sandbox):
    """Negação por padrão: whitelist vazia não libera nada."""
    _, allowed, _, _ = sandbox
    empty = PathGuard([])
    assert not empty.enabled
    assert not empty.is_allowed(allowed / "sub" / "arquivo.txt")


def test_raiz_relativa_e_descartada_e_reportada(sandbox):
    """Uma raiz relativa não serve como fronteira — é registrada e ignorada."""
    _, allowed, _, _ = sandbox
    guard = PathGuard(["pasta/relativa", allowed])
    assert guard.invalid_roots == ["pasta/relativa"]
    assert guard.enabled


def test_validate_devolve_caminho_real(path_guard, sandbox):
    _, allowed, _, _ = sandbox
    resolved = path_guard.validate(allowed / "sub" / "arquivo.txt")
    assert resolved.is_absolute()
    assert resolved.name == "arquivo.txt"


def test_validate_levanta_erro_com_motivo(path_guard, sandbox):
    _, _, secret, _ = sandbox
    with pytest.raises(PathNotAllowed):
        path_guard.validate(secret / "senhas.txt")
