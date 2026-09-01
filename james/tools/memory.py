"""Ferramentas de memória.

Escrever na memória é Nível 1: é uma nota pessoal, não uma ação no sistema. Se
o James tivesse que pedir permissão toda vez que aprendesse algo sobre você, a
conversa viraria um interrogatório.

O que vale guardar, em ordem de prioridade (regra herdada do Hermes):
    preferência, correção ou detalhe pessoal  >  fato estável do ambiente
    >  procedimento
E o que NÃO guardar: o óbvio, o que é fácil redescobrir, e progresso de tarefa
— isso é estado temporário, não memória.
"""

from __future__ import annotations

from james.logs import get_logger
from james.memory import MemoryScope, MemoryStore
from james.memory.curated_store import MemoryFull
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.memory")


def _scope_from(value: str) -> MemoryScope:
    try:
        return MemoryScope.parse(value)
    except ValueError:
        # Sem escopo claro, o padrão é "sobre o usuário": é o caso mais comum
        # e o menos surpreendente.
        return MemoryScope.USER


def register(registry: ToolRegistry, config, guard, memory: MemoryStore) -> None:
    def lembrar(args: dict) -> ToolResult:
        texto = str(args.get("texto", "")).strip()
        if not texto:
            return ToolResult.failure("Não entendi o que devo guardar.")
        scope = _scope_from(str(args.get("sobre", "usuario")))
        try:
            mensagem = memory.add(scope, texto)
        except MemoryFull as exc:
            return ToolResult(
                ok=False,
                speech=(
                    "Minha memória está cheia, senhor. Preciso esquecer algo antes "
                    "de guardar isso."
                ),
                data={"erro": str(exc)},
            )
        # SEM `ack`: guardar é interno.
        #
        # A mensagem do armazenamento ("Guardado.") virava fala, e o James
        # anunciava a própria contabilidade — "guardei isso na memória" — no
        # meio de uma conversa sobre outra coisa. Ninguém narra que está
        # formando uma lembrança; a pessoa só continua conversando.
        #
        # `data` continua indo para o modelo: ele PRECISA saber que deu certo
        # para não tentar de novo. O que sai é só a narração em voz alta.
        return ToolResult(ok=True, data={"resultado": mensagem})

    def esquecer(args: dict) -> ToolResult:
        trecho = str(args.get("trecho", "")).strip()
        if not trecho:
            return ToolResult.failure("Preciso saber o que esquecer.")
        # Procura nos dois arquivos: o usuário não pensa em termos de escopo.
        for scope in MemoryScope:
            resultado = memory.remove(scope, trecho)
            if resultado == "Esquecido.":
                return ToolResult(ok=True, data={"resultado": resultado})
        return ToolResult(
            ok=True, data={"resultado": "nao_encontrado"}
        )

    def atualizar_memoria(args: dict) -> ToolResult:
        antigo = str(args.get("trecho_antigo", "")).strip()
        novo = str(args.get("texto_novo", "")).strip()
        if not antigo or not novo:
            return ToolResult.failure("Preciso saber o que trocar e pelo quê.")
        for scope in MemoryScope:
            try:
                resultado = memory.replace(scope, antigo, novo)
            except MemoryFull as exc:
                return ToolResult.failure(str(exc))
            if resultado == "Atualizado.":
                return ToolResult(ok=True, data={"resultado": resultado})
        return ToolResult(
            ok=True, data={"resultado": "nao_encontrado"}
        )

    def consultar_memoria(args: dict) -> ToolResult:
        busca = str(args.get("busca", "")).strip()
        encontrados = memory.search(busca)
        if not encontrados:
            return ToolResult(
                ok=True, speech="Não tenho nada guardado sobre isso.", data={"itens": []}
            )
        itens = [entrada for _, entrada in encontrados]
        return ToolResult(
            ok=True,
            speech="Sobre isso eu sei: " + "; ".join(itens[:5]) + ".",
            data={"itens": itens},
        )

    registry.register(
        Tool(
            name="lembrar",
            description=(
                "Guarda um fato durável na memória. Use quando o usuário revelar "
                "uma preferência, corrigir você, ou contar um detalhe pessoal ou do "
                "ambiente que valha lembrar em conversas futuras. NÃO use para "
                "progresso de tarefa nem para o que é fácil redescobrir."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "texto": {
                        "type": "string",
                        "description": "O fato a guardar, em uma frase curta.",
                    },
                    "sobre": {
                        "type": "string",
                        "enum": ["usuario", "ambiente"],
                        "description": (
                            "'usuario' para preferências e detalhes pessoais; "
                            "'ambiente' para fatos desta máquina."
                        ),
                    },
                },
                "required": ["texto"],
            },
            handler=lembrar,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="esquecer",
            description="Remove da memória a entrada que contém o trecho indicado.",
            parameters={
                "type": "object",
                "properties": {
                    "trecho": {
                        "type": "string",
                        "description": "Trecho que identifica a entrada a remover.",
                    }
                },
                "required": ["trecho"],
            },
            handler=esquecer,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="atualizar_memoria",
            description=(
                "Corrige uma entrada da memória. Use quando o usuário disser que "
                "algo que você sabia mudou."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "trecho_antigo": {
                        "type": "string",
                        "description": "Trecho que identifica a entrada existente.",
                    },
                    "texto_novo": {"type": "string", "description": "A versão correta."},
                },
                "required": ["trecho_antigo", "texto_novo"],
            },
            handler=atualizar_memoria,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="consultar_memoria",
            description=(
                "Procura algo específico na memória. O que você já sabe está no seu "
                "contexto; use isto só para buscar detalhes que não estejam lá."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "busca": {"type": "string", "description": "Palavra-chave a procurar."}
                },
                "required": ["busca"],
            },
            handler=consultar_memoria,
            fire_and_forget=True,
        )
    )
