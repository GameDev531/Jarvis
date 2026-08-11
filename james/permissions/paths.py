"""Validação de caminhos contra a whitelist de pastas.

Bypasses que este módulo precisa barrar (e que os testes exercitam):
  - travessia com `..` / `..\\` em qualquer posição;
  - caminho absoluto fora da whitelist;
  - link simbólico / junction dentro da whitelist apontando para fora;
  - caminho relativo (resolvido contra o CWD, que o James não controla);
  - "irmão" com prefixo igual: `C:\\Dados` não libera `C:\\Dados-secretos`;
  - byte nulo embutido.
"""

from __future__ import annotations

import os
from pathlib import Path


class PathNotAllowed(PermissionError):
    """Caminho fora da whitelist ou malformado."""


def _real(path: Path) -> Path:
    """Resolve links e normaliza `..`, funcionando para caminho inexistente.

    Um alvo que ainda não existe (destino de um "mover") é legítimo: o que
    importa é onde ele *ficaria*. `resolve(strict=False)` resolve a parte
    existente (seguindo links) e normaliza o resto lexicamente.
    """
    try:
        return Path(os.path.realpath(str(path)))
    except (OSError, ValueError) as exc:
        raise PathNotAllowed(f"Caminho inválido: {path} ({exc})") from exc


class PathGuard:
    """Decide se um caminho pode ser tocado, com base em raízes permitidas."""

    def __init__(self, allowed_roots: list[str | Path]) -> None:
        self.roots: list[Path] = []
        self.invalid_roots: list[str] = []
        for raw in allowed_roots or []:
            text = str(raw).strip()
            if not text:
                continue
            candidate = Path(text).expanduser()
            if not candidate.is_absolute():
                # Uma raiz relativa dependeria do CWD do processo — inutilizável
                # como fronteira de segurança.
                self.invalid_roots.append(text)
                continue
            self.roots.append(_real(candidate))

    @property
    def enabled(self) -> bool:
        """Sem raiz válida, nada é permitido (negação por padrão)."""
        return bool(self.roots)

    def is_allowed(self, candidate: str | Path) -> bool:
        try:
            self.validate(candidate)
        except PathNotAllowed:
            return False
        return True

    def validate(self, candidate: str | Path) -> Path:
        """Devolve o caminho real validado ou levanta PathNotAllowed."""
        text = str(candidate)
        if not text.strip():
            raise PathNotAllowed("Caminho vazio.")
        if "\x00" in text:
            raise PathNotAllowed("Caminho contém byte nulo.")

        path = Path(text).expanduser()
        if not path.is_absolute():
            # Cobre também `C:foo` no Windows (drive sem raiz), que é relativo
            # ao diretório corrente daquele drive.
            raise PathNotAllowed(
                f"Caminho relativo não é aceito: {text!r}. Use o caminho completo."
            )

        if not self.roots:
            raise PathNotAllowed(
                "Nenhuma pasta permitida configurada (permissions.folders vazio)."
            )

        resolved = _real(path)
        for root in self.roots:
            if resolved == root or _is_within(resolved, root):
                return resolved

        raise PathNotAllowed(f"Fora das pastas permitidas: {resolved}")


def _is_within(candidate: Path, root: Path) -> bool:
    """Contenção real de diretório.

    `Path.is_relative_to` compara componente a componente (e, no Windows, sem
    diferenciar maiúsculas), então `C:\\Dados-secretos` não passa por
    `C:\\Dados` — que seria o resultado de uma comparação de prefixo de string.
    """
    try:
        return candidate.is_relative_to(root)
    except (AttributeError, ValueError):
        # Python < 3.9 não tem is_relative_to; relative_to levanta ValueError.
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False
