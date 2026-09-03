"""Memória curada em markdown — dois arquivos que o James possui.

Princípio central, herdado do estudo do Hermes: a memória nasce APENAS da
conversa. Não existe ingestão de e-mail, arquivos, histórico de navegador ou
qualquer outra fonte. Isso não é uma limitação técnica, é a fronteira que
separa "um assistente que te conhece" de "um aspirador de dados" — e é a mesma
cautela que fez o Recall da Microsoft ser tão criticado.

Dois arquivos, com papéis distintos:
    MEMORY.md   notas do próprio James: ambiente, convenções, manias de app
    USER.md     o que ele sabe sobre você: preferências, jeito de falar, hábitos

Três decisões de projeto que valem explicar:

1. **Markdown simples, não banco de dados.** Você consegue abrir, ler e editar
   os arquivos à mão. Uma memória que só o programa entende é uma memória que
   você não controla.

2. **Limite por CARACTERES, não por tokens.** Um teto em tokens é invisível
   para quem escreve; um teto em caracteres obriga o James a guardar só o que
   tem sinal alto, em vez de despejar tudo que foi dito.

3. **Instantâneo congelado.** O conteúdo entra no prompt uma vez, no início da
   sessão. Uma escrita no meio da conversa vai para o arquivo na hora, mas só
   entra no prompt na próxima — assim o contexto não muda sob os pés do modelo
   no meio de um raciocínio.
"""

from __future__ import annotations

import re
import threading
from enum import Enum
from pathlib import Path

from james.logs import audit, audit_text, get_logger

logger = get_logger("james.memory")

_HEADERS = {
    "MEMORY.md": (
        "# Memória do James\n\n"
        "Notas do próprio assistente: ambiente, convenções e detalhes\n"
        "operacionais desta máquina. Editável à mão.\n\n"
    ),
    "USER.md": (
        "# Sobre o usuário\n\n"
        "Preferências, hábitos e detalhes pessoais que o James aprendeu\n"
        "conversando. Editável à mão.\n\n"
    ),
}


class MemoryScope(Enum):
    MEMORY = "memory"   # sobre o ambiente e o próprio James
    USER = "user"       # sobre a pessoa

    @property
    def filename(self) -> str:
        return "MEMORY.md" if self is MemoryScope.MEMORY else "USER.md"

    @classmethod
    def parse(cls, value: str) -> "MemoryScope":
        text = str(value or "").strip().lower()
        if text in ("user", "usuario", "usuário", "pessoa", "sobre_mim"):
            return cls.USER
        if text in ("memory", "memoria", "memória", "ambiente", "sistema"):
            return cls.MEMORY
        raise ValueError(f"Escopo de memória desconhecido: {value!r}")


class MemoryFull(RuntimeError):
    """O arquivo atingiu o limite de caracteres."""


class MemoryStore:
    def __init__(self, directory: str | Path | None, max_chars: int = 4000) -> None:
        self.directory = Path(directory) if directory else Path("memories")
        self.max_chars = max(200, int(max_chars))
        self._lock = threading.RLock()
        self._snapshot: str | None = None
        self._ensure_files()

    # ------------------------------------------------------------- arquivos

    def _path(self, scope: MemoryScope) -> Path:
        return self.directory / scope.filename

    def _ensure_files(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            for scope in MemoryScope:
                path = self._path(scope)
                if not path.exists():
                    path.write_text(_HEADERS[scope.filename], encoding="utf-8")
        except OSError as exc:
            # Memória é um recurso auxiliar: sem ela o James conversa igual,
            # só não lembra. Não vale derrubar a inicialização por isso.
            logger.error("Não consegui preparar a pasta de memória: %s", exc)

    def read(self, scope: MemoryScope) -> str:
        with self._lock:
            try:
                return self._path(scope).read_text(encoding="utf-8")
            except OSError as exc:
                logger.error("Não consegui ler %s: %s", scope.filename, exc)
                return ""

    def _write(self, scope: MemoryScope, content: str) -> bool:
        try:
            path = self._path(scope)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(content, encoding="utf-8")
            temp.replace(path)
            return True
        except OSError as exc:
            logger.error("Não consegui gravar %s: %s", scope.filename, exc)
            return False

    # ------------------------------------------------------------- entradas

    def entries(self, scope: MemoryScope) -> list[str]:
        """Cada item de memória é uma linha de lista no markdown."""
        return [
            line.strip()[2:].strip()
            for line in self.read(scope).splitlines()
            if line.strip().startswith("- ")
        ]

    def add(self, scope: MemoryScope, text: str) -> str:
        """Acrescenta uma entrada. Devolve mensagem legível do resultado."""
        entry = _normalize(text)
        if not entry:
            return "Não havia nada para guardar."

        with self._lock:
            current = self.read(scope)
            existing = self.entries(scope)
            if any(entry.lower() == item.lower() for item in existing):
                return "Isso eu já sabia."

            candidate = current.rstrip() + f"\n- {entry}\n"
            if len(candidate) > self.max_chars:
                # Estourar o limite silenciosamente transformaria a memória num
                # despejo; avisar deixa a decisão do que remover com o usuário.
                raise MemoryFull(
                    f"{scope.filename} está no limite de {self.max_chars} caracteres. "
                    "Remova algo antes de guardar mais."
                )
            if not self._write(scope, candidate):
                return "Não consegui gravar na memória."

        self._snapshot = None
        # A anotação é o retrato que o James faz de você; guardá-la de novo
        # na trilha seria uma segunda cópia, num arquivo mais fácil de ler.
        audit("memoria_add", escopo=scope.value, **audit_text(entry, campo="texto"))
        return "Guardado."

    def replace(self, scope: MemoryScope, fragment: str, new_text: str) -> str:
        """Substitui a entrada que contém `fragment`.

        A localização é por trecho de texto, não por identificador: o modelo
        não teria como saber um id, e trechos são o que ele naturalmente cita.
        """
        needle = _normalize(fragment).lower()
        replacement = _normalize(new_text)
        if not needle or not replacement:
            return "Preciso saber o que trocar e pelo quê."

        with self._lock:
            matches = [item for item in self.entries(scope) if needle in item.lower()]
            if not matches:
                return "Não encontrei essa entrada na memória."
            if len(matches) > 1:
                # Trocar a entrada errada é pior que não trocar nada.
                return f"Encontrei {len(matches)} entradas parecidas. Seja mais específico."

            target = matches[0]
            content = self.read(scope).replace(f"- {target}", f"- {replacement}", 1)
            if len(content) > self.max_chars:
                raise MemoryFull(f"{scope.filename} ficaria acima do limite.")
            if not self._write(scope, content):
                return "Não consegui gravar na memória."

        self._snapshot = None
        # Substituir vaza DUAS vezes se ninguém olhar: o de antes e o de depois.
        audit(
            "memoria_replace",
            escopo=scope.value,
            **audit_text(target, campo="de"),
            **audit_text(replacement, campo="para"),
        )
        return "Atualizado."

    def remove(self, scope: MemoryScope, fragment: str) -> str:
        needle = _normalize(fragment).lower()
        if not needle:
            return "Preciso saber o que remover."

        with self._lock:
            matches = [item for item in self.entries(scope) if needle in item.lower()]
            if not matches:
                return "Não encontrei essa entrada na memória."
            if len(matches) > 1:
                return f"Encontrei {len(matches)} entradas parecidas. Seja mais específico."

            target = matches[0]
            lines = [
                line
                for line in self.read(scope).splitlines()
                if line.strip() != f"- {target}"
            ]
            if not self._write(scope, "\n".join(lines).rstrip() + "\n"):
                return "Não consegui gravar na memória."

        self._snapshot = None
        audit("memoria_remove", escopo=scope.value, **audit_text(target, campo="texto"))
        return "Esquecido."

    def search(self, query: str) -> list[tuple[str, str]]:
        """Busca literal nos dois arquivos. Devolve (escopo, entrada)."""
        needle = _normalize(query).lower()
        if not needle:
            return []
        found: list[tuple[str, str]] = []
        for scope in MemoryScope:
            for entry in self.entries(scope):
                if needle in entry.lower():
                    found.append((scope.value, entry))
        return found

    # ------------------------------------------------------------ instantâneo

    def snapshot(self) -> str:
        """Texto injetado no prompt do sistema, congelado por sessão."""
        with self._lock:
            if self._snapshot is not None:
                return self._snapshot

            blocos: list[str] = []
            for scope, titulo in (
                (MemoryScope.USER, "O QUE VOCÊ SABE SOBRE O USUÁRIO"),
                (MemoryScope.MEMORY, "SUAS NOTAS SOBRE ESTA MÁQUINA"),
            ):
                entradas = self.entries(scope)
                if entradas:
                    itens = "\n".join(f"- {item}" for item in entradas)
                    blocos.append(f"{titulo}\n{itens}")

            self._snapshot = "\n\n".join(blocos)
            return self._snapshot

    def invalidate_snapshot(self) -> None:
        """Força a releitura na próxima sessão (ou após edição manual)."""
        with self._lock:
            self._snapshot = None

    def usage(self) -> dict[str, int]:
        return {
            scope.value: len(self.read(scope)) for scope in MemoryScope
        }


def _normalize(text: str) -> str:
    """Uma entrada é sempre uma linha: quebras virariam itens soltos."""
    collapsed = " ".join(str(text or "").split())
    # Um '- ' no começo viria de o modelo já formatar como lista, o que
    # duplicaria o marcador ao gravar.
    return re.sub(r"^[-*]\s+", "", collapsed).strip()
