"""Quais ferramentas o modelo vê neste turno.

O problema, medido antes de qualquer mudança: 33 ferramentas somam ~3.700
tokens de schema, e eles iam em TODO turno. "Que horas são" pagava a conta do
criador de planilha, do analisador de ações e do grafo de memória juntos.

Pior que o custo é a direção: cada ferramenta nova é imposto permanente em todo
turno futuro. Sem esta camada, "adicionar mais poderes ao James" e "deixar o
James mais barato e mais preciso" seriam objetivos opostos. Com ela, não são.

TRÊS COISAS QUE ESTA CAMADA NÃO É
---------------------------------

1. **Não é segurança.** Esconder uma ferramenta do modelo não impede nada: o
   guard é quem decide, e ele não sabe que packs existem. Se um dia esconder
   uma ferramenta virar a razão de ela ser segura, a trava real sumiu. Há teste
   parametrizado provando que o veredito do guard é idêntico com e sem pack.

2. **Não é uma segunda chamada de LLM.** Gastar uma requisição para escolher
   ferramentas economizaria tokens na principal e custaria uma viagem de rede
   inteira — numa máquina com 1938 ms de latência de conexão, seria trocar
   dinheiro por tempo, que é justamente o recurso escasso no caminho de voz.
   A seleção é determinística: palavra-chave e estado.

3. **Não é para acertar sempre.** Errar para menos é o único erro caro (o
   modelo não consegue fazer o que foi pedido), então o roteador é generoso, e
   existe uma saída de emergência: `mais_ferramentas`, sempre no CORE, com que
   o próprio modelo pede o pack que faltou. Custa uma volta a mais, só quando o
   roteador erra — e deixa o erro visível na trilha em vez de virar uma recusa
   inexplicável.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from james.config import normalize_text

# --------------------------------------------------------------------- packs

# CORE é o que se usa o tempo todo e é barato. A tentação é engordá-lo "porque
# vai que precisa" — e aí ele vira o catálogo inteiro com outro nome.
CORE = "core"

PACKS: dict[str, tuple[str, ...]] = {
    CORE: (
        "que_horas_sao",
        "abrir_app",
        "fechar_app",
        "ajustar_volume",
        "info_sistema",
        "briefing_do_dia",
        "mais_ferramentas",
        # Os modos são a PORTA para as outras capacidades: sem `ativar_modo`
        # sempre disponível, "liga o modo navegador" não tem como funcionar, e
        # o pack de navegador nunca chegaria a ser útil.
        "ativar_modo",
        "desativar_modo",
        "listar_modos",
    ),
    "memoria": (
        "lembrar",
        "consultar_memoria",
        "atualizar_memoria",
        "esquecer",
        "registrar_fato",
        "consultar_fatos",
        "revisar_fato",
        "relacionar",
        "como_se_conectam",
    ),
    "web": (
        "buscar_na_web",
        "pesquisar_web",
        "ler_pagina",
        "pesquisa_aprofundada",
        "abrir_pagina",
    ),
    "arquivos": (
        "listar_arquivos",
        "mover_arquivo",
        "renomear_arquivo",
        "organizar_arquivos",
    ),
    "escritorio": (
        "criar_apresentacao",
        "criar_planilha",
    ),
    "financas": (
        "analisar_acao",
        "comparar_acoes",
    ),
    "visao": (
        "ver_tela",
        "ver_camera",
    ),
    "agente": (
        "executar_sequencia",
    ),
    "habilidades": (
        "habilidades",
        "instalar_habilidade",
    ),
    "navegador": (
        "abrir_aba",
        "listar_abas",
        "inspecionar_pagina",
        "clicar_em",
        "preencher_campo",
        "fechar_aba",
    ),
}

# --------------------------------------------------------------- as palavras

# Uma palavra que aparece em muitos assuntos ("ver", "mostra") não entra: ela
# puxaria o pack errado com frequência e o roteador viraria ruído.
GATILHOS: dict[str, tuple[str, ...]] = {
    "memoria": (
        "lembra", "lembre", "lembrar", "memoria", "memorize", "anota", "anote",
        "guarda isso", "guarde isso", "esquece", "esqueca", "voce sabe sobre",
        "o que voce sabe", "quem e", "tem a ver com", "relacao", "relacionad",
        "nao esquece", "grava isso",
    ),
    "web": (
        "pesquisa", "pesquise", "procura na", "procure na", "busca", "busque",
        "google", "internet", "site", "pagina", "noticia", "artigo", "link",
        "abre o site", "na web", "online", "investiga", "investigue",
    ),
    "arquivos": (
        "arquivo", "arquivos", "pasta", "pastas", "diretorio", "downloads",
        "documentos", "area de trabalho", "desktop", "organiza", "organize",
        "renomeia", "renomeie", "move o", "mova o", "lista os",
    ),
    "escritorio": (
        "apresentacao", "slide", "slides", "powerpoint", "planilha", "excel",
        "tabela", "grafico", "relatorio",
    ),
    "financas": (
        "acao", "acoes", "bolsa", "investimento", "investir", "ticker",
        "petr", "vale3", "ibovespa", "dividendo", "cotacao", "mercado",
        "carteira", "renda fixa",
    ),
    "visao": (
        "na tela", "minha tela", "essa tela", "camera", "webcam", "print",
        "captura", "o que aparece", "olha a tela", "ve a tela", "essa imagem",
        "essa foto",
    ),
    "agente": (
        "sequencia", "passo a passo", "varias coisas", "primeiro", "depois",
        "para cada", "faz tudo", "automatiza", "automatize",
    ),
    "habilidades": (
        "habilidade", "habilidades", "skill", "skills", "instala",
        "aprende a", "aprenda a",
    ),
    "navegador": (
        "navegador", "chrome", "aba", "abas", "clica", "clique", "preenche",
        "preencha", "formulario", "botao", "campo", "inspeciona", "inspecione",
    ),
}

# Um modo ligado já é uma declaração de intenção mais forte que qualquer
# palavra na frase: quem ligou o modo navegador vai falar do navegador.
PACK_POR_MODO: dict[str, str] = {
    "navegador": "navegador",
    "visao": "visao",
    "gestos": "visao",
}


@dataclass(frozen=True)
class Selecao:
    """O que foi escolhido e por quê — o porquê é o que torna isto depurável."""

    packs: frozenset[str]
    motivos: tuple[str, ...] = ()
    completo: bool = False

    @property
    def resumo(self) -> str:
        return ", ".join(sorted(self.packs))


def _todos_os_packs() -> frozenset[str]:
    return frozenset(PACKS)


# Aberturas que pedem CONHECIMENTO, não ação. Ficam ancoradas no começo da
# frase de propósito: "me explica" no início é uma pergunta; no meio de "abre o
# chrome e me explica" é outra coisa, e aí os gatilhos de ação já falaram.
_ABERTURAS_DE_PERGUNTA = (
    "quem foi", "quem e", "quem sao", "o que e", "o que sao", "o que significa",
    "por que", "porque", "por quê", "como funciona", "como se", "qual e",
    "quais sao", "quanto e", "quantos", "quando foi", "onde fica",
    "me explica", "me explique", "explica", "explique", "me fala sobre",
    "me diz sobre", "voce acha", "o que voce acha", "sera que", "vale a pena",
)


# Palavras que abrem uma fala sem dizer nada sobre ela. Em texto escrito quase
# não aparecem; em FALA são a regra — "e o que você acha", "então me explica",
# "james, por que...". O benchmark mostrou o estrago: cinco de seis perguntas
# naturais caíam no catálogo inteiro porque tinham uma dessas na frente.
_ENFEITES = (
    "e", "entao", "ai", "mas", "ok", "beleza", "olha", "escuta", "ei",
    "james", "jarvis", "ultron", "cara", "po", "so", "agora", "bom",
    "hmm", "hm", "ah", "eh", "tipo", "veja", "diz", "me diz",
)


def _sem_enfeite(texto_normal: str) -> str:
    """Tira os enfeites do começo, um por vez.

    Um por vez, e não todos de uma: "e aí, james, o que você acha" tem três
    empilhados, e uma passada só deixaria dois.

    O teto é o número de palavras da frase, não uma constante: o laço já para
    quando nada muda, então o teto só existe contra o caso patológico — e um
    número fixo (4, por exemplo) faria uma transcrição ruidosa de dez sílabas
    sobrar com metade dos enfeites e ainda parecer um pedido.
    """
    texto = texto_normal
    for _ in range(len(texto_normal.split()) + 1):
        anterior = texto
        for enfeite in _ENFEITES:
            if texto.startswith(enfeite + " "):
                texto = texto[len(enfeite) + 1:].lstrip(" ,")
                break
            if texto.startswith(enfeite + ", "):
                texto = texto[len(enfeite) + 2:].lstrip(" ,")
                break
        if texto == anterior:
            break
    return texto


def _parece_pergunta(texto_normal: str) -> bool:
    limpo = _sem_enfeite(texto_normal)
    return any(limpo.startswith(a) for a in _ABERTURAS_DE_PERGUNTA)


# Cortesia não é pedido. Sem esta lista, "tudo bem com você" caía no fallback
# de catálogo inteiro por ter quatro palavras e nenhum gatilho — 34 schemas
# para responder "tudo ótimo, senhor".
_CORTESIAS = (
    "tudo bem", "tudo bom", "como voce esta", "como vai voce", "como voce vai",
    "bom dia", "boa tarde", "boa noite", "obrigado", "obrigada", "valeu",
    "muito obrigado", "ate mais", "ate logo", "tchau", "bom trabalho",
    "boa noite james", "oi james", "ola james", "e ai james",
)


def _parece_cortesia(texto_normal: str) -> bool:
    limpo = _sem_enfeite(texto_normal)
    return any(limpo.startswith(c) for c in _CORTESIAS)


def escolher_packs(
    texto: str,
    *,
    modos_ligados: frozenset[str] | tuple[str, ...] = (),
    forcados: frozenset[str] | tuple[str, ...] = (),
) -> Selecao:
    """Decide os packs deste turno. Determinístico, sem rede, sem modelo.

    `forcados` é a saída de emergência: o modelo pediu um pack por nome e o
    turno seguinte já vai com ele.
    """
    texto_normal = normalize_text(texto or "").lower()
    escolhidos = {CORE}
    motivos: list[str] = []

    for pack in forcados:
        if pack in PACKS:
            escolhidos.add(pack)
            motivos.append(f"{pack}: pedido pelo modelo")

    for modo in modos_ligados:
        pack = PACK_POR_MODO.get(modo)
        if pack:
            escolhidos.add(pack)
            motivos.append(f"{pack}: modo '{modo}' ligado")

    for pack, palavras in GATILHOS.items():
        achado = next((p for p in palavras if p in texto_normal), None)
        if achado:
            escolhidos.add(pack)
            motivos.append(f"{pack}: '{achado}'")

    # A contagem é sobre o texto SEM enfeite. "e aí, james, oi" tem quatro
    # palavras e é um "oi" — e um assistente de voz recebe muito disso:
    # transcrição truncada, ruído virando sílaba, começo de frase que a pessoa
    # abandonou. Contar o enfeite mandaria o catálogo inteiro para o barulho,
    # que é o caminho mais caro que existe.
    essencial = _sem_enfeite(texto_normal)
    if len(escolhidos) == 1 and len(essencial.split()) > 3:
        # Sem gatilho nenhum, e a frase é longa o bastante para ser um pedido.
        # Aqui o roteador sabe menos, e é onde errar para menos custa mais.
        #
        # Mas "catálogo inteiro" seria a resposta errada para metade destes
        # casos, e foi o benchmark que mostrou: "quem foi Alan Turing" e "me
        # explica o que é entropia" recebiam as 34 ferramentas — 100% do
        # catálogo para perguntas que não precisam de ferramenta NENHUMA. O
        # pior caso caía exatamente onde ele era menos justificável.
        #
        # Uma pergunta de conhecimento tem no máximo dois destinos plausíveis:
        # procurar na web, ou lembrar de algo que você já contou. Os dois packs
        # custam um terço do catálogo — e se mesmo assim faltar, `mais_
        # ferramentas` está no CORE.
        if _parece_cortesia(texto_normal):
            return Selecao(packs=frozenset(escolhidos), motivos=("cortesia",))
        if _parece_pergunta(texto_normal):
            return Selecao(
                packs=frozenset({CORE, "web", "memoria"}),
                motivos=("pergunta sem gatilho: conhecimento, web e memória",),
            )
        return Selecao(
            packs=_todos_os_packs(),
            motivos=("pedido sem gatilho: catálogo inteiro",),
            completo=True,
        )

    return Selecao(packs=frozenset(escolhidos), motivos=tuple(motivos))


def ferramentas_dos_packs(packs: frozenset[str] | tuple[str, ...]) -> frozenset[str]:
    nomes: set[str] = set()
    for pack in packs:
        nomes.update(PACKS.get(pack, ()))
    return frozenset(nomes)


def pack_da_ferramenta(nome: str) -> str | None:
    for pack, ferramentas in PACKS.items():
        if nome in ferramentas:
            return pack
    return None


def packs_disponiveis(catalogo: frozenset[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Os packs que têm ao menos uma ferramenta REALMENTE registrada.

    Anunciar ao modelo um pack vazio seria oferecer uma porta para uma sala que
    não existe — ele pediria, receberia nada, e não teria como entender por quê.
    """
    presentes = set(catalogo)
    return tuple(
        sorted(
            pack
            for pack, ferramentas in PACKS.items()
            if pack != CORE and presentes.intersection(ferramentas)
        )
    )
