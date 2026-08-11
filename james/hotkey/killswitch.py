"""Kill switch: atalho global que cancela tudo (A6).

Um assistente que executa ações no sistema precisa de um freio que funcione
quando a própria voz falhou — se o James entendeu errado e começou algo, pedir
"para" por voz depende justamente da camada que errou.

Precisa ser um atalho GLOBAL: `QShortcut` só dispara com a janela em foco, e o
overlay nunca tem foco (é click-through de propósito). No Windows isso é
`RegisterHotKey`, que exige que o registro e o laço de mensagens estejam na
MESMA thread — daí a thread dedicada com laço próprio.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from typing import Callable

if sys.platform == "win32":
    import ctypes.wintypes

from james.logs import get_logger

logger = get_logger("james.hotkey")

_MODIFIERS = {
    "alt": 0x0001,
    "ctrl": 0x0002,
    "control": 0x0002,
    "shift": 0x0004,
    "win": 0x0008,
    "super": 0x0008,
}
_NAMED_KEYS = {
    "space": 0x20, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
    "enter": 0x0D, "return": 0x0D, "backspace": 0x08, "delete": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "pause": 0x13,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}

_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_HOTKEY_ID = 0xB10C


class HotkeyError(RuntimeError):
    """Atalho inválido ou já registrado por outro programa."""


def parse_hotkey(spec: str) -> tuple[int, int]:
    """"ctrl+alt+j" -> (modificadores, virtual-key). Levanta HotkeyError."""
    text = str(spec or "").strip().lower()
    if not text:
        raise HotkeyError("Atalho vazio.")

    parts = [part.strip() for part in text.split("+") if part.strip()]
    if not parts:
        raise HotkeyError(f"Atalho inválido: {spec!r}")

    modifiers = 0
    key: int | None = None
    for part in parts:
        if part in _MODIFIERS:
            modifiers |= _MODIFIERS[part]
            continue
        if key is not None:
            raise HotkeyError(f"Atalho com mais de uma tecla principal: {spec!r}")
        if part in _NAMED_KEYS:
            key = _NAMED_KEYS[part]
        elif len(part) == 1 and (part.isalpha() or part.isdigit()):
            key = ord(part.upper())
        else:
            raise HotkeyError(f"Tecla desconhecida no atalho: {part!r}")

    if key is None:
        raise HotkeyError(f"Atalho sem tecla principal: {spec!r}")
    if modifiers == 0:
        # Sem modificador, o atalho engoliria a tecla no sistema inteiro.
        raise HotkeyError(f"Atalho precisa de ao menos um modificador: {spec!r}")
    return modifiers, key


class GlobalHotkey:
    """Registra um atalho global e chama `callback` quando ele dispara."""

    def __init__(self, spec: str, callback: Callable[[], None]) -> None:
        self.spec = spec
        self.callback = callback
        self.modifiers, self.key = parse_hotkey(spec)
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self._stop = threading.Event()

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def start(self) -> bool:
        """Devolve True se o atalho ficou ativo."""
        if not self.supported:
            logger.warning(
                "Atalho global só é suportado no Windows; use o menu da bandeja "
                "para cancelar uma ação."
            )
            return False

        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="killswitch", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)

        if self._error:
            logger.error("Kill switch indisponível: %s", self._error)
            return False
        logger.info("Kill switch ativo em %s.", self.spec)
        return True

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        if not user32.RegisterHotKey(None, _HOTKEY_ID, self.modifiers, self.key):
            self._error = (
                f"'{self.spec}' já está registrado por outro programa "
                f"(erro {ctypes.GetLastError()})."
            )
            self._ready.set()
            return

        self._ready.set()
        try:
            message = ctypes.wintypes.MSG()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):   # WM_QUIT ou erro
                    break
                if message.message == _WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception:  # noqa: BLE001 — o freio não pode quebrar
                        logger.exception("Erro no callback do kill switch.")
        finally:
            user32.UnregisterHotKey(None, _HOTKEY_ID)

    def stop(self) -> None:
        self._stop.set()
        if self._thread_id is not None and self.supported:
            try:
                # Desbloqueia o GetMessageW, que é uma espera bloqueante.
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
            except (AttributeError, OSError) as exc:
                logger.debug("Falha ao encerrar a thread do atalho: %s", exc)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
