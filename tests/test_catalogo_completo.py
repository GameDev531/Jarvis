"""O catálogo inteiro monta, e todo handler degrada com elegância.

Este arquivo nasceu de uma auditoria de cobertura com um resultado
desconfortável: 786 testes verdes, e **nenhum deles chamava um handler de
ferramenta**. Os testes cobriam o guard (que decide se pode), os armazéns de
memória (que guardam), o cliente de LLM (que fala com a nuvem) — mas não a cola
entre eles, que é justamente onde o modelo encosta no sistema.

`build_registry` também nunca era chamado. Um erro no registro de qualquer
ferramenta — schema inválido, nome duplicado, dependência faltando — passaria
por toda a suíte e só apareceria quando alguém rodasse o James.

Os testes aqui são rasos de propósito. Não verificam o que cada ferramenta faz
(isso é dos arquivos específicos); verificam que o catálogo **existe**, que cada
peça **responde** e que ninguém **estoura** quando o modelo manda algo torto — o
que o modelo faz o tempo todo.
"""

from __future__ import annotations

import pytest

from james.config import load_config
from james.memory import MemoryStore
from james.memory.fact_store import FactStore
from james.modes import build_manager
from james.permissions.guard import Guard
from james.skills import SkillRegistry
from james.tools import build_registry
from james.tools.registry import ToolResult
from james.ui.bus import StateBus


@pytest.fixture(scope="module")
def catalogo(tmp_path_factory):
    """O catálogo completo, montado como o orquestrador monta."""
    tmp = tmp_path_factory.mktemp("catalogo")
    config = load_config()
    bus = StateBus()
    registry = build_registry(
        config,
        Guard(config),
        MemoryStore(tmp / "memories"),
        facts=FactStore(tmp / "fatos.db"),
        skills=SkillRegistry(tmp / "skills"),
        modes=build_manager(config, on_acao=lambda *a: None, bus=bus),
        bus=bus,
    )
    yield registry
    bus.close()


# ------------------------------------------------------------- o catálogo


def test_catalogo_monta(catalogo):
    """Se `build_registry` estourar, o James não sobe. Nada testava isso."""
    assert len(catalogo.names) >= 30


def test_nao_ha_ferramenta_duplicada(catalogo):
    assert len(catalogo.names) == len(set(catalogo.names))


def test_toda_ferramenta_tem_handler_e_descricao(catalogo):
    for nome in catalogo.names:
        tool = catalogo.get(nome)
        assert callable(tool.handler), f"{nome} sem handler"
        assert tool.description.strip(), f"{nome} sem descrição"
        # A descrição é o que o modelo lê para decidir. Curta demais não decide.
        assert len(tool.description) > 25, f"{nome}: descrição curta demais"


def test_todo_schema_e_json_schema_valido(catalogo):
    """Schema torto faz o provedor recusar a requisição inteira — todas as
    ferramentas vão junto, não só a defeituosa."""
    for nome in catalogo.names:
        schema = catalogo.get(nome).parameters
        assert schema.get("type") == "object", f"{nome}: type != object"
        propriedades = schema.get("properties", {})
        assert isinstance(propriedades, dict), f"{nome}: properties não é objeto"

        for campo, definicao in propriedades.items():
            assert "type" in definicao, f"{nome}.{campo}: sem type"
            assert definicao.get("description"), f"{nome}.{campo}: sem description"
            # Enum vazio recusa qualquer valor e alguns provedores rejeitam a
            # ferramenta inteira por causa dele.
            if "enum" in definicao:
                assert definicao["enum"], f"{nome}.{campo}: enum vazio"

        for obrigatorio in schema.get("required", []):
            assert obrigatorio in propriedades, (
                f"{nome}: '{obrigatorio}' é obrigatório mas não está em properties"
            )


def test_toda_ferramenta_do_catalogo_e_conhecida_pelo_guard(catalogo):
    """Ferramenta fora do guard é bloqueada por padrão — existe no catálogo,
    o modelo a escolhe, e ela nunca roda. Falha silenciosa das piores."""
    conhecidas = set(Guard(load_config()).known_tools)
    orfas = [nome for nome in catalogo.names if nome not in conhecidas]
    assert not orfas, f"ferramentas sem regra no guard: {orfas}"


def test_toda_regra_do_guard_tem_ferramenta(catalogo):
    """O contrário também importa: regra órfã é sinal de ferramenta removida
    pela metade, e mascara um erro de digitação no nome."""
    do_catalogo = set(catalogo.names)
    orfas = [n for n in Guard(load_config()).known_tools if n not in do_catalogo]
    assert not orfas, f"regras do guard sem ferramenta correspondente: {orfas}"


# --------------------------------------------------- robustez dos handlers


def test_nenhum_handler_estoura_com_argumentos_vazios(catalogo):
    """O modelo esquece argumento obrigatório o tempo todo.

    A resposta certa é um ToolResult com `ok=False`, que vira uma frase; um
    traceback derrubaria o turno e o James ficaria mudo no meio da conversa.
    """
    quebrados = []
    for nome in catalogo.names:
        try:
            resultado = catalogo.get(nome).handler({})
        except Exception as exc:  # noqa: BLE001 — é exatamente o que se procura
            quebrados.append(f"{nome}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(resultado, ToolResult):
            quebrados.append(f"{nome}: devolveu {type(resultado).__name__}")
    assert not quebrados, "handlers que não degradam:\n  " + "\n  ".join(quebrados)


@pytest.mark.parametrize(
    "lixo",
    [
        {"texto": None},
        {"texto": 12345},
        {"texto": ["lista", "onde", "esperava", "texto"]},
        {"texto": {"objeto": "onde esperava texto"}},
        {"campo_que_nao_existe": "valor"},
        {"texto": "x" * 5000},
    ],
    ids=["nulo", "numero", "lista", "objeto", "campo-inexistente", "texto-enorme"],
)
def test_nenhum_handler_estoura_com_tipo_errado(catalogo, lixo):
    """Argumento de tipo errado chega de dois lugares: o modelo alucinando o
    schema, e uma injeção vinda de página web. Nenhum dos dois pode derrubar."""
    quebrados = []
    for nome in catalogo.names:
        try:
            catalogo.get(nome).handler(dict(lixo))
        except Exception as exc:  # noqa: BLE001
            quebrados.append(f"{nome}: {type(exc).__name__}: {str(exc)[:60]}")
    assert not quebrados, "handlers frágeis:\n  " + "\n  ".join(quebrados)


def test_ferramentas_de_efeito_previsivel_sao_fire_and_forget(catalogo):
    """`fire_and_forget` é o que economiza metade das requisições.

    Se uma ferramenta de resultado previsível perder a marca, o James passa a
    pagar dois ciclos de API por comando trivial — e a cota diária, que já é
    apertada, cai pela metade sem ninguém notar.
    """
    previsiveis = [
        "que_horas_sao", "lembrar", "esquecer", "ajustar_volume",
        "ativar_modo", "desativar_modo", "projetar_holograma",
    ]
    for nome in previsiveis:
        tool = catalogo.get(nome)
        if tool is not None:
            assert tool.fire_and_forget is True, f"{nome} perdeu fire_and_forget"


def test_ferramentas_de_resultado_imprevisivel_pagam_o_segundo_ciclo(catalogo):
    """O oposto: analisar tela sem o segundo ciclo devolveria a descrição crua
    em vez de uma resposta ao que foi perguntado."""
    for nome in ("ver_tela", "ver_camera", "ler_pagina", "pesquisa_aprofundada"):
        tool = catalogo.get(nome)
        if tool is not None:
            assert tool.fire_and_forget is False, f"{nome} não deveria ser F&F"


def test_schemas_viram_o_formato_do_provedor(catalogo):
    """`schemas()` é o que efetivamente vai na requisição."""
    esquemas = catalogo.schemas()
    assert len(esquemas) == len(catalogo.names)
    for esquema in esquemas:
        assert esquema.name and esquema.description
        assert isinstance(esquema.parameters, dict)
