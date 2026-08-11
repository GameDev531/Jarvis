#!/usr/bin/env python3
"""Fase 0 — prova de compatibilidade. Rode isto antes de qualquer outra coisa.

    python check_hardware.py
    python check_hardware.py --only cpu piper
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from james.diagnostics.check_hardware import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
