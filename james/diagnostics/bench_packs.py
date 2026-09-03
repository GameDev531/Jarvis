"""Quanto o roteador de packs economiza, medido — não prometido.

"Otimizou tokens" sem número é opinião. Isto roda o roteador contra um
conjunto fixo de comandos representativos e imprime o schema que iria na rede
em cada um, comparado com o catálogo inteiro que ia antes.

    python -m james.diagnostics.bench_packs

O que a saída permite julgar, e que a média sozinha esconderia:

  - a mediana, que é o turno típico;
  - o PIOR caso, que é o que decide se a mudança pode piorar alguma coisa;
  - quantos comandos caíram no fallback de catálogo inteiro — se forem muitos,
    a lista de gatilhos está fraca e a economia é menor do que parece.

O contador é de CARACTERES de JSON, não de tokens. Chamar de token exigiria o
tokenizador do provedor, que muda por modelo; a razão entre antes e depois é a
mesma, e é ela que interessa. A conversão aproximada (÷4) aparece só como
ordem de grandeza, marcada como tal.
"""

from __future__ import annotations

import json
import statistics
import tempfile
from pathlib import Path

from james.config import Config
from james.memory.curated_store import MemoryStore
from james.memory.fact_store import FactStore
from james.permissions.guard import Guard
from james.skills.registry import SkillRegistry
from james.tools import build_registry
from james.tools.packs import escolher_packs, ferramentas_dos_packs

# Comandos representativos do uso real, agrupados como o prompt da auditoria
# pediu. Não são casos de teste: são o que a pessoa fala num dia.
COMANDOS: dict[str, tuple[str, ...]] = {
    "sem ferramenta": (
        "obrigado senhor",
        "bom dia James",
        "tudo bem com você",
        "quem foi Alan Turing",
        "me explica o que é entropia",
        "conta uma piada",
    ),
    "locais": (
        "que horas são",
        "aumenta o volume",
        "abaixa o som",
        "abre o chrome",
        "fecha o spotify",
        "como está a memória da máquina",
    ),
    "memória": (
        "lembra que eu prefiro café sem açúcar",
        "o que você sabe sobre o meu chefe",
        "anota que a reunião mudou para quinta",
        "esquece o que eu falei sobre a viagem",
        "que relação tem a Maria com São Paulo",
    ),
    "web": (
        "pesquisa sobre baterias de estado sólido",
        "procura na internet o preço do dólar",
        "abre o site da receita federal",
        "faz uma pesquisa aprofundada sobre fusão nuclear",
    ),
    "arquivos": (
        "organiza a pasta de downloads",
        "lista os arquivos do desktop",
        "renomeia esse arquivo para relatorio final",
    ),
    "escritório": (
        "faz uma apresentação sobre robótica",
        "monta uma planilha de gastos do mês",
    ),
    "finanças": (
        "analisa a PETR4",
        "compara VALE3 e PETR4",
        "como está o ibovespa hoje",
    ),
    "visão": (
        "o que apareceu na minha tela",
        "olha a câmera e me diz quem está aí",
    ),
}


def _tamanho(esquemas: dict, nomes) -> int:
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


def medir() -> dict:
    raiz = Path(tempfile.mkdtemp())
    config = Config({})
    registry = build_registry(
        config,
        Guard(config),
        memory=MemoryStore(raiz),
        facts=FactStore(raiz / "f.db"),
        skills=SkillRegistry(raiz / "skills"),
    )
    esquemas = {s.name: s for s in registry.schemas()}
    catalogo = frozenset(registry.names)
    antes = _tamanho(esquemas, catalogo)

    linhas = []
    for grupo, frases in COMANDOS.items():
        for frase in frases:
            selecao = escolher_packs(frase)
            nomes = ferramentas_dos_packs(selecao.packs)
            depois = _tamanho(esquemas, nomes)
            linhas.append(
                {
                    "grupo": grupo,
                    "frase": frase,
                    "antes": antes,
                    "depois": depois,
                    "ferramentas": len([n for n in nomes if n in esquemas]),
                    "packs": selecao.resumo,
                    "completo": selecao.completo,
                }
            )

    return {
        "catalogo": {"ferramentas": len(catalogo), "caracteres": antes},
        "linhas": linhas,
    }


def _barra(fracao: float, largura: int = 18) -> str:
    cheio = round(fracao * largura)
    return "█" * cheio + "·" * (largura - cheio)


def main() -> int:
    dados = medir()
    antes = dados["catalogo"]["caracteres"]
    total = dados["catalogo"]["ferramentas"]

    print(f"\nCatálogo inteiro: {total} ferramentas, {antes} caracteres de schema")
    print(f"                  (~{antes // 4} tokens, ordem de grandeza)\n")

    grupo_atual = None
    for linha in dados["linhas"]:
        if linha["grupo"] != grupo_atual:
            grupo_atual = linha["grupo"]
            print(f"  {grupo_atual.upper()}")
        fracao = linha["depois"] / antes
        marca = "  TUDO" if linha["completo"] else ""
        print(
            f"    {_barra(fracao)} {fracao:5.0%}  "
            f"{linha['ferramentas']:2d} ferr.  {linha['frase'][:38]:38s}{marca}"
        )

    fracoes = [l["depois"] / antes for l in dados["linhas"]]
    completos = [l for l in dados["linhas"] if l["completo"]]

    print(f"\n  mediana .......... {statistics.median(fracoes):.0%} do catálogo")
    print(f"  média ............ {statistics.fmean(fracoes):.0%}")
    print(f"  melhor caso ...... {min(fracoes):.0%}")
    print(f"  PIOR caso ........ {max(fracoes):.0%}   <- é este que decide")
    print(
        f"  catálogo inteiro . {len(completos)} de {len(fracoes)} comandos "
        f"({len(completos) / len(fracoes):.0%})"
    )
    if completos:
        print("     " + "; ".join(l["frase"] for l in completos[:4]))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
