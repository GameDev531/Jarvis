#!/usr/bin/env python3
"""Empacota o projeto para compartilhar, sem levar junto os seus dados.

Existe porque um ZIP feito à mão levou coisas que não deviam sair da máquina:
comandos falados e argumentos de ferramenta nos logs, o TOKEN da interface
holográfica, o relatório detalhado do hardware, o banco de memória com o que
o James sabe sobre você, e `__pycache__` por toda parte.

O `.gitignore` estava certo. O problema é que "compactar a pasta" não lê o
`.gitignore` — e é o que qualquer pessoa faz na pressa.

    python distribuir.py                  -> james-dist.zip
    python distribuir.py --saida /tmp/x.zip
    python distribuir.py --listar         -> só mostra o que entraria

O critério é o oposto do intuitivo: em vez de listar o que EXCLUIR (e esquecer
um), pergunta ao git o que é RASTREADO. O que o git não versiona não é código
do projeto — é dado de execução, e dado de execução é seu.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent

# Rastreado pelo git mas ainda assim fora: exemplo é exemplo, e um `.env` de
# verdade nunca deveria estar rastreado — se estiver, é bug e o ZIP não é a
# hora de descobrir.
# `Path(".coverage").suffix` é VAZIO — o ponto inicial faz o pathlib tratar o
# nome inteiro como stem. Filtrar por extensão deixaria este passar, e foi
# exatamente assim que ele entrou no repositório.
NUNCA = {".env", ".coverage", "hardware_report.json"}


def arquivos_rastreados() -> list[Path]:
    try:
        saida = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=RAIZ, capture_output=True, check=True, text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            f"Precisei do git para saber o que é código e não consegui: {exc}\n"
            "Sem isso eu não tenho como distinguir o projeto dos seus dados, e "
            "prefiro não gerar um ZIP a gerar um com as suas conversas dentro."
        )
    return [RAIZ / nome for nome in saida.split("\0") if nome]


def _suspeito(caminho: Path) -> str | None:
    """Última conferência, arquivo a arquivo. Cinto além do suspensório."""
    nome = caminho.name.lower()
    partes = {p.lower() for p in caminho.parts}
    if nome in NUNCA:
        return "arquivo de segredos"
    if caminho.suffix in {".log", ".jsonl", ".db", ".db-wal", ".db-shm"}:
        return f"dado de execução ({caminho.suffix or nome})"
    if "__pycache__" in partes or caminho.suffix == ".pyc":
        return "cache do Python"
    if "memories" in partes:
        return "memória do James sobre você"
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Empacota o projeto sem dados privados.")
    parser.add_argument("--saida", default="james-dist.zip", help="arquivo .zip de saída")
    parser.add_argument("--listar", action="store_true", help="só mostra, não escreve")
    args = parser.parse_args(argv)

    incluidos: list[Path] = []
    recusados: list[tuple[Path, str]] = []

    for caminho in arquivos_rastreados():
        if not caminho.exists():
            continue                     # rastreado mas apagado no disco
        motivo = _suspeito(caminho)
        if motivo:
            recusados.append((caminho, motivo))
        else:
            incluidos.append(caminho)

    if recusados:
        print("Deixados de fora, mesmo estando rastreados pelo git:")
        for caminho, motivo in recusados:
            print(f"  - {caminho.relative_to(RAIZ)}  ({motivo})")
        print()

    total = sum(c.stat().st_size for c in incluidos)
    print(f"{len(incluidos)} arquivos, {total / 1024 / 1024:.1f} MB.")

    if args.listar:
        for caminho in sorted(incluidos):
            print(f"  {caminho.relative_to(RAIZ)}")
        return 0

    destino = Path(args.saida)
    if not destino.is_absolute():
        destino = RAIZ / destino
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        for caminho in sorted(incluidos):
            zf.write(caminho, caminho.relative_to(RAIZ))

    print(f"\nPronto: {destino}")
    print(
        "Sem logs, sem token, sem memória, sem banco, sem relatório de hardware.\n"
        "Quem receber precisa criar o próprio .env — as chaves não vão junto."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
