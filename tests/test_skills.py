"""Habilidades: cabeçalho, busca, carregamento e travas da instalação."""

import pytest

from james.skills.installer import SkillInstallError, validar
from james.skills.registry import SkillRegistry, parse_skill_file

FONTES = ["anthropics/skills", "vercel-labs/agent-skills"]


def criar_skill(base, nome, descricao="uma descrição", corpo="# Conteúdo\n\nTexto."):
    pasta = base / nome
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "SKILL.md").write_text(
        f"---\nname: {nome}\ndescription: {descricao}\n---\n{corpo}\n", encoding="utf-8"
    )
    return pasta


@pytest.fixture
def registry(tmp_path):
    base = tmp_path / "skills"
    base.mkdir()
    criar_skill(base, "planilhas", "Como estruturar uma planilha útil")
    criar_skill(base, "graficos-3d", "Cenas Three.js com estética de holograma")
    return SkillRegistry(base)


# ------------------------------------------------------------- cabeçalho

def test_cabecalho_e_corpo_sao_separados():
    metadados, corpo = parse_skill_file("---\nname: x\ndescription: y\n---\n# Título\ntexto")
    assert metadados == {"name": "x", "description": "y"}
    assert corpo.startswith("# Título")


def test_arquivo_sem_cabecalho_e_todo_corpo():
    metadados, corpo = parse_skill_file("# Só conteúdo")
    assert metadados == {}
    assert corpo == "# Só conteúdo"


def test_cabecalho_ilegivel_nao_derruba():
    metadados, corpo = parse_skill_file("---\n: : : inválido :\n---\ncorpo")
    assert isinstance(metadados, dict)
    assert corpo == "corpo"


def test_cabecalho_que_nao_e_mapeamento():
    metadados, _ = parse_skill_file("---\n- item\n- outro\n---\ncorpo")
    assert metadados == {}


# ---------------------------------------------------------------- listagem

def test_lista_as_habilidades(registry):
    nomes = [s.nome for s in registry.list_all()]
    assert nomes == ["graficos-3d", "planilhas"]


def test_descricao_vem_do_cabecalho(registry):
    planilhas = registry.get("planilhas")
    assert "planilha" in planilhas.descricao.lower()


def test_busca_por_nome_pesa_mais_que_descricao(tmp_path):
    base = tmp_path / "s"
    base.mkdir()
    criar_skill(base, "planilhas", "assunto qualquer")
    criar_skill(base, "outra", "fala sobre planilhas também")

    resultados = SkillRegistry(base).search("planilhas")
    assert resultados[0].nome == "planilhas"


def test_busca_sem_acento(tmp_path):
    base = tmp_path / "s"
    base.mkdir()
    criar_skill(base, "graficos", "Como fazer gráficos")
    assert SkillRegistry(base).search("graficos") != []


def test_busca_vazia_nao_devolve_nada(registry):
    assert registry.search("") == []
    assert registry.search("a") == []


def test_habilidade_com_nome_invalido_e_ignorada(tmp_path):
    """O nome vira pasta: separador de caminho aqui seria travessia."""
    base = tmp_path / "s"
    base.mkdir()
    pasta = base / "suspeita"
    pasta.mkdir()
    (pasta / "SKILL.md").write_text(
        "---\nname: ../../fora\ndescription: x\n---\ncorpo", encoding="utf-8"
    )
    assert SkillRegistry(base).list_all() == []


def test_pasta_inexistente_nao_quebra(tmp_path):
    assert SkillRegistry(tmp_path / "nao-existe").list_all() == []


# -------------------------------------------------------------- carregar

def test_carregar_devolve_o_corpo(registry):
    conteudo = registry.load("planilhas")
    assert "# Conteúdo" in conteudo
    # O cabeçalho não entra: ele já foi mostrado na listagem.
    assert "description:" not in conteudo


def test_carregar_habilidade_inexistente(registry):
    assert registry.load("nao-existe") is None


def test_carregar_ignora_caixa_e_acento(tmp_path):
    base = tmp_path / "s"
    base.mkdir()
    criar_skill(base, "graficos", "x")
    assert SkillRegistry(base).load("GRAFICOS") is not None


def test_conteudo_gigante_e_truncado(tmp_path):
    base = tmp_path / "s"
    base.mkdir()
    criar_skill(base, "grande", "x", corpo="linha\n" * 5000)
    conteudo = SkillRegistry(base).load("grande")
    assert "truncada" in conteudo
    assert len(conteudo) < 13000


def test_invisiveis_sao_removidos_ao_carregar(tmp_path):
    """Habilidade remota é conteúdo de terceiros entrando no contexto."""
    base = tmp_path / "s"
    base.mkdir()
    criar_skill(base, "suspeita", "x", corpo="texto​escondido‮")
    conteudo = SkillRegistry(base).load("suspeita")
    assert "​" not in conteudo and "‮" not in conteudo


def test_habilidade_nova_aparece_sem_reiniciar(tmp_path):
    base = tmp_path / "s"
    base.mkdir()
    registry = SkillRegistry(base)
    assert registry.list_all() == []

    criar_skill(base, "nova", "recém-criada")
    assert [s.nome for s in registry.list_all()] == ["nova"]


# ------------------------------------------------------------ instalação

@pytest.mark.parametrize("fonte", ["anthropics/skills", "vercel-labs/agent-skills"])
def test_fonte_confiavel_passa(fonte):
    assert validar(fonte, "img2threejs", FONTES) == (fonte, "img2threejs")


def test_fonte_desconhecida_e_recusada():
    """Baixar habilidade é baixar instruções de terceiros para seguir."""
    with pytest.raises(SkillInstallError, match="confiáveis"):
        validar("aleatorio/repo", "qualquer", FONTES)


@pytest.mark.parametrize(
    "fonte",
    [
        "https://evil.com/repo",
        "../../etc",
        "sem-barra",
        "dono/repo; rm -rf /",
        "",
        "a/b/c",
    ],
)
def test_fonte_malformada_e_recusada(fonte):
    with pytest.raises(SkillInstallError):
        validar(fonte, "nome", FONTES)


@pytest.mark.parametrize("nome", ["../fora", "com/barra", "", "nome com espaco", "a" * 80])
def test_nome_de_habilidade_invalido(nome):
    with pytest.raises(SkillInstallError):
        validar("anthropics/skills", nome, FONTES)


def test_sem_fontes_configuradas_nada_instala():
    """Negação por padrão também aqui."""
    with pytest.raises(SkillInstallError, match="Nenhuma fonte confiável"):
        validar("anthropics/skills", "x", [])


def test_comparacao_de_fonte_ignora_caixa():
    assert validar("Anthropics/Skills", "x", FONTES)[0] == "Anthropics/Skills"
