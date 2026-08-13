"""Geração de apresentações e planilhas — renderização determinística."""

import pytest

from james.config import Config
from james.permissions.guard import Decision, Guard
from james.tools.office import _coerce, _normalize_slides, _slugify, register
from james.tools.registry import ToolRegistry

pptx = pytest.importorskip("pptx", reason="python-pptx não instalado")
openpyxl = pytest.importorskip("openpyxl", reason="openpyxl não instalado")


@pytest.fixture
def rig(tmp_path, config_data):
    saida = tmp_path / "documentos"
    saida.mkdir()
    config_data["permissions"]["folders"] = [str(saida)]
    config_data["office"] = {"output_dir": str(saida)}
    config = Config(config_data)
    guard = Guard(config)
    registry = ToolRegistry()
    register(registry, config, guard)
    return registry, guard, saida


# ------------------------------------------------------------ normalização

def test_slides_como_dicionarios():
    slides = _normalize_slides([{"titulo": "Intro", "topicos": ["a", "b"]}])
    assert slides == [{"titulo": "Intro", "topicos": ["a", "b"]}]


def test_slides_como_texto_puro():
    """Modelos às vezes devolvem só os títulos."""
    assert _normalize_slides(["Primeiro", "Segundo"]) == [
        {"titulo": "Primeiro", "topicos": []},
        {"titulo": "Segundo", "topicos": []},
    ]


def test_chaves_em_ingles_sao_aceitas():
    slides = _normalize_slides([{"title": "Intro", "bullets": ["a"]}])
    assert slides[0]["titulo"] == "Intro" and slides[0]["topicos"] == ["a"]


def test_topicos_como_texto_com_quebras():
    slides = _normalize_slides([{"titulo": "T", "topicos": "linha um\nlinha dois"}])
    assert slides[0]["topicos"] == ["linha um", "linha dois"]


def test_marcador_de_lista_e_removido():
    slides = _normalize_slides([{"titulo": "T", "topicos": ["- um", "* dois", "• três"]}])
    assert slides[0]["topicos"] == ["um", "dois", "três"]


def test_slides_vazios_sao_descartados():
    assert _normalize_slides([{}, {"titulo": "", "topicos": []}, None, 42]) == []


def test_teto_de_slides_e_topicos():
    slides = _normalize_slides([{"titulo": f"S{i}", "topicos": [f"t{j}" for j in range(50)]}
                                for i in range(100)])
    assert len(slides) <= 40
    assert all(len(s["topicos"]) <= 8 for s in slides)


def test_nome_de_arquivo_seguro():
    assert "/" not in _slugify("Relatório: 2026/2027 <final>", "x")
    assert _slugify("///", "padrao") == "padrao"


# ---------------------------------------------------------------- números

@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("42", 42),
        ("3.5", 3.5),
        ("1.234", 1234),          # separador de milhar brasileiro
        ("1.234,56", 1234.56),
        ("R$ 1.500,00", 1500),
        ("texto", "texto"),
        ("", ""),
        (7, 7),
        (None, None),
    ],
)
def test_conversao_de_valores(entrada, esperado):
    """Números precisam virar número, senão o Excel não soma nem plota."""
    assert _coerce(entrada) == esperado


# ----------------------------------------------------------------- guard

def test_criar_documento_e_nivel_1(rig):
    """Arquivo NOVO, sem sobrescrever nada: reversível."""
    _, guard, _ = rig
    verdict = guard.evaluate("criar_apresentacao", {"titulo": "Plano", "slides": []})
    assert verdict.decision is Decision.ALLOW


def test_titulo_vazio_e_bloqueado(rig):
    _, guard, _ = rig
    assert guard.evaluate("criar_planilha", {"titulo": "  "}).decision is Decision.BLOCK


def test_sem_pasta_permitida_e_bloqueado(config_data):
    config_data["permissions"]["folders"] = []
    guard = Guard(Config(config_data))
    verdict = guard.evaluate("criar_apresentacao", {"titulo": "Plano"})
    assert verdict.decision is Decision.BLOCK


# ------------------------------------------------------------ apresentação

def test_apresentacao_e_criada(rig):
    registry, _, saida = rig
    resultado = registry.execute(
        "criar_apresentacao",
        {
            "titulo": "Plano de 2026",
            "subtitulo": "Visão geral",
            "slides": [
                {"titulo": "Contexto", "topicos": ["ponto um", "ponto dois"]},
                {"titulo": "Próximos passos", "topicos": ["fazer X"]},
            ],
        },
    )
    assert resultado.ok is True
    arquivos = list(saida.glob("*.pptx"))
    assert len(arquivos) == 1

    prs = pptx.Presentation(str(arquivos[0]))
    assert len(prs.slides) == 3   # capa + dois de conteúdo


def test_apresentacao_sem_slides_e_recusada(rig):
    registry, _, _ = rig
    assert registry.execute("criar_apresentacao", {"titulo": "X", "slides": []}).ok is False


def test_dois_arquivos_com_mesmo_titulo_nao_se_sobrescrevem(rig):
    registry, _, saida = rig
    args = {"titulo": "Igual", "slides": [{"titulo": "A", "topicos": []}]}
    registry.execute("criar_apresentacao", args)
    registry.execute("criar_apresentacao", args)
    assert len(list(saida.glob("*.pptx"))) == 2


# --------------------------------------------------------------- planilha

def test_planilha_e_criada_com_cabecalho(rig):
    registry, _, saida = rig
    resultado = registry.execute(
        "criar_planilha",
        {
            "titulo": "Vendas",
            "colunas": ["Mês", "Total"],
            "linhas": [["Janeiro", "1.500,00"], ["Fevereiro", "2300"]],
        },
    )
    assert resultado.ok is True
    arquivos = list(saida.glob("*.xlsx"))
    assert len(arquivos) == 1

    wb = openpyxl.load_workbook(str(arquivos[0]))
    ws = wb.active
    assert [c.value for c in ws[1]] == ["Mês", "Total"]
    # O valor textual virou número de verdade.
    assert ws.cell(row=2, column=2).value == 1500


def test_linhas_como_registros(rig):
    """Modelos às vezes devolvem dicionários em vez de listas."""
    registry, _, saida = rig
    registry.execute(
        "criar_planilha",
        {
            "titulo": "Registros",
            "colunas": ["nome", "idade"],
            "linhas": [{"nome": "Ana", "idade": 30}, {"nome": "Léo", "idade": 25}],
        },
    )
    wb = openpyxl.load_workbook(str(next(saida.glob("*.xlsx"))))
    ws = wb.active
    assert ws.cell(row=2, column=1).value == "Ana"
    assert ws.cell(row=3, column=2).value == 25


def test_planilha_com_grafico(rig):
    registry, _, saida = rig
    resultado = registry.execute(
        "criar_planilha",
        {
            "titulo": "Com gráfico",
            "colunas": ["Mês", "Total"],
            "linhas": [["Jan", 10], ["Fev", 20]],
            "grafico": {"tipo": "barra", "titulo": "Evolução"},
        },
    )
    assert resultado.ok is True
    wb = openpyxl.load_workbook(str(next(saida.glob("*.xlsx"))))
    assert len(wb.active._charts) == 1


def test_planilha_sem_colunas_e_recusada(rig):
    registry, _, _ = rig
    assert registry.execute("criar_planilha", {"titulo": "X", "colunas": []}).ok is False


def test_linhas_em_formato_invalido(rig):
    registry, _, _ = rig
    resultado = registry.execute(
        "criar_planilha", {"titulo": "X", "colunas": ["A"], "linhas": "não é lista"}
    )
    assert resultado.ok is False


def test_linha_com_mais_valores_que_colunas_e_truncada(rig):
    registry, _, saida = rig
    registry.execute(
        "criar_planilha",
        {"titulo": "Extra", "colunas": ["A", "B"], "linhas": [[1, 2, 3, 4, 5]]},
    )
    wb = openpyxl.load_workbook(str(next(saida.glob("*.xlsx"))))
    assert wb.active.max_column == 2
