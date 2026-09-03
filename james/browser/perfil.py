"""Onde o navegador do James guarda as coisas dele.

## A decisão que mudou

A primeira versão anexava ao seu Chrome pessoal por CDP, e o argumento era bom:
suas contas já logadas, sem download de 150 MB numa internet lenta. Três
problemas apareceram depois.

**Um: não é confiável.** Exige que você tenha aberto o Chrome com
`--remote-debugging-port`. Fechar e reabrir o navegador com uma flag não é o
que alguém faz para pedir "abre uma aba" ao assistente, e o Chrome moderno
restringe cada vez mais a depuração remota no perfil padrão.

**Dois: o CDP tem fidelidade menor.** Ele não é o protocolo nativo do
Playwright; parte do isolamento de contexto e da interceptação de rede funciona
diferente ou não funciona. A política de rede desta fase depende de
interceptação — construir a trava sobre a base menos confiável seria construir
ao contrário.

**Três, e é o que decide:** dar ao assistente o seu perfil pessoal dá a ele o
seu banco logado, o seu e-mail logado e os seus cookies de sessão. Isso não é
uma decisão de conveniência que se toma por padrão.

Então inverteu: **o padrão é um perfil só do James**, em
`state/browser_profiles/`. Persistente — você loga uma vez num site e ele
lembra —, mas separado do seu, e apagável sem tocar no seu Chrome. Anexar ao
Chrome pessoal continua existindo como modo avançado, ligado a dedo no config.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from james.logs import audit, get_logger

logger = get_logger("james.browser.perfil")

PERFIL_PADRAO = "jarvis-default"


class PerfilInvalido(ValueError):
    """O nome do perfil não serve como nome de pasta."""


def _validar_nome(nome: str) -> str:
    """Um nome de perfil vira caminho de pasta — e caminho aceita `..`.

    Sem esta validação, um perfil chamado `../../..` apagaria o que estivesse
    fora da pasta de perfis quando alguém pedisse "limpa esse perfil".
    """
    limpo = (nome or "").strip()
    if not limpo:
        raise PerfilInvalido("O perfil precisa de um nome.")
    if limpo in (".", "..") or "/" in limpo or "\\" in limpo or limpo.startswith("."):
        raise PerfilInvalido(f"Nome de perfil inválido: {nome!r}")
    if not all(c.isalnum() or c in "-_" for c in limpo):
        raise PerfilInvalido(
            f"Nome de perfil inválido: {nome!r}. Use letras, números, '-' e '_'."
        )
    return limpo


@dataclass(frozen=True)
class Perfil:
    nome: str
    caminho: Path

    @property
    def existe(self) -> bool:
        return self.caminho.is_dir()

    @property
    def tamanho_mb(self) -> float:
        if not self.existe:
            return 0.0
        total = sum(f.stat().st_size for f in self.caminho.rglob("*") if f.is_file())
        return round(total / (1024 * 1024), 1)


class GerenteDePerfis:
    """Cria, lista e apaga perfis — sempre dentro da raiz, nunca fora dela."""

    def __init__(self, raiz: Path | str) -> None:
        self.raiz = Path(raiz)

    def perfil(self, nome: str = PERFIL_PADRAO) -> Perfil:
        limpo = _validar_nome(nome)
        caminho = (self.raiz / limpo).resolve()

        # Cinto e suspensório: mesmo com o nome validado, o caminho final tem
        # que cair dentro da raiz. Um link simbólico dentro da pasta de perfis
        # poderia apontar para fora, e a validação de nome não veria isso.
        raiz = self.raiz.resolve()
        if raiz != caminho.parent and raiz not in caminho.parents:
            raise PerfilInvalido(f"O perfil {nome!r} cairia fora de {raiz}.")
        return Perfil(nome=limpo, caminho=caminho)

    def preparar(self, nome: str = PERFIL_PADRAO) -> Perfil:
        p = self.perfil(nome)
        p.caminho.mkdir(parents=True, exist_ok=True)
        return p

    def listar(self) -> list[Perfil]:
        if not self.raiz.is_dir():
            return []
        saida = []
        for caminho in sorted(self.raiz.iterdir()):
            if caminho.is_dir():
                try:
                    saida.append(self.perfil(caminho.name))
                except PerfilInvalido:
                    continue
        return saida

    def limpar(self, nome: str = PERFIL_PADRAO) -> str:
        """Apaga o perfil inteiro — cookies, sessões, histórico.

        É a resposta para "esquece tudo que você fez no navegador", e é
        importante que exista: um perfil persistente sem botão de apagar é um
        acúmulo de sessão que ninguém controla.
        """
        p = self.perfil(nome)
        if not p.existe:
            return f"O perfil '{p.nome}' já estava vazio."
        tamanho = p.tamanho_mb
        shutil.rmtree(p.caminho)
        audit("navegador_perfil_limpo", perfil=p.nome, mb=tamanho)
        logger.info("Perfil '%s' apagado (%.1f MB).", p.nome, tamanho)
        return f"Apaguei o perfil '{p.nome}' — {tamanho} MB, cookies e sessões junto."
