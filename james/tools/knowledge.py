"""Ferramentas das duas camadas de conhecimento: fatos e habilidades.

**Fatos** (memória profunda) — o que o James sabe. Fica em SQLite, não entra no
prompt sozinho, e é consultado quando faz falta. Diferente da memória curada,
que é pequena e está sempre no contexto.

**Habilidades** — como o James faz. Instruções especializadas por assunto,
carregadas só quando o modelo decide que precisa daquela referência.

As duas seguem a mesma divisão do resto do projeto: o código entrega os dados,
a leitura é do modelo. Em particular, `consultar_fatos` no modo contradições
devolve *candidatos* — pares que dividem entidade e têm sobreposição de termos.
Se realmente se contradizem, quem julga é o modelo: SQL não entende negação,
ironia nem mudança de contexto no tempo.
"""

from __future__ import annotations

from james.logs import get_logger
from james.memory.fact_store import FactStore, FactStoreFull
from james.skills.installer import SkillInstallError, install_skill
from james.skills.registry import SkillRegistry
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.knowledge")

_ACOES_REVISAO = ("confirmar", "refutar", "remover")


def register_facts(registry: ToolRegistry, config, guard, facts: FactStore) -> None:
    def registrar_fato(args: dict) -> ToolResult:
        texto = str(args.get("texto", "")).strip()
        if not texto:
            return ToolResult.failure("Não entendi o que devo registrar.")

        entidades = args.get("entidades") or []
        if isinstance(entidades, str):
            entidades = [e for e in entidades.split(",") if e.strip()]

        try:
            fato = facts.add(texto, [str(e) for e in entidades], fonte="conversa")
        except FactStoreFull as exc:
            return ToolResult(
                ok=False,
                speech="Minha memória de fatos está cheia, senhor.",
                data={"erro": str(exc)},
            )

        if fato is None:
            # Duplicata: o fato já existia e ganhou confiança.
            return ToolResult(ok=True, ack="Isso eu já sabia.", data={"duplicata": True})
        return ToolResult(ok=True, ack="Anotado.", data=fato.to_dict())

    def consultar_fatos(args: dict) -> ToolResult:
        modo = str(args.get("modo", "busca")).strip().lower()

        if modo == "entidade":
            alvo = str(args.get("entidade", "")).strip()
            if not alvo:
                return ToolResult.failure("Preciso saber sobre quem ou o quê.")
            encontrados = facts.probe(alvo)
            relacionadas = facts.related(alvo)
            if not encontrados:
                return ToolResult(
                    ok=True, speech=f"Não tenho nada guardado sobre {alvo}.", data={"fatos": []}
                )
            return ToolResult(
                ok=True,
                data={
                    "entidade": alvo,
                    "fatos": [f.to_dict() for f in encontrados],
                    "relacionadas": [
                        {"entidade": nome, "fatos_em_comum": n} for nome, n in relacionadas
                    ],
                },
            )

        if modo == "contradicoes":
            pares = facts.contradictions()
            if not pares:
                return ToolResult(
                    ok=True,
                    speech="Não encontrei nada que pareça se contradizer.",
                    data={"pares": []},
                )
            return ToolResult(
                ok=True,
                data={
                    "pares": [
                        {"primeiro": a.to_dict(), "segundo": b.to_dict()} for a, b in pares
                    ],
                    "lembrete": (
                        "Estes são apenas candidatos, achados por sobreposição de "
                        "termos. Avalie se realmente se contradizem antes de agir."
                    ),
                },
            )

        busca = str(args.get("busca", "")).strip()
        if not busca:
            return ToolResult.failure("Preciso de algo para procurar.")
        encontrados = facts.search(busca)
        if not encontrados:
            return ToolResult(
                ok=True, speech="Não achei nada sobre isso na memória.", data={"fatos": []}
            )
        return ToolResult(ok=True, data={"fatos": [f.to_dict() for f in encontrados]})

    def revisar_fato(args: dict) -> ToolResult:
        try:
            fato_id = int(args.get("id"))
        except (TypeError, ValueError):
            return ToolResult.failure("Preciso do número do fato.")

        acao = str(args.get("acao", "")).strip().lower()
        if acao not in _ACOES_REVISAO:
            return ToolResult.failure("Não entendi o que fazer com esse fato.")

        if acao == "remover":
            if not facts.remove(fato_id):
                return ToolResult.failure("Não encontrei esse fato.")
            return ToolResult(ok=True, ack="Removido.", data={"id": fato_id})

        nova = facts.confirm(fato_id) if acao == "confirmar" else facts.refute(fato_id)
        if nova is None:
            return ToolResult.failure("Não encontrei esse fato.")
        return ToolResult(
            ok=True,
            ack="Anotado." if acao == "confirmar" else "Vou considerar isso duvidoso.",
            data={"id": fato_id, "confianca": round(nova, 2)},
        )

    registry.register(
        Tool(
            name="registrar_fato",
            description=(
                "Guarda um fato na memória profunda, com as entidades que ele "
                "menciona (pessoas, projetos, lugares). Use para informação que "
                "vale consultar depois mas não precisa estar sempre em mente — "
                "para preferências e hábitos do usuário, prefira 'lembrar'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "texto": {"type": "string", "description": "O fato, em uma frase."},
                    "entidades": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Pessoas, projetos ou lugares citados no fato.",
                    },
                },
                "required": ["texto"],
            },
            handler=registrar_fato,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="consultar_fatos",
            description=(
                "Consulta a memória profunda. Modo 'busca' procura por palavras; "
                "modo 'entidade' traz tudo sobre uma pessoa, projeto ou lugar, mais "
                "o que aparece junto; modo 'contradicoes' lista fatos guardados que "
                "podem estar em conflito, para revisão."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "modo": {
                        "type": "string",
                        "enum": ["busca", "entidade", "contradicoes"],
                        "description": "Como consultar. Padrão: busca.",
                    },
                    "busca": {"type": "string", "description": "Palavras a procurar."},
                    "entidade": {"type": "string", "description": "Nome da entidade."},
                },
            },
            handler=consultar_fatos,
            fire_and_forget=False,
        )
    )
    registry.register(
        Tool(
            name="revisar_fato",
            description=(
                "Ajusta um fato já guardado: 'confirmar' quando o usuário valida, "
                "'refutar' quando ele diz que está errado ou mudou, 'remover' quando "
                "não faz mais sentido existir. Um fato refutado duas vezes some das "
                "buscas mas não é apagado."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Número do fato."},
                    "acao": {
                        "type": "string",
                        "enum": list(_ACOES_REVISAO),
                        "description": "O que fazer com ele.",
                    },
                },
                "required": ["id", "acao"],
            },
            handler=revisar_fato,
            fire_and_forget=True,
        )
    )


def register_skills(registry: ToolRegistry, config, guard, skills: SkillRegistry) -> None:
    destino = skills.directory
    fontes_confiaveis = list(config.get("skills.fontes_confiaveis", []) or [])

    def habilidades(args: dict) -> ToolResult:
        acao = str(args.get("acao", "listar")).strip().lower()

        if acao == "carregar":
            nome = str(args.get("nome", "")).strip()
            conteudo = skills.load(nome)
            if conteudo is None:
                disponiveis = ", ".join(s.nome for s in skills.list_all()) or "nenhuma"
                return ToolResult.failure(
                    f"Não tenho a habilidade '{nome}'. Disponíveis: {disponiveis}."
                )
            # Conteúdo de arquivo, possivelmente de terceiros: entra no
            # histórico envelopado como dado, não como instrução do sistema.
            return ToolResult(ok=True, data=conteudo, external_content=True)

        if acao == "buscar":
            consulta = str(args.get("busca", "")).strip()
            encontradas = skills.search(consulta)
            return ToolResult(
                ok=True, data={"habilidades": [s.to_dict() for s in encontradas]}
            )

        todas = skills.list_all()
        return ToolResult(ok=True, data={"habilidades": [s.to_dict() for s in todas]})

    def instalar_habilidade(args: dict) -> ToolResult:
        fonte = str(args.get("fonte", "")).strip()
        nome = str(args.get("nome", "")).strip()
        try:
            caminho = install_skill(fonte, nome, destino, fontes_confiaveis)
        except SkillInstallError as exc:
            logger.info("Instalação de habilidade recusada: %s", exc)
            return ToolResult(ok=False, speech=str(exc), data={"erro": str(exc)})

        skills.refresh(forcar=True)
        return ToolResult(
            ok=True,
            ack=f"Habilidade {nome} instalada.",
            data={"nome": nome, "fonte": fonte, "caminho": str(caminho)},
        )

    registry.register(
        Tool(
            name="habilidades",
            description=(
                "Instruções especializadas por assunto. Use 'listar' para ver o que "
                "existe, 'buscar' para achar por palavra-chave, e 'carregar' para ler "
                "uma antes de executar uma tarefa daquele tipo. Carregue a habilidade "
                "ANTES de começar a tarefa, não depois."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "acao": {
                        "type": "string",
                        "enum": ["listar", "buscar", "carregar"],
                        "description": "O que fazer. Padrão: listar.",
                    },
                    "nome": {"type": "string", "description": "Nome da habilidade a carregar."},
                    "busca": {"type": "string", "description": "Palavra-chave para buscar."},
                },
            },
            handler=habilidades,
            fire_and_forget=False,
        )
    )
    registry.register(
        Tool(
            name="instalar_habilidade",
            description=(
                "Baixa uma habilidade nova de um repositório confiável. Use só quando "
                "o usuário pedir explicitamente ou quando nenhuma habilidade local "
                "servir para a tarefa."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "fonte": {
                        "type": "string",
                        "description": "Repositório no formato dono/projeto.",
                    },
                    "nome": {"type": "string", "description": "Nome da habilidade."},
                },
                "required": ["fonte", "nome"],
            },
            handler=instalar_habilidade,
            fire_and_forget=True,
        )
    )
