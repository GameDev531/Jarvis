#!/usr/bin/env python3
"""Confere se os modelos do config.yaml ainda existem no catálogo do OpenRouter.

O catálogo `:free` muda sozinho — em agosto de 2026 o tier grátis inteiro da
Meta e da Qwen saiu de uma vez. Rode isto de vez em quando, e sempre que o
James começar a demorar mais do que devia para responder.

    python check_modelos.py
    python check_modelos.py --json
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from james.diagnostics.check_models import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
