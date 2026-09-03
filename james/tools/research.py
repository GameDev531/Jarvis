"""Busca com conteúdo e pesquisa aprofundada.

Diferença para a `pesquisar_web` que já existia: aquela abre uma aba para o
usuário ler; estas trazem o conteúdo para dentro, para o modelo trabalhar em
cima.

Sobre custo de requisição: a pesquisa aprofundada faz várias buscas e abre
várias páginas, mas **nada disso gasta cota de LLM** — é tudo HTTP direto. O
modelo entra uma vez só, no fim, para sintetizar. Uma pesquisa que abre oito
páginas custa a mesma requisição que uma pergunta simples.

Todo conteúdo que volta daqui é externo e vai para o histórico envelopado pelo
sanitizador. Página web é o vetor clássico de prompt injection: o texto pode
conter instruções tentando redirecionar o James, e o system prompt manda tratar
o que está dentro do envelope como dado, nunca como ordem.
"""

from __future__ import annotations

from james.logs import audit, audit_text, get_logger
from james.tools.registry import Tool, ToolRegistry, ToolResult
from james.web.search import SearchError, fetch_page, search_web, termos_relevantes

logger = get_logger("james.tools.research")

MAX_PAGINAS = 6
MAX_CHARS_POR_PAGINA = 4000
# Cada rodada faz uma busca e abre páginas. Mais que três vira espera longa
# demais para uma resposta falada.
MAX_RODADAS = 3


def register(registry: ToolRegistry, config, guard) -> None:
    def buscar_na_web(args: dict) -> ToolResult:
        consulta = str(args.get("consulta", "")).strip()
        if not consulta:
            return ToolResult.failure("Preciso saber o que buscar.")
        limite = max(1, min(8, int(args.get("limite", 5) or 5)))

        try:
            resultados = search_web(consulta, limite=limite)
        except SearchError as exc:
            return ToolResult(ok=False, speech=str(exc), data={"erro": str(exc)})

        if not resultados:
            return ToolResult(
                ok=True,
                speech="Não encontrei resultados para isso.",
                data={"resultados": []},
            )

        audit("buscar_na_web", **audit_text(consulta, campo="consulta"), encontrados=len(resultados))
        return ToolResult(
            ok=True,
            data={"consulta": consulta, "resultados": [r.to_dict() for r in resultados]},
            external_content=True,
        )

    def ler_pagina(args: dict) -> ToolResult:
        # A URL vem resolvida e validada pelo guard, que já barrou esquema
        # perigoso, endereço interno e domínio bloqueado.
        url = str(args.get("url", "")).strip()
        if not url:
            return ToolResult.failure("Preciso do endereço da página.")

        try:
            titulo, texto = fetch_page(url, max_chars=MAX_CHARS_POR_PAGINA * 2)
        except SearchError as exc:
            return ToolResult(ok=False, speech=str(exc), data={"erro": str(exc)})

        if not texto:
            return ToolResult.failure("Essa página não tem texto que eu consiga ler.")

        audit("ler_pagina", url=url, caracteres=len(texto))
        return ToolResult(
            ok=True,
            data={"url": url, "titulo": titulo, "texto": texto},
            external_content=True,
        )

    def pesquisa_aprofundada(args: dict) -> ToolResult:
        pergunta = str(args.get("pergunta", "")).strip()
        if not pergunta:
            return ToolResult.failure("Preciso saber o que pesquisar.")

        rodadas = max(1, min(MAX_RODADAS, int(args.get("rodadas", 2) or 2)))
        consulta = pergunta
        visitadas: set[str] = set()
        fontes: list[dict] = []
        consultas_feitas: list[str] = []

        for rodada in range(rodadas):
            try:
                resultados = search_web(consulta, limite=4)
            except SearchError as exc:
                logger.info("Rodada %d falhou: %s", rodada + 1, exc)
                break

            consultas_feitas.append(consulta)
            texto_da_rodada: list[str] = []

            for resultado in resultados:
                if len(fontes) >= MAX_PAGINAS:
                    break
                chave = resultado.url.rstrip("/")
                if chave in visitadas:
                    continue
                visitadas.add(chave)

                try:
                    titulo, texto = fetch_page(
                        resultado.url, max_chars=MAX_CHARS_POR_PAGINA
                    )
                except SearchError as exc:
                    # Página que não abre é normal numa lista de resultados:
                    # registra e segue, sem derrubar a pesquisa inteira.
                    logger.debug("Pulei %s: %s", resultado.url, exc)
                    continue

                if not texto:
                    continue
                fontes.append(
                    {
                        "url": resultado.url,
                        "titulo": titulo or resultado.titulo,
                        "texto": texto,
                    }
                )
                texto_da_rodada.append(texto)

            if len(fontes) >= MAX_PAGINAS:
                break

            # Próxima rodada: refina a busca com os termos que apareceram no
            # que já foi lido. É o que torna a pesquisa "aprofundada" em vez de
            # apenas repetir a mesma consulta.
            if texto_da_rodada:
                termos = termos_relevantes(" ".join(texto_da_rodada), limite=4)
                novos = [t for t in termos if t.lower() not in pergunta.lower()]
                if novos:
                    consulta = f"{pergunta} {' '.join(novos[:3])}"

        if not fontes:
            return ToolResult(
                ok=False,
                speech="Não consegui reunir material sobre isso.",
                data={"erro": "nenhuma fonte legível"},
            )

        audit(
            "pesquisa_aprofundada",
            **audit_text(pergunta, campo="pergunta"),
            fontes=len(fontes),
            consultas=consultas_feitas,
        )
        return ToolResult(
            ok=True,
            data={
                "pergunta": pergunta,
                "consultas": consultas_feitas,
                "fontes": fontes,
                "orientacao": (
                    "Sintetize uma resposta a partir destas fontes. Diga quando "
                    "elas discordam entre si, e cite de onde veio cada afirmação "
                    "importante. Se o material não responder à pergunta, diga isso "
                    "em vez de preencher com suposição."
                ),
            },
            external_content=True,
        )

    registry.register(
        Tool(
            name="buscar_na_web",
            description=(
                "Busca na internet e traz os resultados (título, endereço e resumo) "
                "para você ler. Diferente de 'pesquisar_web', que só abre uma aba "
                "para o usuário. Use quando VOCÊ precisa da informação para responder."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "consulta": {"type": "string", "description": "O que buscar."},
                    "limite": {
                        "type": "integer",
                        "description": "Quantos resultados trazer (1 a 8). Padrão: 5.",
                    },
                },
                "required": ["consulta"],
            },
            handler=buscar_na_web,
            fire_and_forget=False,
        )
    )
    registry.register(
        Tool(
            name="ler_pagina",
            description=(
                "Abre um endereço e traz o texto da página para você ler. Use depois "
                "de uma busca, quando o resumo do resultado não for suficiente."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Endereço completo da página.",
                        "audit_mode": "plaintext",
                    }
                },
                "required": ["url"],
            },
            handler=ler_pagina,
            fire_and_forget=False,
        )
    )
    registry.register(
        Tool(
            name="pesquisa_aprofundada",
            description=(
                "Investiga um assunto a fundo: faz várias buscas encadeadas, abre as "
                "páginas mais relevantes e traz o material reunido para você "
                "sintetizar. Cada rodada refina a busca com o que foi aprendido na "
                "anterior. Use para perguntas que uma busca única não responde."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pergunta": {
                        "type": "string",
                        "description": "A pergunta a investigar, completa.",
                    },
                    "rodadas": {
                        "type": "integer",
                        "description": f"Rodadas de busca (1 a {MAX_RODADAS}). Padrão: 2.",
                    },
                },
                "required": ["pergunta"],
            },
            handler=pesquisa_aprofundada,
            fire_and_forget=False,
        )
    )
