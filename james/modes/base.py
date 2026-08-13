"""Modos — capacidades que ligam e desligam sob comando.

O princípio: **nada de recurso contínuo ativo por padrão.** O James nasce
fazendo o essencial — escutar a palavra de ativação, ouvir, agir e responder —
e nada além disso consome CPU, câmera ou memória enquanto ninguém pedir.

"Jarvis, ativa a webcam" liga o modo de gestos. Enquanto ele estiver ligado, a
câmera está aberta e o rastreamento roda. "Jarvis, desativa a webcam" fecha
tudo — inclusive a luz do hardware, que é a única prova visível para o usuário
de que a câmera parou de verdade.

Duas regras que valem para todo modo:

1. **Um recurso, um dono.** Dois modos que precisem da câmera não podem estar
   ligados ao mesmo tempo; o gerente recusa em vez de deixar os dois brigarem
   pelo dispositivo.
2. **Desligar nunca é bloqueado.** Ligar pode exigir confirmação; desligar é
   sempre permitido, de imediato. Um freio que às vezes não funciona não é
   freio.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from james.logs import audit, get_logger

logger = get_logger("james.modes")


class ModeState(Enum):
    DESLIGADO = "desligado"
    LIGADO = "ligado"
    FALHOU = "falhou"


class ModeError(RuntimeError):
    """Não foi possível ligar ou desligar o modo."""


@dataclass
class ModeInfo:
    nome: str
    descricao: str
    estado: str
    recursos: list[str]
    detalhe: str = ""

    def to_dict(self) -> dict:
        return {
            "nome": self.nome,
            "descricao": self.descricao,
            "estado": self.estado,
            "recursos": self.recursos,
            "detalhe": self.detalhe,
        }


class Mode:
    """Base de um modo. Subclasses implementam `_ligar` e `_desligar`.

    `recursos` declara o hardware que o modo ocupa enquanto ligado — é o que
    permite ao gerente impedir dois modos de disputarem a mesma câmera.
    """

    nome: str = "modo"
    descricao: str = ""
    recursos: tuple[str, ...] = ()
    # Ligar mexe em privacidade (câmera, microfone contínuo)? Então o guard
    # pede confirmação antes.
    sensivel: bool = False

    def __init__(self) -> None:
        self._estado = ModeState.DESLIGADO
        self._detalhe = ""
        self._lock = threading.RLock()

    @property
    def estado(self) -> ModeState:
        with self._lock:
            return self._estado

    @property
    def ligado(self) -> bool:
        return self.estado is ModeState.LIGADO

    @property
    def detalhe(self) -> str:
        with self._lock:
            return self._detalhe

    def info(self) -> ModeInfo:
        return ModeInfo(
            nome=self.nome,
            descricao=self.descricao,
            estado=self.estado.value,
            recursos=list(self.recursos),
            detalhe=self.detalhe,
        )

    # ------------------------------------------------------- ciclo de vida

    def ligar(self) -> str:
        """Liga o modo. Devolve uma frase para o usuário. Levanta ModeError."""
        with self._lock:
            if self._estado is ModeState.LIGADO:
                return f"O modo {self.nome} já está ligado."
            try:
                mensagem = self._ligar()
            except ModeError:
                self._estado = ModeState.FALHOU
                raise
            except Exception as exc:  # noqa: BLE001 — vira erro de modo, não crash
                self._estado = ModeState.FALHOU
                self._detalhe = str(exc)
                raise ModeError(f"Não consegui ligar o modo {self.nome}: {exc}") from exc

            self._estado = ModeState.LIGADO
            self._detalhe = ""

        audit("modo_ligado", modo=self.nome)
        logger.info("Modo '%s' ligado.", self.nome)
        return mensagem or f"Modo {self.nome} ligado."

    def desligar(self) -> str:
        """Desliga o modo. Nunca levanta: desligar precisa sempre funcionar."""
        with self._lock:
            if self._estado is not ModeState.LIGADO:
                return f"O modo {self.nome} não está ligado."
            try:
                mensagem = self._desligar()
            except Exception as exc:  # noqa: BLE001
                # Mesmo com erro na limpeza, o modo passa a desligado: deixá-lo
                # "ligado" impediria uma nova tentativa de liberar o recurso.
                logger.error("Erro ao desligar o modo %s: %s", self.nome, exc)
                mensagem = f"Modo {self.nome} encerrado, mas com erro na limpeza."
            self._estado = ModeState.DESLIGADO
            self._detalhe = ""

        audit("modo_desligado", modo=self.nome)
        logger.info("Modo '%s' desligado.", self.nome)
        return mensagem or f"Modo {self.nome} desligado."

    # ------------------------------------------------------ a implementar

    def _ligar(self) -> str:
        raise NotImplementedError

    def _desligar(self) -> str:
        raise NotImplementedError


class ModeManager:
    def __init__(self, modos: Iterable[Mode] = ()) -> None:
        self._modos: dict[str, Mode] = {}
        self._lock = threading.RLock()
        for modo in modos:
            self.register(modo)

    def register(self, modo: Mode) -> None:
        chave = modo.nome.strip().lower()
        if not chave:
            raise ValueError("Modo sem nome.")
        if chave in self._modos:
            raise ValueError(f"Modo '{chave}' registrado duas vezes.")
        self._modos[chave] = modo

    def get(self, nome: str) -> Mode | None:
        return self._modos.get(str(nome or "").strip().lower())

    @property
    def nomes(self) -> tuple[str, ...]:
        return tuple(self._modos)

    def listar(self) -> list[ModeInfo]:
        return [modo.info() for modo in self._modos.values()]

    def ativos(self) -> list[str]:
        return [nome for nome, modo in self._modos.items() if modo.ligado]

    # ------------------------------------------------------------ operações

    def ligar(self, nome: str) -> str:
        with self._lock:
            modo = self.get(nome)
            if modo is None:
                raise ModeError(f"Não conheço o modo '{nome}'.")

            conflito = self._conflito(modo)
            if conflito is not None:
                raise ModeError(
                    f"O modo {conflito.nome} já está usando "
                    f"{', '.join(sorted(set(modo.recursos) & set(conflito.recursos)))}. "
                    "Desligue-o primeiro."
                )
            return modo.ligar()

    def _conflito(self, alvo: Mode) -> Mode | None:
        """Outro modo ligado que já ocupa um recurso deste."""
        if not alvo.recursos:
            return None
        pedidos = set(alvo.recursos)
        for modo in self._modos.values():
            if modo is alvo or not modo.ligado:
                continue
            if pedidos & set(modo.recursos):
                return modo
        return None

    def desligar(self, nome: str) -> str:
        with self._lock:
            modo = self.get(nome)
            if modo is None:
                raise ModeError(f"Não conheço o modo '{nome}'.")
            return modo.desligar()

    def desligar_todos(self) -> list[str]:
        """Usado no encerramento e pelo kill switch: solta todo hardware."""
        encerrados: list[str] = []
        with self._lock:
            for nome, modo in self._modos.items():
                if modo.ligado:
                    modo.desligar()
                    encerrados.append(nome)
        return encerrados
