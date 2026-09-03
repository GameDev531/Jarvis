"""Ferramentas do navegador — o que o modelo pode pedir ao Chrome.

Divisão de risco, e ela não é arbitrária:

  LER  (Nível 1)   abrir aba, listar abas, inspecionar
  AGIR (Nível 2)   preencher campo, clicar, fechar aba

Abrir uma aba não muda nada no mundo; clicar pode comprar uma passagem. O
guard pede confirmação para o segundo grupo e não para o primeiro.

Acima dos dois níveis existe uma terceira categoria, que não é "arriscada" e
sim **impossível**: campo de senha, upload de arquivo, campo oculto. Ver
`james/browser/actions.py`. Nenhuma confirmação destrava, porque quem decide
não é o guard nem o modelo — é o código, olhando o que a página diz que o
campo é.

## O contrato de alvo, que é a mudança desta fase

Toda ação que MUDA alguma coisa exige `tab_id` e, quando mexe num elemento,
também `snapshot_id` + `element_id`. Não existe "a última aba", não existe
seletor CSS inventado pelo modelo. Os três vêm de `inspecionar_pagina`.

Isso é mais verboso para o modelo, de propósito. O custo de escrever um número
a mais é uma linha de JSON; o custo de clicar na aba errada é uma compra
confirmada na aba do banco que você deixou aberta.
"""

from __future__ import annotations

from james.browser.actions import AcaoRecusada, clicar as _clicar, preencher as _preencher
from james.browser.driver import BrowserUnavailable
from james.browser.inspector import inspecionar as _inspecionar
from james.browser.network_policy import RedeBloqueada
from james.browser.sessao import AbaDesconhecida, AlvoAusente
from james.browser.snapshot import (
    ElementoNaoEncontrado,
    SnapshotInvalido,
    agora,
    capturar,
    revalidar,
)
from james.logs import get_logger
from james.modes.base import ModeError
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.navegador")

# Erros que são RECUSA, não falha: o James fez a coisa certa ao não fazer. A
# frase explica, e o modelo aprende que insistir não muda nada.
_RECUSAS = (AcaoRecusada, AlvoAusente, AbaDesconhecida, SnapshotInvalido,
            ElementoNaoEncontrado, RedeBloqueada)


def register(registry: ToolRegistry, config, guard, modes) -> None:
    """Registra as ferramentas do navegador. Sem `modes`, nenhuma existe."""
    if modes is None:
        return
    modo = modes.get("navegador") if hasattr(modes, "get") else None
    if modo is None:
        return

    def _driver():
        return modo.exigir_driver()

    def _recusa(exc) -> ToolResult:
        codigo = getattr(exc, "codigo", None)
        dados = {"recusado": True}
        if codigo:
            dados["codigo"] = codigo
        return ToolResult(ok=False, speech=str(exc), data=dados)

    # ------------------------------------------------------------- ler

    def abrir_aba(args: dict) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url:
            return ToolResult.failure("Preciso de um endereço.")
        try:
            aba = _driver().abrir(url)
        except _RECUSAS as exc:
            return _recusa(exc)
        except (ModeError, BrowserUnavailable) as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001 — site fora do ar não derruba o James
            return ToolResult.failure(f"Não consegui abrir: {exc}")
        resumo = aba.resumo()
        return ToolResult(
            ok=True,
            ack=f"Abri na aba {aba.tab_id}.",
            data={"tab_id": aba.tab_id, "dominio": resumo["dominio"],
                  "titulo": resumo["titulo"]},
            external_content=True,
        )

    def listar_abas(args: dict) -> ToolResult:
        try:
            abas = _driver().abas()
        except (ModeError, BrowserUnavailable) as exc:
            return ToolResult.failure(str(exc))
        if not abas:
            return ToolResult(ok=True, speech="Nenhuma aba aberta.", data={"abas": []})
        return ToolResult(
            ok=True,
            speech=f"{len(abas)} aba{'s' if len(abas) != 1 else ''} abertas.",
            data={"abas": abas},
            # Título de aba é texto de terceiro: passa pelo sanitizador antes
            # de entrar no histórico do modelo.
            external_content=True,
        )

    def inspecionar_pagina(args: dict) -> ToolResult:
        driver = None
        try:
            driver = _driver()
            aba = driver.aba_para_ler(args.get("tab_id"))
            relatorio = _inspecionar(aba.page)
            snap = capturar(aba.page, aba.tab_id, agora())
            driver.snapshots.guardar(snap)
        except _RECUSAS as exc:
            return _recusa(exc)
        except (ModeError, BrowserUnavailable) as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"Não consegui inspecionar: {exc}")

        relatorio.update(snap.resumo())
        # A URL inteira sai do relatório: ela vai para o histórico do modelo e
        # carrega token de sessão e id de pedido. O domínio já situa.
        relatorio.pop("url", None)
        return ToolResult(
            ok=True,
            speech=relatorio.get("resumo", ""),
            data=relatorio,
            external_content=True,
        )

    # ------------------------------------------------------------- agir

    def _resolver_alvo(driver, args):
        """Aba + snapshot + elemento, revalidados. Levanta se algo não bate."""
        aba = driver.aba_para_agir(args.get("tab_id"))
        snap = driver.snapshots.exigir(args.get("snapshot_id"), aba.tab_id)
        ref = snap.elemento(args.get("element_id"))
        estado = revalidar(aba.page, snap, ref, agora())
        return aba, ref, estado

    def preencher_campo(args: dict) -> ToolResult:
        valor = str(args.get("valor", ""))
        try:
            driver = _driver()
            aba, ref, estado = _resolver_alvo(driver, args)
            frase = _preencher(aba.page, ref.seletor, valor, estado=estado)
            aba.tocar()
        except _RECUSAS as exc:
            return _recusa(exc)
        except (ModeError, BrowserUnavailable) as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"Não consegui preencher: {exc}")
        return ToolResult(ok=True, ack=frase,
                          data={"tab_id": aba.tab_id, "element_id": ref.element_id})

    def clicar_em(args: dict) -> ToolResult:
        try:
            driver = _driver()
            aba, ref, _ = _resolver_alvo(driver, args)
            frase = _clicar(aba.page, ref.seletor)
            aba.tocar()
            # Clicar muda a página com frequência: a leitura que autorizou este
            # clique não vale para o próximo. Descartar aqui é o que impede um
            # segundo clique de acontecer sobre um mapa que já mudou.
            driver.snapshots.esquecer_aba(aba.tab_id)
        except _RECUSAS as exc:
            return _recusa(exc)
        except (ModeError, BrowserUnavailable) as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"Não consegui clicar: {exc}")
        return ToolResult(
            ok=True,
            ack=f"{frase} Se precisar agir de novo, inspecione a página — ela pode ter mudado.",
            data={"tab_id": aba.tab_id, "element_id": ref.element_id},
        )

    def fechar_aba(args: dict) -> ToolResult:
        try:
            frase = _driver().fechar_aba(args.get("tab_id"))
        except _RECUSAS as exc:
            return _recusa(exc)
        except (ModeError, BrowserUnavailable) as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"Não consegui fechar: {exc}")
        return ToolResult(ok=True, ack=frase)

    # ---------------------------------------------------------- registro

    _TAB = {
        "type": "string",
        "description": "Número da aba, vindo de listar_abas ou abrir_aba.",
        "audit_mode": "plaintext",
    }
    _SNAP = {
        "type": "string",
        "description": "snapshot_id devolvido por inspecionar_pagina.",
        "audit_mode": "plaintext",
    }
    _ELEM = {
        "type": "string",
        "description": "element_id (e1, e2...) devolvido por inspecionar_pagina.",
        "audit_mode": "plaintext",
    }

    registry.register(Tool(
        name="abrir_aba",
        description=(
            "Abre um endereço numa aba nova e devolve o número dela. Exige o "
            "modo navegador ligado."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Endereço completo.",
                    "audit_mode": "plaintext",
                }
            },
            "required": ["url"],
        },
        handler=abrir_aba,
    ))

    registry.register(Tool(
        name="listar_abas",
        description=(
            "Lista as abas abertas, cada uma com um número. Use antes de "
            "qualquer ação para saber em qual aba agir."
        ),
        parameters={"type": "object", "properties": {}},
        handler=listar_abas,
        fire_and_forget=False,
    ))

    registry.register(Tool(
        name="inspecionar_pagina",
        description=(
            "Lê a página de uma aba: problemas de qualidade e acessibilidade, "
            "e a lista de elementos com que dá para interagir. Devolve um "
            "snapshot_id e um element_id para cada elemento — são eles que "
            "clicar_em e preencher_campo exigem. Sempre inspecione antes de agir."
        ),
        parameters={
            "type": "object",
            "properties": {"tab_id": _TAB},
        },
        handler=inspecionar_pagina,
        fire_and_forget=False,
    ))

    registry.register(Tool(
        name="preencher_campo",
        description=(
            "Digita um valor num campo. Exige tab_id, snapshot_id e element_id "
            "de inspecionar_pagina — não invente seletores. NUNCA funciona em "
            "campo de senha, upload de arquivo ou dado sensível: são recusados "
            "pelo sistema, não adianta tentar."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tab_id": _TAB,
                "snapshot_id": _SNAP,
                "element_id": _ELEM,
                "valor": {
                    "type": "string",
                    "description": "O texto a digitar.",
                    # NUNCA plaintext, nem em modo de depuração: aqui passa o
                    # que a pessoa digita num formulário — e-mail, endereço,
                    # nome completo. A anotação vence o modo global de
                    # propósito; trava não tem chave de depuração.
                    "audit_mode": "metadata",
                },
            },
            "required": ["tab_id", "snapshot_id", "element_id", "valor"],
        },
        handler=preencher_campo,
    ))

    registry.register(Tool(
        name="clicar_em",
        description=(
            "Clica num elemento. Exige tab_id, snapshot_id e element_id de "
            "inspecionar_pagina. Depois de clicar, a página pode ter mudado — "
            "inspecione de novo antes da próxima ação."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tab_id": _TAB,
                "snapshot_id": _SNAP,
                "element_id": _ELEM,
            },
            "required": ["tab_id", "snapshot_id", "element_id"],
        },
        handler=clicar_em,
    ))

    registry.register(Tool(
        name="fechar_aba",
        description="Fecha uma aba pelo número.",
        parameters={
            "type": "object",
            "properties": {"tab_id": _TAB},
            "required": ["tab_id"],
        },
        handler=fechar_aba,
    ))
