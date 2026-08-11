#!/usr/bin/env python3
"""Orquestrador do James (processo 2).

Normalmente você NÃO roda este arquivo: o wake_listener.py o inicia e o
supervisiona. Executá-lo direto é útil para depurar o pipeline sem a palavra de
ativação — nesse caso ele fica esperando a conexão do processo 1.

    python main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from james.runtime.orchestrator import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
