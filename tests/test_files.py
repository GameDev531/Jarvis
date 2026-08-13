"""Ferramentas de arquivo: nada sai da whitelist, nada é sobrescrito."""

import pytest

from james.config import Config
from james.permissions.guard import Decision, Guard
from james.tools.files import category_for, register, unique_destination
from james.tools.registry import ToolRegistry


@pytest.fixture
def sandbox(tmp_path):
    permitida = tmp_path / "permitida"
    permitida.mkdir()
    proibida = tmp_path / "proibida"
    proibida.mkdir()
    (proibida / "segredo.txt").write_text("x", encoding="utf-8")
    return permitida, proibida


@pytest.fixture
def rig(sandbox, config_data):
    permitida, proibida = sandbox
    config_data["permissions"]["folders"] = [str(permitida)]
    config = Config(config_data)
    guard = Guard(config)
    registry = ToolRegistry()
    register(registry, config, guard)
    return registry, guard, permitida, proibida


def criar(pasta, nome, conteudo="x"):
    caminho = pasta / nome
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


# ------------------------------------------------------------ categorização

@pytest.mark.parametrize(
    "nome,categoria",
    [
        ("foto.JPG", "Imagens"),
        ("relatorio.pdf", "Documentos"),
        ("dados.xlsx", "Planilhas"),
        ("slides.pptx", "Apresentacoes"),
        ("filme.mkv", "Videos"),
        ("musica.mp3", "Audio"),
        ("backup.zip", "Compactados"),
        ("script.py", "Codigo"),
        ("coisa.qualquercoisa", "Outros"),
        ("sem_extensao", "Outros"),
    ],
)
def test_categoria_por_extensao(tmp_path, nome, categoria):
    assert category_for(tmp_path / nome) == categoria


def test_destino_unico_nao_sobrescreve(tmp_path):
    original = criar(tmp_path, "a.txt")
    assert unique_destination(original) != original
    assert unique_destination(tmp_path / "novo.txt") == tmp_path / "novo.txt"


# ----------------------------------------------------------------- guard

def test_pasta_fora_da_whitelist_e_bloqueada(rig):
    _, guard, _, proibida = rig
    assert guard.evaluate("listar_arquivos", {"pasta": str(proibida)}).decision is Decision.BLOCK


def test_travessia_na_pasta_e_bloqueada(rig):
    _, guard, permitida, _ = rig
    alvo = str(permitida / ".." / "proibida")
    assert guard.evaluate("organizar_arquivos", {"pasta": alvo}).decision is Decision.BLOCK


def test_listar_e_nivel_1(rig):
    _, guard, permitida, _ = rig
    assert guard.evaluate("listar_arquivos", {"pasta": str(permitida)}).decision is Decision.ALLOW


def test_organizar_e_nivel_2(rig):
    _, guard, permitida, _ = rig
    verdict = guard.evaluate("organizar_arquivos", {"pasta": str(permitida)})
    assert verdict.decision is Decision.CONFIRM
    assert verdict.spoken


def test_mover_valida_os_dois_lados(rig):
    """Validar só a origem deixaria passar 'mover para fora da whitelist'."""
    _, guard, permitida, proibida = rig
    origem = criar(permitida, "a.txt")

    para_fora = guard.evaluate(
        "mover_arquivo", {"origem": str(origem), "destino": str(proibida)}
    )
    assert para_fora.decision is Decision.BLOCK

    de_fora = guard.evaluate(
        "mover_arquivo",
        {"origem": str(proibida / "segredo.txt"), "destino": str(permitida)},
    )
    assert de_fora.decision is Decision.BLOCK


def test_renomear_com_caminho_no_nome_e_bloqueado(rig):
    """'../../x' num campo de nome é travessia disfarçada de renomeação."""
    _, guard, permitida, _ = rig
    caminho = criar(permitida, "a.txt")
    for nome in ("../fora.txt", "sub/dentro.txt", "..", "."):
        verdict = guard.evaluate(
            "renomear_arquivo", {"caminho": str(caminho), "novo_nome": nome}
        )
        assert verdict.decision is Decision.BLOCK


# ------------------------------------------------------------- execução

def test_organizar_move_para_subpastas(rig):
    registry, guard, permitida, _ = rig
    criar(permitida, "foto.png")
    criar(permitida, "outra.jpg")
    criar(permitida, "texto.pdf")

    verdict = guard.evaluate("organizar_arquivos", {"pasta": str(permitida)})
    resultado = registry.execute("organizar_arquivos", verdict.args)

    assert resultado.ok is True
    assert (permitida / "Imagens" / "foto.png").exists()
    assert (permitida / "Imagens" / "outra.jpg").exists()
    assert (permitida / "Documentos" / "texto.pdf").exists()


def test_organizar_nao_sobrescreve_arquivo_existente(rig):
    registry, guard, permitida, _ = rig
    destino = permitida / "Imagens"
    destino.mkdir()
    criar(destino, "foto.png", "original")
    criar(permitida, "foto.png", "novo")

    verdict = guard.evaluate("organizar_arquivos", {"pasta": str(permitida)})
    registry.execute("organizar_arquivos", verdict.args)

    assert (destino / "foto.png").read_text(encoding="utf-8") == "original"
    assert (destino / "foto (1).png").exists()


def test_organizar_ignora_arquivos_ocultos(rig):
    registry, guard, permitida, _ = rig
    criar(permitida, ".oculto.png")
    criar(permitida, "visivel.png")

    verdict = guard.evaluate("organizar_arquivos", {"pasta": str(permitida)})
    registry.execute("organizar_arquivos", verdict.args)

    assert (permitida / ".oculto.png").exists()
    assert (permitida / "Imagens" / "visivel.png").exists()


def test_organizar_pasta_vazia_nao_e_erro(rig):
    registry, guard, permitida, _ = rig
    verdict = guard.evaluate("organizar_arquivos", {"pasta": str(permitida)})
    resultado = registry.execute("organizar_arquivos", verdict.args)
    assert resultado.ok is True


def test_listar_devolve_arquivos_e_pastas(rig):
    registry, guard, permitida, _ = rig
    criar(permitida, "a.txt")
    (permitida / "sub").mkdir()

    verdict = guard.evaluate("listar_arquivos", {"pasta": str(permitida)})
    resultado = registry.execute("listar_arquivos", verdict.args)

    assert resultado.data["arquivos"] == ["a.txt"]
    assert resultado.data["pastas"] == ["sub"]


def test_mover_arquivo_de_verdade(rig):
    registry, guard, permitida, _ = rig
    origem = criar(permitida, "a.txt", "conteudo")
    destino_pasta = permitida / "sub"
    destino_pasta.mkdir()

    verdict = guard.evaluate(
        "mover_arquivo", {"origem": str(origem), "destino": str(destino_pasta)}
    )
    resultado = registry.execute("mover_arquivo", verdict.args)

    assert resultado.ok is True
    assert not origem.exists()
    assert (destino_pasta / "a.txt").read_text(encoding="utf-8") == "conteudo"


def test_renomear_arquivo_de_verdade(rig):
    registry, guard, permitida, _ = rig
    origem = criar(permitida, "antigo.txt")

    verdict = guard.evaluate(
        "renomear_arquivo", {"caminho": str(origem), "novo_nome": "novo.txt"}
    )
    resultado = registry.execute("renomear_arquivo", verdict.args)

    assert resultado.ok is True
    assert (permitida / "novo.txt").exists()
    assert not origem.exists()


def test_mover_arquivo_inexistente(rig):
    registry, guard, permitida, _ = rig
    verdict = guard.evaluate(
        "mover_arquivo",
        {"origem": str(permitida / "fantasma.txt"), "destino": str(permitida)},
    )
    # O guard aprova (o caminho está na whitelist); a tool descobre que não existe.
    resultado = registry.execute("mover_arquivo", verdict.args)
    assert resultado.ok is False


def test_sem_whitelist_configurada_nada_passa(config_data):
    """Negação por padrão também vale para arquivos."""
    config_data["permissions"]["folders"] = []
    guard = Guard(Config(config_data))
    assert guard.evaluate("listar_arquivos", {"pasta": "C:\\qualquer"}).decision is Decision.BLOCK
