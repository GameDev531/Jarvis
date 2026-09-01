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

from james.config import PROJECT_ROOT, ConfigError, get_secret, load_config, load_env

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


_CATALOGO_GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"


def buscar_catalogo_gemini(api_key: str, timeout_s: int = _TIMEOUT_S) -> set[str]:
    """Modelos que a SUA chave do Gemini enxerga agora.

    Diferente do OpenRouter, aqui a listagem exige chave — o catálogo do Google
    varia por conta e por região. Sem chave não dá para conferir, e dizer
    "morto" nesse caso seria mentira.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise CatalogoIndisponivel("Pacote 'httpx' não instalado.") from exc

    try:
        resposta = httpx.get(
            _CATALOGO_GEMINI, params={"key": api_key}, timeout=timeout_s
        )
        resposta.raise_for_status()
        dados = resposta.json()
    except Exception as exc:  # noqa: BLE001
        raise CatalogoIndisponivel(f"Não consegui consultar o Gemini: {exc}") from exc

    modelos = dados.get("models") if isinstance(dados, dict) else None
    if not isinstance(modelos, list):
        raise CatalogoIndisponivel("Resposta do Gemini em formato inesperado.")

    # A API devolve "models/gemini-3.5-flash"; o config usa o nome curto. Guardar
    # as duas formas evita um falso "morto" por causa do prefixo.
    ids: set[str] = set()
    for item in modelos:
        nome = str((item or {}).get("name") or "")
        if nome:
            ids.add(nome)
            ids.add(nome.split("/")[-1])
    if not ids:
        raise CatalogoIndisponivel("O catálogo do Gemini veio vazio.")
    return ids


def modelos_configurados(config) -> dict[str, list[str]]:
    """As listas do config, na ordem em que cada provedor as tenta."""
    openrouter = config.section("llm.openrouter")
    gemini = config.section("llm.gemini")
    do_gemini = [str(m) for m in (gemini.get("models") or [])]
    if not do_gemini and gemini.get("model"):
        do_gemini = [str(gemini["model"])]
    return {
        # O Gemini vem primeiro porque é o papel de PERCEPÇÃO: sem ele, o
        # comando falado não vira texto e o James fica surdo. Um modelo morto
        # aqui derruba mais que um modelo morto no raciocínio.
        "gemini": do_gemini,
        "raciocínio": [str(m) for m in (openrouter.get("models") or [])],
        "visão": [str(m) for m in (openrouter.get("vision_models") or [])],
    }


def conferir(
    config, catalogo: set[str], catalogo_gemini: set[str] | None = None
) -> dict[str, list[tuple[str, bool]]]:
    """Cada papel contra o catálogo do provedor certo.

    `catalogo_gemini=None` significa "não deu para perguntar" — e aí os
    modelos do Gemini saem de fora do relatório em vez de aparecerem como
    mortos. Não conseguir conferir não é o mesmo que estar morto.
    """
    resultado: dict[str, list[tuple[str, bool]]] = {}
    for papel, lista in modelos_configurados(config).items():
        if papel == "gemini":
            if catalogo_gemini is None:
                continue
            resultado[papel] = [(m, m in catalogo_gemini) for m in lista]
        else:
            resultado[papel] = [(m, m in catalogo) for m in lista]
    return resultado


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

    load_env()
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

    # O catálogo do Gemini exige chave; sem ela, aquele papel é omitido do
    # relatório com uma linha explicando — em vez de sumir em silêncio.
    catalogo_gemini = None
    chave = get_secret("GEMINI_API_KEY")
    if chave:
        try:
            catalogo_gemini = buscar_catalogo_gemini(chave)
        except CatalogoIndisponivel as exc:
            print(f"Aviso: não consegui conferir o Gemini ({exc}).", file=sys.stderr)
    else:
        print(
            "Aviso: GEMINI_API_KEY ausente — os modelos do Gemini não foram "
            "conferidos. É justamente o papel de percepção: um modelo morto "
            "ali deixa o James surdo.",
            file=sys.stderr,
        )

    resultado = conferir(config, catalogo, catalogo_gemini)

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
