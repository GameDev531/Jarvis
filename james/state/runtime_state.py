"""Estado operacional persistente (seção 15).

Guarda coisas como "a apresentação inicial já rodou". É deliberadamente
separado da memória curada: aqui é estado de máquina, lá é conteúdo sobre o
usuário. Misturar os dois faria a memória virar depósito de flags.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from james.logs import get_logger

logger = get_logger("james.state.runtime")


class RuntimeState:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            # Estado corrompido volta ao padrão: o pior efeito é o James se
            # apresentar de novo, o que é bem melhor que não iniciar.
            logger.warning("Estado de runtime ilegível (%s); recomeçando.", exc)
            return {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temp.replace(self.path)
        except OSError as exc:
            logger.warning("Não consegui gravar o estado de runtime: %s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    # ------------------------------------------------------ primeira execução

    def first_run_done(self) -> bool:
        return bool(self.get("primeira_execucao_concluida", False))

    def mark_first_run_done(self) -> None:
        self.set("primeira_execucao_concluida", True)
