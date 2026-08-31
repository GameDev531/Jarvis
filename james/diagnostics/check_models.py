"""Confere se os modelos do `config.yaml` ainda existem no OpenRouter.

Existe por causa de um estrago concreto: em agosto de 2026 o OpenRouter tirou
o tier grátis inteiro da Meta e da Qwen de uma vez. O `config.yaml` do James
continuou apontando para `meta-llama/llama-3.3-70b-instruct:free` e
`qwen/qwen-2.5-72b-instruct:free` — dois dos cinco modelos de raciocínio,
mortos, silenciosamente.

O modo como isso falhava é o pior possível: **o James continuava respondendo.**
Cada requisição gastava duas viagens de rede levando 404 antes de chegar a um
modelo vivo. Nada quebrava; só ficava lento, e ninguém tinha motivo para
suspeitar do config.

Não dá para descobrir isso lendo o código, porque a resposta está no catálogo
do OpenRouter, que muda sozinho. Só uma consulta responde — e ela não precisa
de chave: o endpoint `/models` é público.

    python check_modelos.py
    python check_modelos.py --json
"""

from __future__ import annotations

import argparse
import json
import sys

from james.config import PROJECT_ROOT, ConfigError, load_config

_CATALOGO = "https://openrouter.ai/api/v1/models"
_TIMEOUT_S = 30


class CatalogoIndisponivel(RuntimeError):
    """Não deu para consultar o catálogo. Diferente de 'o modelo morreu'."""


def buscar_catalogo(url: str = _CATALOGO, timeout_s: int = _TIMEOUT_S) -> set[str]:
    """Todos os IDs de modelo que o OpenRouter oferece agora.

    Sem chave de propósito: `/models` é público, e assim o diagnóstico funciona
    mesmo para quem ainda não configurou o `.env` — que é justamente quem mais
    precisa saber se a lista está sã.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx é dependência fixa
        raise CatalogoIndisponivel("Pacote 'httpx' não instalado.") from exc

    try:
        resposta = httpx.get(url, timeout=timeout_s)
        resposta.raise_for_status()
        dados = resposta.json()
    except Exception as exc:  # noqa: BLE001 — httpx levanta tipos variados
        raise CatalogoIndisponivel(f"Não consegui consultar {url}: {exc}") from exc

    modelos = dados.get("data") if isinstance(dados, dict) else None
    if not isinstance(modelos, list):
        raise CatalogoIndisponivel(
            "Resposta do OpenRouter em formato inesperado (sem a lista 'data')."
        )

    ids = {
        str(item["id"])
        for item in modelos
        if isinstance(item, dict) and item.get("id")
    }
    if not ids:
        raise CatalogoIndisponivel("O catálogo veio vazio.")
    return ids


def modelos_configurados(config) -> dict[str, list[str]]:
    """As duas listas do config, na ordem em que o provedor as tenta."""
    secao = config.section("llm.openrouter")
    return {
        "raciocínio": [str(m) for m in (secao.get("models") or [])],
        "visão": [str(m) for m in (secao.get("vision_models") or [])],
    }


def conferir(config, catalogo: set[str]) -> dict[str, list[tuple[str, bool]]]:
    return {
        papel: [(modelo, modelo in catalogo) for modelo in lista]
        for papel, lista in modelos_configurados(config).items()
    }


def _imprimir(resultado: dict[str, list[tuple[str, bool]]]) -> list[str]:
    """Imprime o relatório e devolve os IDs mortos."""
    mortos: list[str] = []
    for papel, itens in resultado.items():
        print(f"\n{papel.upper()}")
        if not itens:
            print("  (nenhum modelo configurado)")
            continue
        for modelo, vivo in itens:
            print(f"  [{'  OK  ' if vivo else 'MORTO '}] {modelo}")
            if not vivo:
                mortos.append(modelo)
    return mortos


def _veredito(resultado: dict[str, list[tuple[str, bool]]], mortos: list[str]) -> int:
    print("\n" + "=" * 70)
    if not mortos:
        total = sum(len(itens) for itens in resultado.values())
        print(f"VEREDITO: os {total} modelos configurados existem no catálogo.")
        return 0

    print(f"VEREDITO: {len(mortos)} modelo(s) não existem mais no OpenRouter.")
    print("Remova do config.yaml (llm.openrouter):")
    for modelo in mortos:
        print(f"  - {modelo}")

    # Uma lista que ficou vazia é bem pior que uma lista encurtada, e merece
    # ser dita separadamente: o papel inteiro deixa de funcionar.
    for papel, itens in resultado.items():
        if itens and not any(vivo for _, vivo in itens):
            print(
                f"\nATENÇÃO: NENHUM modelo de {papel} está vivo. "
                "Esse papel vai falhar em toda requisição até a lista ser corrigida."
            )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Confere se os modelos do config.yaml existem no OpenRouter."
    )
    parser.add_argument("--json", action="store_true", help="saída em JSON")
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuração inválida: {exc}", file=sys.stderr)
        return 2

    try:
        catalogo = buscar_catalogo()
    except CatalogoIndisponivel as exc:
        # Não conseguir perguntar não é o mesmo que os modelos estarem mortos.
        # Dizer "MORTO" aqui seria mentira, e mandaria alguém apagar uma lista
        # perfeitamente boa por causa de um cabo de rede.
        print(f"Não deu para conferir: {exc}", file=sys.stderr)
        return 3

    resultado = conferir(config, catalogo)

    if args.json:
        print(json.dumps(
            {
                papel: {modelo: vivo for modelo, vivo in itens}
                for papel, itens in resultado.items()
            },
            indent=2, ensure_ascii=False,
        ))
        return 0 if all(v for itens in resultado.values() for _, v in itens) else 1

    print(f"Catálogo do OpenRouter: {len(catalogo)} modelos disponíveis.")
    print(f"Config: {PROJECT_ROOT / 'config.yaml'}")
    mortos = _imprimir(resultado)
    return _veredito(resultado, mortos)


if __name__ == "__main__":
    raise SystemExit(main())
