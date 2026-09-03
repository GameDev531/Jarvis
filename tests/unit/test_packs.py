"""O roteador de packs: menos schema por turno, sem perder capacidade.

Medido antes de existir: 33 ferramentas somavam ~3.700 tokens de schema, e iam
em todo turno. "Que horas são" pagava a conta do criador de planilha.

O risco desta camada é de uma classe específica e desagradável: **perder uma
ferramenta em silêncio**. Uma ferramenta que não está em pack nenhum nunca
chega ao modelo, e nada quebra — nenhuma exceção, nenhum teste vermelho. Ela
simplesmente deixa de existir para o James, e quando alguém notar, terá sido
"ele parou de conseguir fazer aquilo" semanas depois.

Por isso a primeira e mais importante verificação aqui não é sobre economia: é
que o conjunto dos packs cobre o catálogo real, medido no registry montado de
verdade, nos dois sentidos.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from james.config import Config
from james.memory.curated_store import MemoryStore
from james.memory.fact_store import FactStore
from james.modes import build_manager as build_modes
from james.permissions.guard import Guard
from james.skills.registry import SkillRegistry
from james.tools import build_registry
from james.ui.bus import StateBus
from james.tools.packs import (
    CORE,
    PACKS,
    escolher_packs,
    ferramentas_dos_packs,
    pack_da_ferramenta,
    packs_disponiveis,
)


def _registry_completo():
    """TUDO registrado, inclusive os modos.

    A primeira versão desta fixture não passava `modes`, e o preço foi imediato:
    o pack `navegador` citava cinco nomes que não existem (`navegador_abrir` em
    vez de `abrir_aba`), e três ferramentas de modo ficaram órfãs. Nenhuma das
    duas coisas apareceu, porque o teste de fantasmas EXCLUÍA justamente o pack
    que estava errado — ficou cego exatamente onde havia o defeito.

    Uma exceção num teste de cobertura é um buraco na cobertura. Se um pack não
    pode ser conferido, o certo é fazê-lo conferível, não pulá-lo.
    """
    raiz = Path(tempfile.mkdtemp())
    config = Config({})
    return build_registry(
        config,
        Guard(config),
        memory=MemoryStore(raiz),
        facts=FactStore(raiz / "f.db"),
        skills=SkillRegistry(raiz / "skills"),
        modes=build_modes(config, on_acao=lambda *a, **k: None, bus=StateBus()),
    )


@pytest.fixture(scope="module")
def catalogo() -> frozenset[str]:
    return frozenset(_registry_completo().names)


@pytest.fixture(scope="module")
def esquemas():
    return {s.name: s for s in _registry_completo().schemas()}


def _tamanho(esquemas, nomes) -> int:
    presentes = [esquemas[n] for n in nomes if n in esquemas]
    return len(
        json.dumps(
            [
                {"name": s.name, "description": s.description, "parameters": s.parameters}
                for s in presentes
            ],
            ensure_ascii=False,
        )
    )


# ------------------------------------------------------- a cobertura, os dois lados


def test_toda_ferramenta_registrada_esta_em_algum_pack(catalogo):
    """A que mais importa: fora de todo pack = invisível para sempre.

    E invisível em silêncio — sem exceção, sem teste vermelho, sem log. Só
    "ele parou de conseguir fazer aquilo", semanas depois.
    """
    orfas = sorted(nome for nome in catalogo if pack_da_ferramenta(nome) is None)
    assert not orfas, (
        f"ferramentas fora de todo pack, invisíveis para o modelo: {orfas}"
    )


def test_todo_nome_citado_nos_packs_existe_de_verdade(catalogo):
    """O outro lado: um erro de digitação num pack não levanta nada.

    `"consultar_fato"` em vez de `"consultar_fatos"` deixaria a ferramenta certa
    órfã E o pack apontando para o vazio, sem uma linha de aviso.
    """
    citadas = {nome for nomes in PACKS.values() for nome in nomes}
    fantasmas = sorted(citadas - catalogo)
    assert not fantasmas, f"packs citam ferramenta que não existe: {fantasmas}"


def test_nenhuma_ferramenta_em_dois_packs(catalogo):
    """Duplicar não quebra, mas esconde intenção: em qual pack ela mora?"""
    vistas: dict[str, str] = {}
    repetidas = []
    for pack, nomes in PACKS.items():
        for nome in nomes:
            if nome in vistas:
                repetidas.append(f"{nome} ({vistas[nome]} e {pack})")
            vistas[nome] = pack
    assert not repetidas, f"ferramenta em mais de um pack: {repetidas}"


def test_o_core_e_um_pedaco_pequeno_do_catalogo(esquemas, catalogo):
    """O CORE cresce por tentação — "vai que precisa" — até virar o catálogo
    inteiro com outro nome, e aí a economia some sem ninguém perceber.

    A medida é a FRAÇÃO, não a contagem: dez ferramentas curtas custam menos
    que quatro com schema grande, e é o custo que interessa. Contar itens daria
    um limite arbitrário que ou trava adição legítima ou deixa passar um schema
    gigante — e ainda se ajusta sozinho conforme o catálogo cresce.
    """
    core = _tamanho(esquemas, PACKS[CORE])
    inteiro = _tamanho(esquemas, catalogo)
    assert core / inteiro < 0.25, (
        f"CORE em {core / inteiro:.0%} do catálogo ({core} de {inteiro} ch) — "
        "ele é o piso de TODO turno"
    )


# ------------------------------------------------------------------ a escolha


@pytest.mark.parametrize(
    "frase,esperado",
    [
        ("que horas são", CORE),
        ("aumenta o volume", CORE),
        ("lembra que eu prefiro café sem açúcar", "memoria"),
        ("o que você sabe sobre o meu chefe", "memoria"),
        ("pesquisa sobre baterias de estado sólido", "web"),
        ("abre o site da receita federal", "web"),
        ("organiza a pasta de downloads", "arquivos"),
        ("faz uma apresentação sobre robótica", "escritorio"),
        ("monta uma planilha de gastos", "escritorio"),
        ("analisa a PETR4", "financas"),
        ("o que apareceu na minha tela", "visao"),
        ("olha a câmera e me diz quem está aí", "visao"),
        ("instala a habilidade de tradução", "habilidades"),
        ("clica no botão de login", "navegador"),
    ],
)
def test_a_frase_puxa_o_pack_certo(frase, esperado):
    selecao = escolher_packs(frase)
    assert esperado in selecao.packs, (
        f"'{frase}' escolheu {selecao.resumo}, faltou '{esperado}'"
    )


def test_o_core_vai_sempre():
    for frase in ("analisa a PETR4", "pesquisa sobre X", "", "qualquer coisa"):
        assert CORE in escolher_packs(frase).packs


def test_pedido_sem_gatilho_recebe_o_catalogo_inteiro():
    """Errar para menos custa a tarefa; errar para mais custa tokens.

    "faz aquilo que eu pedi ontem" não tem palavra-chave de nada — e é
    exatamente aí que cortar o catálogo seria mais perigoso.
    """
    selecao = escolher_packs("faz aquilo que eu te pedi ontem por favor")
    assert selecao.completo is True
    assert selecao.packs == frozenset(PACKS)


@pytest.mark.parametrize(
    "pergunta",
    [
        "quem foi Alan Turing",
        "me explica o que é entropia",
        "por que o céu é azul",
        "como funciona um motor de combustão",
        "vale a pena comprar um SSD agora",
    ],
)
def test_pergunta_de_conhecimento_nao_carrega_o_catalogo_inteiro(pergunta):
    """Achado do próprio benchmark: o fallback disparava onde era MENOS útil.

    Uma pergunta de conhecimento não precisa de ferramenta nenhuma, e recebia
    as 34 — o pior caso caía exatamente onde ele era menos justificável. Os
    dois destinos plausíveis de uma pergunta são procurar e lembrar; se ainda
    assim faltar, `mais_ferramentas` está no CORE.
    """
    selecao = escolher_packs(pergunta)
    assert selecao.completo is False
    assert selecao.packs == {CORE, "web", "memoria"}


@pytest.mark.parametrize(
    "frase",
    ["tudo bem com você", "bom dia James", "obrigado por tudo hoje", "boa noite senhor"],
)
def test_cortesia_nao_carrega_ferramenta_nenhuma(frase):
    """34 schemas para responder "tudo ótimo, senhor" era o caso mais absurdo."""
    assert escolher_packs(frase).packs == {CORE}


def test_a_pergunta_nao_atropela_um_gatilho_de_verdade():
    """"o que é" no começo não pode apagar o assunto que vem depois."""
    selecao = escolher_packs("o que é que tem na minha tela agora")
    assert "visao" in selecao.packs


def test_frase_curta_sem_gatilho_fica_no_core():
    """"obrigado", "beleza": não vale mandar 33 schemas para uma cortesia."""
    selecao = escolher_packs("obrigado senhor")
    assert selecao.packs == {CORE}
    assert selecao.completo is False


def test_o_modo_ligado_vale_mais_que_a_frase():
    """Quem ligou o modo navegador vai falar do navegador, com ou sem a palavra."""
    selecao = escolher_packs("volta uma página", modos_ligados=("navegador",))
    assert "navegador" in selecao.packs


def test_o_pack_pedido_pelo_modelo_entra():
    selecao = escolher_packs("obrigado", forcados=("financas",))
    assert "financas" in selecao.packs


def test_pack_inventado_e_ignorado_sem_explodir():
    selecao = escolher_packs("obrigado", forcados=("teleporte",))
    assert selecao.packs == {CORE}


def test_o_motivo_da_escolha_fica_registrado():
    """Sem o porquê, ajustar um gatilho vira adivinhação."""
    selecao = escolher_packs("analisa a PETR4 pra mim por favor")
    assert any("financas" in m for m in selecao.motivos)


# -------------------------------------------------------------- a economia real


def test_o_corte_e_real_e_grande(esquemas, catalogo):
    """Número, não promessa: "otimizou tokens" sem medida não vale nada."""
    inteiro = _tamanho(esquemas, catalogo)

    medidas = {}
    for frase in ("que horas são", "aumenta o volume", "analisa a PETR4"):
        selecao = escolher_packs(frase)
        medidas[frase] = _tamanho(esquemas, ferramentas_dos_packs(selecao.packs))

    for frase, tamanho in medidas.items():
        assert tamanho < inteiro * 0.45, (
            f"'{frase}': {tamanho} de {inteiro} caracteres — corte pequeno demais"
        )


def test_o_pior_caso_nao_e_pior_que_antes(esquemas, catalogo):
    """O fallback manda tudo — e "tudo" não pode ser mais que o catálogo."""
    selecao = escolher_packs("faz aquilo que eu te pedi ontem por favor")
    tamanho = _tamanho(esquemas, ferramentas_dos_packs(selecao.packs))
    assert tamanho <= _tamanho(esquemas, catalogo)


# ---------------------------------------------- packs oferecidos ao modelo


def test_so_oferece_pack_que_tem_ferramenta_registrada(catalogo):
    """Anunciar um pack vazio é oferecer porta para sala que não existe.

    O modelo pediria, receberia nada, e não teria como entender por quê — o
    navegador só existe com os modos ligados, as habilidades só com o registro
    de skills. Um catálogo parcial é o caso normal, não a exceção.
    """
    # Com tudo registrado, todo pack tem dono.
    for pack in packs_disponiveis(catalogo):
        assert catalogo.intersection(PACKS[pack]), f"pack vazio oferecido: {pack}"

    # E com um catálogo parcial, o pack sem ferramenta some da oferta.
    parcial = frozenset(PACKS[CORE]) | {"analisar_acao"}
    oferecidos = packs_disponiveis(parcial)
    assert "financas" in oferecidos
    assert "navegador" not in oferecidos
    assert "escritorio" not in oferecidos


def test_o_core_nao_e_oferecido(catalogo):
    """Pedir o CORE não faz sentido: ele já está sempre lá."""
    assert CORE not in packs_disponiveis(catalogo)


# ------------------------------------------------------------- não é segurança


@pytest.mark.parametrize(
    "ferramenta,args",
    [
        ("mover_arquivo", {"origem": "C:/Windows/system32/x.dll", "destino": "D:/y"}),
        ("abrir_pagina", {"url": "http://127.0.0.1:8080/admin"}),
        ("abrir_app", {"nome": "chrome malicioso"}),
        ("ler_pagina", {"url": "http://169.254.169.254/latest/meta-data/"}),
        ("organizar_arquivos", {"pasta": "C:/Windows"}),
        ("renomear_arquivo", {"origem": "../../etc/passwd", "novo_nome": "x"}),
    ],
)
def test_o_pack_nao_muda_nenhum_veredito(ferramenta, args):
    """Esconder uma ferramenta não é o que a torna segura.

    Se um dia a razão de uma ação ser segura for "aquele pack não estava
    carregado", a trava real terá sumido sem ninguém notar — e ela sumiria em
    silêncio, porque um pack a mais não levanta erro nenhum.

    O guard nem sabe que packs existem. Este teste é o que mantém assim.
    """
    config = Config({})
    guard = Guard(config)

    sozinho = guard.evaluate(ferramenta, dict(args))
    with_tudo = Guard(config).evaluate(ferramenta, dict(args))

    assert sozinho.decision is with_tudo.decision
    # E o veredito não pode depender de quais packs o turno carregou: nada na
    # assinatura do guard aceita essa informação, e é isso que se prova aqui.
    import inspect

    assinatura = inspect.signature(guard.evaluate)
    assert "pack" not in str(assinatura) and "packs" not in str(assinatura)


def test_pedir_um_pack_nao_e_acao_de_risco():
    """Carregar ferramentas não age em nada; usá-las, sim — e cada uma passa
    pelo guard na sua vez."""
    config = Config({})
    veredito = Guard(config).evaluate("mais_ferramentas", {"pack": "arquivos"})
    assert veredito.decision.value == "allow"


def test_ferramenta_de_risco_continua_de_risco_dentro_do_pack():
    """O contraprova: se tudo virasse `allow`, o teste acima não valeria nada."""
    config = Config({})
    veredito = Guard(config).evaluate(
        "mover_arquivo", {"origem": "C:/Windows/system32/kernel32.dll", "destino": "D:/"}
    )
    assert veredito.decision.value != "allow"
