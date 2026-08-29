#!/usr/bin/env python3
"""Orquestrador do James (processo 2).

Normalmente você NÃO roda este arquivo: o wake_listener.py o inicia e o
supervisiona. Executá-lo direto é útil para ver as interfaces sem depender da
palavra de ativação — nesse caso ele fica esperando a conexão do processo 1.

    python main.py                 # só a janela Qt
    python main.py --holograma     # janela Qt + interface holográfica no navegador
    python main.py --modo gestos   # liga qualquer modo pelo nome
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from james.runtime.orchestrator import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
