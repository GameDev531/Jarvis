#!/usr/bin/env python3
"""Empacota o projeto para compartilhar, sem levar junto os seus dados.

    python distribuir.py                  -> james-dist.zip
    python distribuir.py --saida /tmp/x.zip
    python distribuir.py --listar         -> só mostra o que entraria
    python distribuir.py --verificar      -> só confere, devolve 0 ou 1

A decisão de o que entra mora em `james/distribuicao.py`; aqui é a linha de
comando. A separação existe para os testes poderem afirmar coisas sobre o
pacote sem gerar um ZIP.

## O que mudou, e por quê

A versão anterior perguntava ao git o que era rastreado e depois passava um
filtro de suspeitos por cima. Ainda assim saíram bancos SQLite, `-wal`/`-shm`,
`runtime_state`, contadores de uso, logs e `__pycache__` — porque a pergunta
estava invertida: "a árvore toda, menos o que eu lembrar de excluir". Formato
de artefato novo entra por padrão nesse modelo.

Agora é o contrário: **nada entra, exceto o que a allowlist nomeia**. E antes
de escrever o ZIP, um scanner lê o conteúdo dos arquivos aprovados procurando
padrão de credencial. Achou, ABORTA — não gera pacote nenhum.

Um ZIP que não sai custa dois minutos. Um ZIP com a sua chave dentro não tem
volta: a chave já circulou.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from james.distribuicao import Pacote, montar, motivo_de_recusa  # noqa: E402

RAIZ = Path(__file__).resolve().parent


def _suspeito(caminho: Path) -> str | None:
    """Este caminho é dado de execução? Devolve o motivo, ou `None`.

    Mantida com o mesmo nome e o mesmo contrato de antes: era a função que os
    testes de auditoria interrogavam, e quebrar isso perderia a cobertura que
    já existia. Por dentro, agora é a decisão por allowlist.
    """
    caminho = Path(caminho)
    if caminho.is_absolute():
        try:
            caminho = caminho.relative_to(RAIZ)
        except ValueError:
            pass
    return motivo_de_recusa(caminho)


def _relatar(pacote: Pacote, mostrar_recusas: bool) -> None:
    if mostrar_recusas and pacote.recusados:
        print(f"Ficaram de fora {len(pacote.recusados)} arquivos. Amostra:")
        for achado in pacote.recusados[:15]:
            print(f"  - {achado}")
        if len(pacote.recusados) > 15:
            print(f"  ... e mais {len(pacote.recusados) - 15}.")
        print()

    total = sum((RAIZ / c).stat().st_size for c in pacote.incluidos)
    print(f"{len(pacote.incluidos)} arquivos, {total / 1024 / 1024:.1f} MB.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Empacota o projeto sem dados privados.")
    parser.add_argument("--saida", default="james-dist.zip", help="arquivo .zip de saída")
    parser.add_argument("--listar", action="store_true", help="só mostra, não escreve")
    parser.add_argument(
        "--verificar",
        action="store_true",
        help="só confere se o pacote sairia limpo (0 = sim, 1 = não)",
    )
    parser.add_argument(
        "--recusas", action="store_true", help="mostra o que ficou de fora e por quê"
    )
    args = parser.parse_args(argv)

    pacote = montar(RAIZ)
    _relatar(pacote, mostrar_recusas=args.recusas or args.listar)

    if pacote.segredos:
        print("\nABORTADO: padrão de segredo dentro de arquivo que ia no pacote.\n")
        for achado in pacote.segredos:
            print(f"  ! {achado}")
        print(
            "\nNenhum ZIP foi gerado. Tire o segredo do arquivo (use variável de "
            "ambiente / .env, que não é distribuído) e rode de novo."
        )
        return 1

    if args.verificar:
        print("\nPacote limpo: nenhum segredo, nenhum dado de execução.")
        return 0

    if args.listar:
        for caminho in pacote.incluidos:
            print(f"  {caminho}")
        return 0

    destino = Path(args.saida)
    if not destino.is_absolute():
        destino = RAIZ / destino
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        for relativo in pacote.incluidos:
            zf.write(RAIZ / relativo, relativo)

    print(f"\nPronto: {destino}")
    print(
        "Sem logs, sem token, sem memória, sem banco, sem perfil de navegador.\n"
        "Quem receber precisa criar o próprio .env — as chaves não vão junto."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
