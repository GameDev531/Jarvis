"""Roteador local: comandos frequentes sem gastar requisição.

Papel revisado (ver E2 do plano): como o caminho comum manda o áudio direto ao
Gemini, o roteador NÃO fica na frente de tudo — colocá-lo lá obrigaria a
transcrever localmente toda frase, que é justamente a etapa lenta que o áudio
direto elimina. Ele serve para:

  1. modo degradado (sem internet / sem cota), onde é a única forma de agir;
  2. execução em paralelo com a requisição ao Gemini, cancelando-a quando casa
     primeiro — economiza cota sem custar latência.

Princípio de projeto: falhar em casar é barato (custa uma requisição); casar
errado é caro (executa a ação errada). Por isso o roteador é deliberadamente
conservador — na dúvida, não casa.

E o mais importante: o que sai daqui é uma INTENÇÃO, não uma execução. Continua
passando pelo guard igual a qualquer chamada vinda do LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from james.config import normalize_text

# Indícios de que a frase tem mais de uma ação ("abre o chrome e pesquisa X").
# Encadeamento é trabalho do LLM; o roteador só trata comando único.
_COMPOUND = re.compile(
    r"\b(?:e (?:depois|tambem|ai)|,\s*(?:depois|dai)|"
    r"e (?:abre|abra|pesquisa|pesquise|procura|busca|fecha|feche|toca))\b"
)

_PATTERNS: list[tuple[re.Pattern[str], str, dict[str, Any]]] = [
    # --- horas ---
    (
        re.compile(r"^(?:me diz(?:er)? )?(?:que |qual )?(?:horas? (?:sao|e|eh)|hora e|as horas)\??$"),
        "que_horas_sao",
        {},
    ),
    # --- volume ---
    (
        re.compile(r"^(?:aumenta(?:r)?|sobe|subir|levanta) (?:o |a )?(?:volume|som)$"),
        "ajustar_volume",
        {"acao": "aumentar"},
    ),
    (
        re.compile(r"^(?:diminui(?:r)?|abaixa(?:r)?|baixa(?:r)?|reduz(?:ir)?) (?:o |a )?(?:volume|som)$"),
        "ajustar_volume",
        {"acao": "diminuir"},
    ),
    (
        re.compile(r"^(?:muta(?:r)?|silencia(?:r)?|tira o som|corta o som|sem som)$"),
        "ajustar_volume",
        {"acao": "mutar"},
    ),
    # --- sistema ---
    (
        re.compile(r"^(?:como (?:esta|anda) o (?:pc|computador|sistema)|info(?:rmacoes)? do sistema|status do sistema)\??$"),
        "info_sistema",
        {},
    ),
]

_APP_PATTERN = re.compile(
    r"^(?:abre|abrir|abra|inicia|iniciar|executa|executar|roda|rodar|liga) "
    r"(?:o |a |os |as )?(?P<nome>.+?)\s*$"
)
_SEARCH_PATTERN = re.compile(
    r"^(?:pesquisa(?:r)?|pesquise|procura(?:r)?|procure|busca(?:r)?|busque|google|"
    r"da uma olhada) (?:sobre |por |o |a |no |na )?(?P<query>.+?)\s*$"
)


@dataclass
class RouterMatch:
    """Intenção casada localmente. Ainda passa pelo guard antes de executar."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    source: str = "router"
    matched_text: str = ""


class LocalRouter:
    def __init__(self, app_whitelist: dict[str, str], enabled: bool = True) -> None:
        self.enabled = bool(enabled)
        self.apps = app_whitelist or {}

    def match(self, transcript: str | None) -> RouterMatch | None:
        """Casa um comando frequente. None significa "manda pro LLM"."""
        if not self.enabled or not transcript:
            return None

        text = normalize_text(transcript)
        if not text:
            return None
        # Pontuação final atrapalha a âncora `$` dos padrões.
        text = text.rstrip(".!?,;")
        if not text:
            return None

        if _COMPOUND.search(text):
            return None

        for pattern, tool, args in _PATTERNS:
            if pattern.match(text):
                return RouterMatch(tool=tool, args=dict(args), matched_text=text)

        app_match = _APP_PATTERN.match(text)
        if app_match:
            name = app_match.group("nome").strip()
            # Só casa se o nome for exatamente um app da whitelist. Sem isso,
            # "abre a janela pra ventilar" viraria uma tentativa de abrir app.
            if name in self.apps:
                return RouterMatch(
                    tool="abrir_app", args={"nome": name}, matched_text=text
                )
            return None

        search_match = _SEARCH_PATTERN.match(text)
        if search_match:
            query = search_match.group("query").strip()
            if len(query) >= 2:
                # A query volta do texto original para preservar acentuação;
                # a normalização serve só para reconhecer o comando.
                original = _recover_query(transcript, query)
                return RouterMatch(
                    tool="pesquisar_web", args={"query": original}, matched_text=text
                )
        return None


def _recover_query(original_text: str, normalized_query: str) -> str:
    """Recupera o trecho acentuado correspondente ao final normalizado.

    O casamento acontece sobre texto sem acento, mas a busca deve usar o que o
    usuário realmente disse ("São Paulo", não "sao paulo"). Como a normalização
    preserva o número de palavras, basta pegar as N últimas do texto original.
    """
    words = original_text.strip().rstrip(".!?,;").split()
    count = len(normalized_query.split())
    if 0 < count <= len(words):
        return " ".join(words[-count:])
    return normalized_query
