"""Plano de vários passos, com encadeamento de resultados.

O que isto resolve: hoje o modelo pode pedir várias ferramentas num turno, mas
cada uma é independente. "Pesquise X, monte uma planilha com o resultado e
salve na pasta Y" exige que o passo 2 consuma a saída do passo 1.

O estudo do Brahma-AI-Lite mostrou o caminho que NÃO seguir: o prompt do
planejador deles diz literalmente "NEVER reference previous step results in
parameters. Every step is independent." Isso não é automação sequencial — é uma
lista de ações soltas com nome bonito.

Como o encadeamento funciona aqui: um passo pode guardar seu resultado sob um
nome (`salvar_como`), e passos seguintes referenciam esse nome com
`{{nome}}` ou `{{nome.campo}}`. A substituição acontece em **Python**, antes do
guard — o modelo escreve a referência, mas quem a resolve é o código. Isso
importa: se a substituição fosse feita pelo modelo, ele poderia injetar
qualquer caminho ou URL no lugar, contornando a validação que o guard faz sobre
argumentos já resolvidos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MAX_STEPS = 8
MAX_DEPTH = 6
# Nome de variável: letras, números e sublinhado. Casar qualquer coisa aqui
# permitiria referências que parecem caminho de arquivo.
_NAME = r"[A-Za-zÀ-ÿ0-9_]+"
_REFERENCE = re.compile(r"\{\{\s*(" + _NAME + r"(?:\." + _NAME + r")*)\s*\}\}")
_EXACT_REFERENCE = re.compile(r"^\s*\{\{\s*(" + _NAME + r"(?:\." + _NAME + r")*)\s*\}\}\s*$")

# Ferramentas que um passo nunca pode chamar: a própria sequência (recursão) e
# qualquer coisa que não seja uma tool de verdade.
FORBIDDEN_IN_STEPS = frozenset({"executar_sequencia"})


class PlanError(ValueError):
    """Plano malformado ou referência impossível de resolver."""


@dataclass
class Step:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    salvar_como: str | None = None
    descricao: str = ""

    @property
    def rotulo(self) -> str:
        return self.descricao or self.tool


@dataclass
class Plan:
    objetivo: str
    steps: list[Step] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.steps)


def parse_plan(objetivo: str, raw_steps: Any, known_tools: tuple[str, ...]) -> Plan:
    """Valida a estrutura vinda do modelo e devolve um plano utilizável.

    A validação é estrutural apenas — se cada passo PODE rodar é decisão do
    guard, no momento da execução. Aqui só barramos o que nem chega a ser um
    plano.
    """
    objetivo = " ".join(str(objetivo or "").split())
    if not objetivo:
        raise PlanError("O plano precisa de um objetivo.")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanError("O plano precisa de uma lista de passos.")
    if len(raw_steps) > MAX_STEPS:
        raise PlanError(
            f"São {len(raw_steps)} passos, acima do meu limite de {MAX_STEPS}."
        )

    steps: list[Step] = []
    nomes_disponiveis: set[str] = set()

    for indice, bruto in enumerate(raw_steps, start=1):
        if not isinstance(bruto, dict):
            raise PlanError(f"O passo {indice} não é um objeto.")

        tool = str(bruto.get("ferramenta") or bruto.get("tool") or "").strip()
        if not tool:
            raise PlanError(f"O passo {indice} não diz qual ferramenta usar.")
        if tool in FORBIDDEN_IN_STEPS:
            raise PlanError(f"Um passo não pode chamar '{tool}'.")
        if tool not in known_tools:
            raise PlanError(f"O passo {indice} usa uma ferramenta que não existe: '{tool}'.")

        args = bruto.get("argumentos") or bruto.get("args") or {}
        if not isinstance(args, dict):
            raise PlanError(f"Os argumentos do passo {indice} não são um objeto.")

        salvar = bruto.get("salvar_como") or bruto.get("save_as")
        salvar = str(salvar).strip() if salvar else None
        if salvar and not re.fullmatch(_NAME, salvar):
            raise PlanError(f"Nome de resultado inválido no passo {indice}: '{salvar}'.")
        if salvar and salvar in nomes_disponiveis:
            raise PlanError(f"O nome de resultado '{salvar}' foi usado duas vezes.")

        # Referência a um passo posterior nunca poderá ser resolvida — falhar
        # aqui é melhor que falhar no meio da execução, com passos já feitos.
        for referencia in _collect_references(args):
            raiz = referencia.split(".")[0]
            if raiz not in nomes_disponiveis:
                raise PlanError(
                    f"O passo {indice} usa o resultado '{raiz}', que nenhum passo "
                    "anterior produz."
                )

        if salvar:
            nomes_disponiveis.add(salvar)

        steps.append(
            Step(
                tool=tool,
                args=args,
                salvar_como=salvar,
                descricao=" ".join(str(bruto.get("descricao") or "").split()),
            )
        )

    return Plan(objetivo=objetivo, steps=steps)


def _collect_references(value: Any, depth: int = 0) -> list[str]:
    """Todas as referências `{{...}}` presentes numa estrutura de argumentos."""
    if depth > MAX_DEPTH:
        return []
    if isinstance(value, str):
        return _REFERENCE.findall(value)
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_collect_references(item, depth + 1))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_collect_references(item, depth + 1))
        return found
    return []


def resolve_references(value: Any, scope: dict[str, Any], depth: int = 0) -> Any:
    """Substitui `{{nome.campo}}` pelos resultados já produzidos.

    Duas formas, de propósito:

    - a string é EXATAMENTE uma referência -> devolve o valor com o tipo
      original (uma lista continua lista, um número continua número);
    - a referência está no meio de um texto -> vira texto.

    Referência inexistente levanta erro em vez de virar literal: deixar
    `{{arquivo}}` passar adiante como texto acabaria virando um nome de arquivo
    absurdo, ou pior, um caminho que o guard avaliaria como válido.
    """
    if depth > MAX_DEPTH:
        raise PlanError("Argumentos aninhados demais.")

    if isinstance(value, str):
        exata = _EXACT_REFERENCE.match(value)
        if exata:
            return _lookup(exata.group(1), scope)

        def trocar(match: re.Match) -> str:
            encontrado = _lookup(match.group(1), scope)
            return "" if encontrado is None else str(encontrado)

        return _REFERENCE.sub(trocar, value)

    if isinstance(value, dict):
        return {chave: resolve_references(item, scope, depth + 1) for chave, item in value.items()}
    if isinstance(value, list):
        return [resolve_references(item, scope, depth + 1) for item in value]
    return value


def _lookup(caminho: str, scope: dict[str, Any]) -> Any:
    """Navega `nome.campo.subcampo` dentro dos resultados guardados.

    Só percorre chaves de dicionário e índices de lista — nunca atributos de
    objeto. Permitir acesso a atributos deixaria o texto do modelo alcançar o
    interior das nossas estruturas.
    """
    partes = caminho.split(".")
    if partes[0] not in scope:
        raise PlanError(f"Não existe um resultado chamado '{partes[0]}'.")

    atual: Any = scope[partes[0]]
    for parte in partes[1:]:
        if isinstance(atual, dict):
            if parte not in atual:
                raise PlanError(f"O resultado '{partes[0]}' não tem o campo '{parte}'.")
            atual = atual[parte]
        elif isinstance(atual, (list, tuple)) and parte.isdigit():
            indice = int(parte)
            if indice >= len(atual):
                raise PlanError(f"O resultado '{partes[0]}' não tem o item {indice}.")
            atual = atual[indice]
        else:
            raise PlanError(
                f"Não consigo acessar '{parte}' dentro de '{partes[0]}'."
            )
    return atual
