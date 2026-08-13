"""Plano de vários passos: validação e encadeamento de resultados."""

import pytest

from james.agent.plan import MAX_STEPS, PlanError, parse_plan, resolve_references

TOOLS = ("pesquisar_web", "criar_planilha", "abrir_app", "listar_arquivos")


def passo(ferramenta="pesquisar_web", **extra):
    base = {"ferramenta": ferramenta, "argumentos": {}}
    base.update(extra)
    return base


# ------------------------------------------------------------- estrutura

def test_plano_valido():
    plano = parse_plan("juntar dados", [passo(), passo("abrir_app")], TOOLS)
    assert len(plano) == 2
    assert plano.objetivo == "juntar dados"


def test_objetivo_vazio_e_recusado():
    for vazio in ("", "   "):
        with pytest.raises(PlanError, match="objetivo"):
            parse_plan(vazio, [passo()], TOOLS)


def test_sem_passos_e_recusado():
    for vazio in ([], None, "não é lista"):
        with pytest.raises(PlanError):
            parse_plan("meta", vazio, TOOLS)


def test_teto_de_passos():
    with pytest.raises(PlanError, match="limite"):
        parse_plan("meta", [passo()] * (MAX_STEPS + 1), TOOLS)


def test_ferramenta_inexistente_e_recusada():
    with pytest.raises(PlanError, match="não existe"):
        parse_plan("meta", [passo("ferramenta_inventada")], TOOLS)


def test_plano_nao_pode_chamar_a_si_mesmo():
    """Sem isso, um plano recursivo faria o James girar até estourar."""
    with pytest.raises(PlanError):
        parse_plan("meta", [passo("executar_sequencia")], TOOLS)


def test_passo_que_nao_e_objeto():
    with pytest.raises(PlanError):
        parse_plan("meta", ["só um texto"], TOOLS)


def test_argumentos_que_nao_sao_objeto():
    with pytest.raises(PlanError, match="argumentos"):
        parse_plan("meta", [passo(argumentos=["a", "b"])], TOOLS)


def test_chaves_em_ingles_sao_aceitas():
    plano = parse_plan("meta", [{"tool": "abrir_app", "args": {"nome": "chrome"}}], TOOLS)
    assert plano.steps[0].tool == "abrir_app"


# -------------------------------------------------------- nomes e ordem

def test_nome_de_resultado_invalido():
    with pytest.raises(PlanError, match="inválido"):
        parse_plan("meta", [passo(salvar_como="../fora")], TOOLS)


def test_nome_de_resultado_repetido():
    with pytest.raises(PlanError, match="duas vezes"):
        parse_plan("meta", [passo(salvar_como="x"), passo(salvar_como="x")], TOOLS)


def test_referencia_a_passo_anterior_e_valida():
    plano = parse_plan(
        "meta",
        [
            passo(salvar_como="busca"),
            passo("criar_planilha", argumentos={"titulo": "{{busca.url}}"}),
        ],
        TOOLS,
    )
    assert len(plano) == 2


def test_referencia_a_passo_posterior_e_recusada():
    """Falhar aqui é melhor que falhar no meio, com passos já executados."""
    with pytest.raises(PlanError, match="nenhum passo anterior"):
        parse_plan(
            "meta",
            [
                passo("criar_planilha", argumentos={"titulo": "{{futuro}}"}),
                passo(salvar_como="futuro"),
            ],
            TOOLS,
        )


def test_referencia_a_nome_inexistente_e_recusada():
    with pytest.raises(PlanError):
        parse_plan("meta", [passo(argumentos={"q": "{{nunca_existiu}}"})], TOOLS)


def test_referencia_dentro_de_estrutura_aninhada_e_detectada():
    with pytest.raises(PlanError):
        parse_plan(
            "meta",
            [passo(argumentos={"linhas": [["a", "{{inexistente}}"]]})],
            TOOLS,
        )


# ------------------------------------------------------------ resolução

def test_referencia_exata_preserva_o_tipo():
    """`{{x}}` sozinho devolve o valor real, não a representação em texto."""
    escopo = {"dados": {"linhas": [[1, 2], [3, 4]]}}
    assert resolve_references("{{dados.linhas}}", escopo) == [[1, 2], [3, 4]]
    assert resolve_references({"n": "{{dados.linhas.0.1}}"}, escopo) == {"n": 2}


def test_referencia_no_meio_do_texto_vira_texto():
    escopo = {"busca": {"url": "https://x.com"}}
    assert resolve_references("Veja {{busca.url}} agora", escopo) == "Veja https://x.com agora"


def test_espacos_dentro_das_chaves():
    assert resolve_references("{{  a.b  }}", {"a": {"b": 7}}) == 7


def test_resolucao_em_estrutura_aninhada():
    escopo = {"r": {"arquivo": "/tmp/x.xlsx"}}
    args = {"linhas": [{"caminho": "{{r.arquivo}}"}], "titulo": "fixo"}
    assert resolve_references(args, escopo) == {
        "linhas": [{"caminho": "/tmp/x.xlsx"}],
        "titulo": "fixo",
    }


def test_referencia_inexistente_levanta_erro():
    """Deixar `{{x}}` passar como literal viraria um caminho absurdo."""
    with pytest.raises(PlanError, match="Não existe um resultado"):
        resolve_references("{{fantasma}}", {})


def test_campo_inexistente_levanta_erro():
    with pytest.raises(PlanError, match="não tem o campo"):
        resolve_references("{{r.faltando}}", {"r": {"existe": 1}})


def test_indice_fora_da_lista():
    with pytest.raises(PlanError, match="não tem o item"):
        resolve_references("{{r.9}}", {"r": [1, 2]})


def test_nao_alcanca_atributos_de_objeto():
    """Permitir atributo deixaria o texto do modelo entrar nas nossas estruturas."""

    class Coisa:
        segredo = "não deveria sair"

    with pytest.raises(PlanError):
        resolve_references("{{r.segredo}}", {"r": Coisa()})


def test_valores_nao_textuais_passam_intactos():
    assert resolve_references({"n": 42, "b": True, "z": None}, {}) == {
        "n": 42,
        "b": True,
        "z": None,
    }


def test_texto_sem_referencia_nao_muda():
    assert resolve_references("nada aqui", {}) == "nada aqui"


def test_chave_solta_nao_e_referencia():
    """'{{' sem fechamento é texto comum, não referência quebrada."""
    assert resolve_references("valor {{ incompleto", {}) == "valor {{ incompleto"


def test_aninhamento_excessivo_e_recusado():
    profundo = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "x"}}}}}}}
    with pytest.raises(PlanError, match="aninhados"):
        resolve_references(profundo, {})
