"""Ações que MUDAM a página — e as que nunca acontecem.

Você pediu que o James mexesse na máquina "mas nunca apague arquivos graves do
disco". O equivalente no navegador é este arquivo.

Preencher formulário é a ação mais perigosa que um assistente de voz pode fazer
sozinho, e o motivo não é óbvio: o risco não é ele digitar errado, é ele digitar
**no campo errado**. Um `input[type=password]` e um `input[type=text]` são
irmãos no DOM; a diferença entre preencher um e outro é a diferença entre
preencher um formulário e vazar uma credencial para o histórico do modelo.

Por isso a recusa é DETERMINÍSTICA e mora aqui, não no prompt:

  - campo de senha: recusado, sempre. Sem exceção, sem confirmação possível.
  - cartão, CVV, CPF: recusados por nome e por `autocomplete`.
  - upload de arquivo: recusado — mandar um arquivo do disco para um site é
    exfiltração, e nenhuma frase falada deveria poder causar isso.

Um modelo convencido a "só desta vez" não consegue passar daqui, porque não é
ele quem decide. É a mesma regra do guard: o LLM decide O QUE fazer; o código
decide SE pode.
"""

from __future__ import annotations

import re

from james.logs import audit, get_logger

logger = get_logger("james.browser.acoes")


class AcaoRecusada(RuntimeError):
    """A ação bateu numa trava. Não é erro de execução — é recusa."""


# Tipos de campo em que nunca se digita, por mais que peçam.
TIPOS_PROIBIDOS = frozenset({"password", "file", "hidden"})

# Nome, id, `autocomplete` ou rótulo que denuncia campo sensível. É por
# substring de propósito: "senha_atual", "novaSenha" e "user-password" contam.
# `_S` = separador opcional. Escrever "-?" pegaria "api-key" e deixaria passar
# "api_key" e "api key" — e o campo é o mesmo campo. Um buraco desses não
# aparece em teste feliz: aparece no dia em que o site usa sublinhado.
_S = r"[-_. ]?"
_PADRAO_SENSIVEL = re.compile(
    rf"senha|password|passwd|pwd|"
    rf"cart[aã]o|card{_S}number|cc{_S}number|credit{_S}card|"
    rf"cvv|cvc|security{_S}code|"
    rf"cpf|cnpj|ssn|social{_S}security|"
    rf"\bpin\b|\botp\b|token|secret|api{_S}key",
    re.IGNORECASE,
)


def _texto_do_campo(info: dict) -> str:
    return " ".join(
        str(info.get(k) or "")
        for k in ("nome", "id", "name", "autocomplete", "rotulo", "placeholder", "seletor")
    )


def conferir_campo(info: dict) -> None:
    """Levanta `AcaoRecusada` se este campo não pode receber texto.

    `info` é o que a página conta sobre o campo — tipo, nome, autocomplete.
    """
    tipo = str(info.get("tipo") or "").lower()
    if tipo in TIPOS_PROIBIDOS:
        audit("navegador_recusado", motivo=f"tipo={tipo}", seletor=info.get("seletor"))
        raise AcaoRecusada(
            f"Não preencho campo do tipo '{tipo}'. Senha, arquivo e campo oculto "
            "ficam com o senhor — isso não é negociável."
        )

    achado = _PADRAO_SENSIVEL.search(_texto_do_campo(info))
    if achado:
        audit("navegador_recusado", motivo=f"padrao={achado.group(0)}",
              seletor=info.get("seletor"))
        raise AcaoRecusada(
            f"Esse campo parece ser de dado sensível ({achado.group(0)}). "
            "Digite o senhor mesmo — eu não toco nele."
        )


# Script que descreve um campo ANTES de digitar. A pergunta "o que é este
# elemento?" tem que ser respondida pela página, não pelo modelo: o modelo
# poderia afirmar que um campo de senha é um campo de e-mail.
_DESCREVER = r"""
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const rotulo = (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.textContent)
    || el.closest('label')?.textContent || '';
  return {
    tipo: (el.type || el.tagName).toLowerCase(),
    nome: el.name || '',
    id: el.id || '',
    autocomplete: el.getAttribute('autocomplete') || '',
    placeholder: el.getAttribute('placeholder') || '',
    rotulo: (rotulo || '').replace(/\s+/g, ' ').trim(),
    seletor: sel,
    visivel: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
  };
}
"""


def descrever_campo(pagina, seletor: str) -> dict | None:
    return pagina.evaluate(_DESCREVER, seletor)


def preencher(pagina, seletor: str, valor: str) -> str:
    """Digita num campo, depois de a PÁGINA dizer que campo é aquele."""
    info = descrever_campo(pagina, seletor)
    if info is None:
        raise AcaoRecusada(f"Não achei nenhum elemento em {seletor!r}.")
    if not info.get("visivel"):
        # Campo invisível quase nunca é o que a pessoa quis, e é o formato
        # clássico de armadilha em página hostil.
        raise AcaoRecusada("Esse campo não está visível na página.")

    conferir_campo(info)

    pagina.fill(seletor, valor)
    audit("navegador_preencheu", seletor=seletor, tipo=info.get("tipo"),
          caracteres=len(valor))
    rotulo = info.get("rotulo") or info.get("nome") or seletor
    return f"Preenchi {rotulo}."


def clicar(pagina, seletor: str) -> str:
    elemento = pagina.query_selector(seletor)
    if elemento is None:
        raise AcaoRecusada(f"Não achei nada em {seletor!r} para clicar.")
    pagina.click(seletor)
    audit("navegador_clicou", seletor=seletor)
    return "Cliquei."
