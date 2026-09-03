"""O que a trilha de auditoria pode gravar — decidido pelo SCHEMA, não pelo nome.

## O buraco que este arquivo fecha

A redação de segredos era por nome de chave: `token`, `password`, `api_key`.
Isso pega a chave da API e não pega nada do que o usuário realmente digita:

    audit("tool_executada", tool="preencher_campo",
          args={"seletor": "#email", "valor": "meu@email.com"})

`valor` não está em lista nenhuma de segredo, então ia para o disco em texto
puro. O mesmo vale para `audit("comando", texto=...)`, que gravava a frase
inteira do usuário em toda interação. Log vira ZIP, print de tela e anexo de
e-mail pedindo ajuda: uma trilha com o que a pessoa digitou é um vazamento com
carimbo de data.

O nome da chave é a informação errada para decidir isso. A informação certa
está no schema da ferramenta, que é quem sabe se um argumento é um identificador
(`chrome`), um endereço (`https://...`) ou conteúdo autoral do usuário.

## O modelo

Cada argumento tem um MODO de auditoria:

    plaintext   grava o valor        — só para o que é enumerável/identificador
    metadata    grava tipo e tamanho — `<redacted:18 chars>`
    hash        grava um digest curto — permite correlacionar sem revelar
    redact      grava `***`          — nem o tamanho sai

O modo vem, nesta ordem:

    1. anotação no schema do argumento (`audit_mode:` ou `sensitive: true`)
    2. padrão da própria ferramenta (`Tool.audit_default`)
    3. padrão do MODO DE PRIVACIDADE global

Com uma ressalva que é o ponto de existir um modo global: `minimal` e
`debug_explicit` valem SOBRE o padrão da ferramenta. Um deles é mais
restritivo que qualquer padrão e o outro é um opt-in consciente; deixar a
ferramenta vencer os dois esvaziaria a chave que o usuário girou. O que
nenhum dos dois derruba é a anotação `redact`/`sensitive` do schema — essa é
a trava, e trava não tem modo de depuração.

E o padrão global falha FECHADO: sob `standard`, um argumento de texto que
ninguém anotou vira `metadata`. Uma ferramenta nova, escrita amanhã, não vaza
conteúdo por esquecimento — no máximo perde detalhe na trilha, que é o erro
barato dos dois.

## Modos de privacidade (seção 26)

    minimal          nada de conteúdo: todo argumento vira metadata/redact
    standard         padrão. Conteúdo só quando o schema declara plaintext
    debug_explicit   grava tudo. Exige opt-in no config e um aviso no log

`debug_explicit` existe porque depurar sem ver os argumentos é quase
impossível — mas ele nunca é o padrão, e ligá-lo é uma decisão consciente.
"""

from __future__ import annotations

import hashlib
import threading
from enum import Enum
from typing import Any, Mapping

# Chave de anotação dentro do schema JSON de um argumento. É removida antes de
# o schema ir para o modelo (ver Tool.schema): é metadado nosso, não contrato
# da API do provedor.
CHAVE_MODO = "audit_mode"
CHAVE_SENSIVEL = "sensitive"

# Acima disto, mesmo um valor "plaintext" é cortado: uma trilha não é lugar
# para o conteúdo de uma página inteira.
MAX_PLAINTEXT = 240


class AuditMode(str, Enum):
    PLAINTEXT = "plaintext"
    METADATA = "metadata"
    HASH = "hash"
    REDACT = "redact"


class PrivacyMode(str, Enum):
    MINIMAL = "minimal"
    STANDARD = "standard"
    DEBUG_EXPLICIT = "debug_explicit"


_lock = threading.RLock()
_modo_atual = PrivacyMode.STANDARD


def _como_enum(valor: Any, enum_cls):
    """Aceita o membro do enum ou o texto dele. `None` se não reconhecer.

    O `str()` de um membro de enum com mixin `str` devolve
    "PrivacyMode.MINIMAL", e não "minimal" — converter pelo texto sem tratar
    esse caso transformaria todo membro passado diretamente em valor inválido.
    """
    if isinstance(valor, enum_cls):
        return valor
    try:
        return enum_cls(str(valor).strip().lower())
    except ValueError:
        return None


def set_privacy_mode(modo: str | PrivacyMode) -> PrivacyMode:
    """Define o modo global. Valor desconhecido cai no mais restritivo."""
    global _modo_atual
    with _lock:
        _modo_atual = _como_enum(modo, PrivacyMode) or PrivacyMode.MINIMAL
        return _modo_atual


def get_privacy_mode() -> PrivacyMode:
    with _lock:
        return _modo_atual


def _digest(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8", "replace")).hexdigest()[:12]


def _descrever(value: Any) -> str:
    """Metadado: o que dá para dizer sem revelar o conteúdo.

    Texto sai como `<redacted:N chars>` — o tamanho é útil para investigar
    ("o campo recebeu 18 caracteres, então não ficou vazio") e não conta nada
    sobre o conteúdo.
    """
    if isinstance(value, str):
        return f"<redacted:{len(value)} chars>"
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, bool):
        return f"<bool:{value}>"
    if isinstance(value, (int, float)):
        return f"<{type(value).__name__}>"
    if isinstance(value, (list, tuple)):
        return f"<list:{len(value)} itens>"
    if isinstance(value, dict):
        return f"<dict:{len(value)} chaves>"
    if value is None:
        return "<none>"
    return f"<{type(value).__name__}>"


def aplicar_modo(value: Any, mode: AuditMode) -> Any:
    """Transforma um valor conforme o modo. Esta é a única porta de saída."""
    if mode is AuditMode.REDACT:
        return "***"
    if mode is AuditMode.METADATA:
        return _descrever(value)
    if mode is AuditMode.HASH:
        if value is None:
            return "<none>"
        return f"<sha256:{_digest(str(value))}>"

    # PLAINTEXT — ainda com teto, e ainda descrevendo o que não é texto curto.
    if isinstance(value, str):
        if len(value) <= MAX_PLAINTEXT:
            return value
        return f"{value[:MAX_PLAINTEXT]}…<+{len(value) - MAX_PLAINTEXT} chars>"
    if isinstance(value, bytes):
        return _descrever(value)
    return value


def modo_do_schema(propriedade: Mapping[str, Any] | None) -> AuditMode | None:
    """Lê a anotação de um argumento. `None` quando não há anotação.

    Aceita as duas formas pedidas na auditoria: `audit_mode: <modo>` para o
    controle fino e `sensitive: true` como atalho para o mais restritivo.
    """
    if not isinstance(propriedade, Mapping):
        return None

    declarado = propriedade.get(CHAVE_MODO)
    if declarado is not None:
        # Anotação errada não pode virar permissão: falha fechado.
        return _como_enum(declarado, AuditMode) or AuditMode.REDACT

    if propriedade.get(CHAVE_SENSIVEL):
        return AuditMode.REDACT
    return None


def _padrao_global(value: Any, tool_default: AuditMode | None) -> AuditMode:
    """O modo de um argumento SEM anotação, conforme o modo de privacidade."""
    modo = get_privacy_mode()

    if modo is PrivacyMode.DEBUG_EXPLICIT:
        return AuditMode.PLAINTEXT
    if modo is PrivacyMode.MINIMAL:
        return AuditMode.METADATA
    if tool_default is not None:
        return tool_default

    # STANDARD: texto é conteúdo até prova em contrário — a prova é a anotação
    # `audit_mode: plaintext` no schema. Escalares (número, booleano, enum
    # curto) não carregam conteúdo autoral e passam.
    if isinstance(value, (str, bytes)):
        return AuditMode.METADATA
    if isinstance(value, (list, tuple, dict)):
        return AuditMode.METADATA
    return AuditMode.PLAINTEXT


# Quão restritivo é cada modo. `hash` e `metadata` empatam: os dois revelam
# uma propriedade do valor (tamanho, igualdade) e nenhum revela o conteúdo.
_RESTRITIVIDADE = {
    AuditMode.PLAINTEXT: 0,
    AuditMode.HASH: 1,
    AuditMode.METADATA: 1,
    AuditMode.REDACT: 2,
}


def _mais_restritivo(a: AuditMode, b: AuditMode | None) -> AuditMode:
    if b is None:
        return a
    return a if _RESTRITIVIDADE[a] >= _RESTRITIVIDADE[b] else b


def _piso_do_modo_global() -> AuditMode | None:
    """O mínimo de proteção que o modo global impõe, mesmo sobre uma anotação.

    Uma anotação `plaintext` é uma PERMISSÃO que o autor da ferramenta deu.
    `minimal` é o usuário revogando permissões — e revogação vence permissão,
    senão a chave não faria nada. O caminho contrário não vale: `debug_explicit`
    não derruba uma anotação restritiva, porque essa é uma TRAVA, e trava não
    tem modo de depuração.
    """
    if get_privacy_mode() is PrivacyMode.MINIMAL:
        return AuditMode.METADATA
    return None


def redact_args(
    args: Mapping[str, Any] | None,
    policy: Mapping[str, AuditMode | None] | None = None,
    tool_default: AuditMode | None = None,
) -> dict[str, Any]:
    """Aplica a política por argumento.

    Três situações, e a diferença entre elas importa:

      - argumento DECLARADO E ANOTADO  -> o modo da anotação;
      - argumento DECLARADO sem anotação -> padrão da ferramenta/global;
      - argumento NÃO DECLARADO (o modelo inventou o nome) -> `redact`. Não
        sabemos o que é, então não vai para o disco.

    Sem política nenhuma (ferramenta desconhecida) tudo cai no padrão global,
    que já falha fechado para texto.
    """
    if not args:
        return {}
    policy = dict(policy or {})
    saida: dict[str, Any] = {}
    for chave, valor in args.items():
        if chave in policy:
            modo = policy[chave] or _padrao_global(valor, tool_default)
        elif policy:
            modo = (
                AuditMode.PLAINTEXT
                if get_privacy_mode() is PrivacyMode.DEBUG_EXPLICIT
                else AuditMode.REDACT
            )
        else:
            modo = _padrao_global(valor, tool_default)
        saida[chave] = aplicar_modo(valor, _mais_restritivo(modo, _piso_do_modo_global()))
    return saida


def audit_text(texto: str | None, *, campo: str = "texto") -> dict[str, Any]:
    """O que gravar sobre uma FALA do usuário (comando, confirmação).

    Por padrão, nada do conteúdo: só o tamanho e um digest que permite dizer
    "foi o mesmo comando de antes" sem dizer qual foi. A transcrição completa
    exige `debug_explicit` — que é o opt-in pedido na auditoria.
    """
    texto = texto or ""
    if get_privacy_mode() is PrivacyMode.DEBUG_EXPLICIT:
        return {campo: aplicar_modo(texto, AuditMode.PLAINTEXT)}
    if not texto:
        return {f"{campo}_chars": 0}
    return {f"{campo}_chars": len(texto), f"{campo}_hash": _digest(texto)}


def limpar_schema(parameters: Mapping[str, Any] | None) -> dict[str, Any]:
    """Devolve o schema SEM as anotações de auditoria.

    O que o modelo recebe tem que continuar sendo JSON Schema puro: campo
    desconhecido pode ser rejeitado por provedor com validação estrita, e de
    qualquer forma gasta tokens sem informar nada ao modelo.
    """
    if not isinstance(parameters, Mapping):
        return {"type": "object", "properties": {}}

    def _limpo(valor: Any) -> Any:
        if isinstance(valor, Mapping):
            return {
                chave: _limpo(item)
                for chave, item in valor.items()
                if chave not in (CHAVE_MODO, CHAVE_SENSIVEL)
            }
        if isinstance(valor, list):
            return [_limpo(item) for item in valor]
        return valor

    return _limpo(dict(parameters))


def politica_do_schema(
    parameters: Mapping[str, Any] | None,
) -> dict[str, AuditMode | None]:
    """Extrai `{argumento: modo}` do schema, incluindo os NÃO anotados.

    Um argumento declarado e sem anotação entra com `None`, e não fica de fora:
    é assim que `redact_args` distingue "declarado, decide pelo padrão" de
    "nome que o modelo inventou, não vai para o disco". O modo dos não anotados
    é resolvido na hora de auditar, e não aqui, porque depende do VALOR (um
    número e uma string no mesmo argumento merecem tratamento diferente) e do
    modo de privacidade vigente naquele instante.
    """
    if not isinstance(parameters, Mapping):
        return {}
    propriedades = parameters.get("properties")
    if not isinstance(propriedades, Mapping):
        return {}
    return {str(nome): modo_do_schema(definicao) for nome, definicao in propriedades.items()}
