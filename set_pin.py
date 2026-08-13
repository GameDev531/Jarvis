#!/usr/bin/env python3
"""Define, troca ou remove o PIN de confirmação do James.

O PIN é pedido antes de qualquer ação de Nível 2 (mover arquivos, fechar
programas, capturar a tela). Ele também resolve uma limitação real: sem
whisper.cpp instalado não há confirmação por voz, e sem confirmação nenhuma
ação de risco pode ser executada.

    python set_pin.py            define ou troca
    python set_pin.py --remover  volta a confirmar por voz
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from james.config import ConfigError, load_config  # noqa: E402
from james.security.pin import PinError, PinStore  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PIN de confirmação do James.")
    parser.add_argument("--remover", action="store_true", help="apaga o PIN atual")
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuração inválida: {exc}", file=sys.stderr)
        return 2

    store = PinStore(config.root / "state" / "pin.json")

    if args.remover:
        if not store.configured:
            print("Não há PIN configurado.")
            return 0
        store.clear()
        print("PIN removido. A confirmação volta a ser por voz.")
        return 0

    if store.configured:
        # Trocar o PIN exige conhecer o atual: senão qualquer um que passe pelo
        # computador desbloqueado poderia simplesmente redefini-lo.
        atual = getpass.getpass("PIN atual: ")
        if not store.verify(atual):
            print("PIN incorreto.", file=sys.stderr)
            return 1

    novo = getpass.getpass("Novo PIN: ")
    repetido = getpass.getpass("Repita o novo PIN: ")
    if novo != repetido:
        print("Os PINs não coincidem.", file=sys.stderr)
        return 1

    try:
        store.set_pin(novo)
    except PinError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    print("PIN configurado. As ações de risco passarão a pedi-lo numa janela.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
