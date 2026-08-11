#!/usr/bin/env python3
"""Ponto de entrada do James (processo 1).

Este é o processo que você inicia. Ele escuta a palavra de ativação e sobe o
orquestrador (processo 2) sozinho, reiniciando-o se ele cair.

    python wake_listener.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from james.runtime.wake_listener import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
