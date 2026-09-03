"""De que é feito o contexto de uma requisição, e o que cada camada economiza.

    python -m james.diagnostics.bench_contexto

"Otimizou tokens" sem número é opinião. Isto monta conversas realistas com o
código de verdade e mede a composição em cada estágio:

    cru        nada ligado — como era antes
    + poda     política por ferramenta na entrada do histórico
    + packs    só os schemas do turno
    + teto     orçamento como rede de segurança

A conta é em CARACTERES. O tokenizador certo depende do modelo, e a cadeia do
James troca de modelo quando um cai — um número exato para o modelo errado
passa uma confiança que não existe. A razão antes/depois é a mesma nas duas
unidades, e é ela que interessa.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from james.config import Config
from james.llm.history import Conversation, ToolCall
from james.llm.message_builder import build_llm_context
from james.llm.orcamento import TETO_PADRAO, medir
from james.llm import resultado_policy
from james.memory.curated_store import MemoryScope, MemoryStore
from james.memory.fact_store import FactStore
from james.permissions.guard import Guard
from james.skills.registry import SkillRegistry
from james.system_prompt import build_system_prompt
from james.tools import build_registry
from james.tools.packs import escolher_packs, ferramentas_dos_packs

PAGINA = {
    "titulo": "Baterias de estado sólido chegam à produção",
    "url": "https://noticias.exemplo.com/tecnologia/baterias-estado-solido",
    "texto": (
        "A tecnologia promete densidade energética maior e menor risco de "
        "incêndio. Fabricantes anunciam linhas piloto para o ano que vem. "
    ) * 40,
    "achados": [
        {"tipo": "acessibilidade", "gravidade": "alto",
         "mensagem": "Imagem sem atributo alt.",
         "seletor": f"main > figure:nth-of-type({i}) > img"}
        for i in range(40)
    ],
}

CENARIOS: dict[str, tuple[str, int]] = {
    # frase do turno -> (ferramenta usada, quantas rodadas)
    "pesquisa longa": ("ler_pagina", 4),
    "revisão de página": ("inspecionar_pagina", 3),
    "conversa sem ferramenta": ("", 5),
    "memória": ("consultar_fatos", 3),
}

FRASES = {
    "pesquisa longa": "pesquisa mais sobre baterias de estado sólido",
    "revisão de página": "revisa essa página pra mim",
    "conversa sem ferramenta": "e o que você acha disso tudo",
    "memória": "o que você sabe sobre o meu chefe",
}


def _registry(raiz: Path):
    config = Config({})
    return build_registry(
        config, Guard(config),
        memory=MemoryStore(raiz), facts=FactStore(raiz / "f.db"),
        skills=SkillRegistry(raiz / "skills"),
    )


def _resultado_de(nome: str):
    if nome == "inspecionar_pagina":
        return {
            "snapshot_id": "a1b2c3", "tab_id": "2",
            "titulo": PAGINA["titulo"],
            "elementos": [
                {"element_id": f"e{i}", "papel": "a", "nome": f"Link número {i}"}
                for i in range(60)
            ],
            "achados": PAGINA["achados"],
            "resumo": "12 apontamentos, 4 graves.",
        }
    if nome == "consultar_fatos":
        return {"fatos": [
            {"id": i, "texto": f"Fato número {i} sobre alguma coisa relevante",
             "confianca": 0.8, "entidades": ["Chefe", "Empresa"]}
            for i in range(25)
        ]}
    return dict(PAGINA)


def _conversa(cenario: str, podar: bool) -> Conversation:
    ferramenta, rodadas = CENARIOS[cenario]
    conv = Conversation(max_turns=20)
    for i in range(rodadas):
        conv.add_user_text(f"{FRASES[cenario]} (rodada {i})")
        if ferramenta:
            conv.add_model_response("", [ToolCall(name=ferramenta, call_id=str(i))])
            dados = _resultado_de(ferramenta)
            if podar:
                conv.add_tool_result(ferramenta, dados, str(i))
            else:
                # Entra cru, como antes de existir a política.
                conv._turns.append(type(conv._turns[0])(
                    role="tool", tool_name=ferramenta, tool_result=dados, call_id=str(i)
                ))
        conv.add_model_response(
            "Aqui está o que encontrei sobre isso, com os pontos principais. " * 6
        )
    return conv


def _schemas(registry, frase: str, com_packs: bool) -> int:
    todos = registry.schemas()
    if com_packs:
        permitidas = ferramentas_dos_packs(escolher_packs(frase).packs)
        todos = [s for s in todos if s.name in permitidas]
    return len(json.dumps(
        [{"name": s.name, "description": s.description, "parameters": s.parameters}
         for s in todos],
        ensure_ascii=False,
    ))


def medir_cenario(cenario: str, registry, prompt: str) -> dict:
    frase = FRASES[cenario]
    saida = {}
    for rotulo, podar, packs, teto in (
        ("cru", False, False, None),
        ("+ poda", True, False, None),
        ("+ packs", True, True, None),
        ("+ teto", True, True, TETO_PADRAO),
    ):
        conv = _conversa(cenario, podar)
        ctx = build_llm_context(conv, frase, system_prompt=prompt, teto=teto)
        m = medir(ctx.mensagens)
        saida[rotulo] = m.total + _schemas(registry, frase, packs)
    return saida


def main() -> int:
    raiz = Path(tempfile.mkdtemp())
    memoria = MemoryStore(raiz)
    for i in range(10):
        memoria.add(MemoryScope.USER, f"preferência {i} sobre o dia a dia")
    registry = _registry(raiz)
    prompt = build_system_prompt(Config({}), memoria.snapshot())

    print(f"\n  system prompt: {len(prompt)} ch    "
          f"catálogo inteiro: {_schemas(registry, 'x', False)} ch\n")
    print(f"  {'cenário':24s} {'cru':>8s} {'+poda':>8s} {'+packs':>8s} {'+teto':>8s}   redução")
    print("  " + "-" * 68)

    totais = {"cru": 0, "+ poda": 0, "+ packs": 0, "+ teto": 0}
    for cenario in CENARIOS:
        m = medir_cenario(cenario, registry, prompt)
        for k in totais:
            totais[k] += m[k]
        reducao = 1 - m["+ teto"] / m["cru"] if m["cru"] else 0
        print(
            f"  {cenario:24s} {m['cru']:8d} {m['+ poda']:8d} "
            f"{m['+ packs']:8d} {m['+ teto']:8d}   {reducao:5.0%}"
        )

    print("  " + "-" * 68)
    reducao = 1 - totais["+ teto"] / totais["cru"] if totais["cru"] else 0
    print(
        f"  {'TOTAL':24s} {totais['cru']:8d} {totais['+ poda']:8d} "
        f"{totais['+ packs']:8d} {totais['+ teto']:8d}   {reducao:5.0%}"
    )
    print(f"\n  ~{totais['cru']//4} -> ~{totais['+ teto']//4} tokens "
          "(ordem de grandeza; ver o cabeçalho)")

    # A composição final diz onde ainda há gordura — hoje, o system prompt.
    conv = _conversa("pesquisa longa", podar=True)
    ctx = build_llm_context(conv, FRASES["pesquisa longa"],
                            system_prompt=prompt, teto=TETO_PADRAO)
    print("\n  composição de um turno típico (sem os schemas):")
    print(medir(ctx.mensagens).relatorio())
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
