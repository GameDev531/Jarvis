"""A trilha de auditoria registra O QUE foi feito, não o que foi digitado.

A redação antiga era por NOME de chave — `token`, `password`, `api_key`. Ela
pegava a chave da API e não pegava nada do que o usuário escreve:

    args={"seletor": "#email", "valor": "meu@email.com"}

`valor` não estava em lista nenhuma, e ia inteiro para o disco. O mesmo com
`audit("comando", texto=...)`, que gravava a frase do usuário em toda
interação. Log vira ZIP, print de tela e anexo de e-mail pedindo ajuda.
"""

from __future__ import annotations

import json

import pytest

from james.logs import logger as logger_mod
from james.logs.privacy import (
    AuditMode,
    PrivacyMode,
    audit_text,
    get_privacy_mode,
    limpar_schema,
    politica_do_schema,
    redact_args,
    set_privacy_mode,
)
from james.tools.registry import Tool, ToolRegistry, ToolResult


@pytest.fixture(autouse=True)
def modo_padrao():
    """Cada teste começa no modo padrão e o restaura ao sair."""
    anterior = get_privacy_mode()
    set_privacy_mode(PrivacyMode.STANDARD)
    yield
    set_privacy_mode(anterior)


def tool_preencher() -> Tool:
    """A ferramenta do exemplo da auditoria, com as anotações reais."""
    from james.tools import navegador  # noqa: F401 — garante que o módulo carrega

    return Tool(
        name="preencher_campo",
        description="Digita num campo.",
        parameters={
            "type": "object",
            "properties": {
                "seletor": {"type": "string", "audit_mode": "plaintext"},
                "valor": {"type": "string", "audit_mode": "metadata"},
            },
        },
        handler=lambda args: ToolResult(ok=True),
    )


# ------------------------------------------------ o caso que motivou tudo


def test_o_texto_digitado_num_formulario_nao_vai_para_a_trilha():
    trilha = tool_preencher().audit_args(
        {"seletor": "#email", "valor": "meu@email.com"}
    )
    assert trilha["valor"] == "<redacted:13 chars>"
    assert "meu@email.com" not in json.dumps(trilha)
    # O seletor fica: é ele que responde "em que campo o James mexeu?".
    assert trilha["seletor"] == "#email"


def test_nem_o_modo_de_depuracao_libera_o_texto_digitado():
    """A anotação do schema vence o modo global. Trava não tem chave de debug."""
    set_privacy_mode(PrivacyMode.DEBUG_EXPLICIT)
    trilha = tool_preencher().audit_args({"seletor": "#email", "valor": "meu@email.com"})
    assert trilha["valor"] == "<redacted:13 chars>"


def test_a_ferramenta_de_navegador_registrada_de_verdade_esta_anotada():
    """Não adianta a política existir se o schema real não a usa."""
    from james.llm.base import ToolSchema  # noqa: F401
    from james.tools.registry import ToolRegistry as _R

    registry = _R()
    modos = _politica_registrada(registry, "preencher_campo")
    assert modos["valor"] is not AuditMode.PLAINTEXT
    assert modos["valor"] is not None, "preencher_campo.valor precisa de anotação"


def _politica_registrada(registry: ToolRegistry, nome: str):
    """Monta o catálogo de navegador de verdade e devolve a política da tool."""
    from james.config import Config
    from james.permissions.guard import Guard
    from james.tools import navegador

    class _Modo:
        def exigir_driver(self):  # pragma: no cover — nunca chamado aqui
            raise RuntimeError("sem navegador nos testes")

    class _Modos:
        def get(self, nome):
            return _Modo()

    config = Config({})
    navegador.register(registry, config, Guard(config), _Modos())
    tool = registry.get(nome)
    assert tool is not None, f"{nome} não foi registrada"
    return tool.audit_policy()


# ---------------------------------------------------- o padrão falha fechado


def test_argumento_de_texto_sem_anotacao_nao_vaza_conteudo():
    """Ferramenta nova, escrita amanhã, não vaza por esquecimento."""
    tool = Tool(
        name="nova",
        description="",
        parameters={"type": "object", "properties": {"algo": {"type": "string"}}},
        handler=lambda args: ToolResult(),
    )
    assert tool.audit_args({"algo": "confidencial"}) == {"algo": "<redacted:12 chars>"}


def test_numero_e_booleano_passam_porque_nao_carregam_conteudo():
    tool = Tool(
        name="nova",
        description="",
        parameters={
            "type": "object",
            "properties": {"quantos": {"type": "integer"}, "forcar": {"type": "boolean"}},
        },
        handler=lambda args: ToolResult(),
    )
    assert tool.audit_args({"quantos": 3, "forcar": True}) == {"quantos": 3, "forcar": True}


def test_argumento_que_o_modelo_inventou_e_apagado():
    """Nome fora do schema: não sabemos o que é, então não vai para o disco."""
    tool = Tool(
        name="nova",
        description="",
        parameters={"type": "object", "properties": {"conhecido": {"type": "string"}}},
        handler=lambda args: ToolResult(),
    )
    assert tool.audit_args({"surpresa": "sk-vazamento"})["surpresa"] == "***"


def test_sensitive_true_e_atalho_para_o_mais_restritivo():
    politica = politica_do_schema(
        {"type": "object", "properties": {"x": {"type": "string", "sensitive": True}}}
    )
    assert politica["x"] is AuditMode.REDACT


def test_anotacao_invalida_nao_vira_permissao():
    """`audit_mode: sim` é erro de quem escreveu — e falha fechado."""
    politica = politica_do_schema(
        {"type": "object", "properties": {"x": {"type": "string", "audit_mode": "sim"}}}
    )
    assert politica["x"] is AuditMode.REDACT


# ----------------------------------------------------------- modos globais


def test_modo_minimal_nao_deixa_nem_o_caminho_de_arquivo_passar():
    set_privacy_mode(PrivacyMode.MINIMAL)
    tool = Tool(
        name="mover",
        description="",
        parameters={
            "type": "object",
            "properties": {"origem": {"type": "string", "audit_mode": "plaintext"}},
        },
        handler=lambda args: ToolResult(),
    )
    # A anotação `plaintext` é uma permissão, e `minimal` é o usuário revogando
    # permissões. Uma trava (`redact`/`metadata`) continuaria valendo.
    assert tool.audit_args({"origem": "C:/Users/ana/nf.pdf"}) == {
        "origem": "<redacted:19 chars>"
    }


def test_modo_desconhecido_cai_no_mais_restritivo():
    set_privacy_mode("nao_existe")
    assert get_privacy_mode() is PrivacyMode.MINIMAL


def test_hash_permite_correlacionar_sem_revelar():
    from james.logs.privacy import aplicar_modo

    a = aplicar_modo("segredo", AuditMode.HASH)
    b = aplicar_modo("segredo", AuditMode.HASH)
    c = aplicar_modo("outro", AuditMode.HASH)
    assert a == b != c and "segredo" not in a


def test_plaintext_tem_teto():
    """Uma página inteira não cabe numa trilha, mesmo autorizada."""
    from james.logs.privacy import MAX_PLAINTEXT, aplicar_modo

    saida = aplicar_modo("x" * (MAX_PLAINTEXT + 50), AuditMode.PLAINTEXT)
    assert len(saida) < MAX_PLAINTEXT + 50 and "+50 chars" in saida


# ------------------------------------------------ a frase do usuário


def test_o_comando_do_usuario_nao_e_auditado_por_padrao():
    campos = audit_text("apaga o relatório do cliente Silva")
    assert "Silva" not in json.dumps(campos)
    assert campos["texto_chars"] == 34
    # O digest permite dizer "foi o mesmo comando de antes" sem dizer qual.
    assert campos["texto_hash"] == audit_text("apaga o relatório do cliente Silva")["texto_hash"]


def test_o_comando_completo_exige_opt_in_explicito():
    set_privacy_mode(PrivacyMode.DEBUG_EXPLICIT)
    assert audit_text("apaga tudo") == {"texto": "apaga tudo"}


def test_a_trilha_diz_em_que_modo_foi_escrita(tmp_path, monkeypatch):
    """Sem isso, um `<redacted>` não distingue "protegido" de "vazio"."""
    destino = tmp_path / "audit.jsonl"
    monkeypatch.setattr(logger_mod, "_audit_path", destino)
    logger_mod.audit("teste", campo="valor")
    registro = json.loads(destino.read_text(encoding="utf-8").splitlines()[0])
    assert registro["privacidade"] == "standard"


def test_a_redacao_por_nome_de_chave_continua_valendo(tmp_path, monkeypatch):
    """Segunda camada: pega o segredo que chega por um caminho sem schema."""
    destino = tmp_path / "audit.jsonl"
    monkeypatch.setattr(logger_mod, "_audit_path", destino)
    logger_mod.audit("teste", token="sk-abcdef", contexto={"password": "1234"})
    linha = destino.read_text(encoding="utf-8")
    assert "sk-abcdef" not in linha and "1234" not in linha


# --------------------------------------- as anotações não vazam para o modelo


def test_o_schema_enviado_ao_modelo_nao_carrega_as_anotacoes():
    """`audit_mode` é metadado nosso. Provedor com validação estrita recusa
    campo desconhecido, e de qualquer forma seriam tokens gastos à toa."""
    tool = tool_preencher()
    schema = tool.schema()
    texto = json.dumps(schema.parameters)
    assert "audit_mode" not in texto and "sensitive" not in texto
    # E o schema continua completo.
    assert set(schema.parameters["properties"]) == {"seletor", "valor"}


def test_limpar_schema_desce_em_estruturas_aninhadas():
    limpo = limpar_schema({
        "type": "object",
        "properties": {
            "linhas": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "valor": {"type": "string", "sensitive": True}
                }},
            }
        },
    })
    assert "sensitive" not in json.dumps(limpo)


def test_todo_o_catalogo_produz_schema_limpo():
    """Uma anotação esquecida em qualquer ferramenta viraria payload inválido."""
    from james.config import Config
    from james.permissions.guard import Guard
    from james.tools import build_registry

    config = Config({})
    registry = build_registry(config, Guard(config))
    for schema in registry.schemas():
        texto = json.dumps(schema.parameters)
        assert "audit_mode" not in texto, schema.name
        assert '"sensitive"' not in texto, schema.name


# ------------------------------------------------- integração com o registro


def test_o_registro_audita_pela_politica_da_ferramenta(tmp_path, monkeypatch):
    destino = tmp_path / "audit.jsonl"
    monkeypatch.setattr(logger_mod, "_audit_path", destino)

    registry = ToolRegistry()
    registry.register(tool_preencher())
    registry.execute("preencher_campo", {"seletor": "#email", "valor": "meu@email.com"})

    linha = destino.read_text(encoding="utf-8")
    assert "meu@email.com" not in linha
    assert "#email" in linha


def test_ferramenta_desconhecida_cai_no_padrao_fechado():
    registry = ToolRegistry()
    assert registry.audit_args("nao_existe", {"x": "conteúdo"}) == {
        "x": "<redacted:8 chars>"
    }


def test_redact_args_sem_politica_ainda_protege_texto():
    assert redact_args({"livre": "texto do usuário"}) == {"livre": "<redacted:16 chars>"}
