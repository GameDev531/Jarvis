"""A fronteira do gesto e as ferramentas de modo.

O teste que mais importa aqui é o de Nível 2: um gesto não pode virar pergunta
de confirmação, porque uma mão na frente da câmera não identifica ninguém.
"""

import pytest

from james.config import Config
from james.modes.actions import GestureActions
from james.modes.base import Mode, ModeManager
from james.permissions.guard import Decision, Guard, GuardVerdict
from james.tools.registry import Tool, ToolRegistry, ToolResult
import james.tools.modes as mode_tools


class ModoFalso(Mode):
    nome = "gestos"
    recursos = ("camera",)

    def _ligar(self):
        return "Webcam ligada."

    def _desligar(self):
        return "Webcam desligada."


class GuardFalso:
    def __init__(self, decisoes=None):
        self.decisoes = decisoes or {}
        self.avaliadas = []

    def evaluate(self, tool, args=None):
        self.avaliadas.append((tool, dict(args or {})))
        return GuardVerdict(
            tool=tool,
            decision=self.decisoes.get(tool, Decision.ALLOW),
            reason="teste",
            spoken="",
            args=dict(args or {}),
        )


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.chamadas = []

    def volume(args):
        r.chamadas.append(args)
        return ToolResult(ok=True, ack="ok")

    r.register(Tool(name="ajustar_volume", description="d", handler=volume))
    return r


# ------------------------------------------------------ ações internas


def test_parar_chama_o_cancelamento(registry):
    chamou = []
    acoes = GestureActions(GuardFalso(), registry, on_parar=lambda: chamou.append(1))
    acoes.executar("punho", "parar")
    assert chamou == [1]


def test_pausar_escuta(registry):
    chamou = []
    acoes = GestureActions(GuardFalso(), registry, on_pausar=lambda: chamou.append(1))
    acoes.executar("palma_aberta", "pausar_escuta")
    assert chamou == [1]


def test_desligar_o_proprio_modo(registry):
    chamou = []
    acoes = GestureActions(
        GuardFalso(), registry, on_desligar_modo=lambda: chamou.append(1)
    )
    acoes.executar("punho", "desligar_modo")
    assert chamou == [1]


def test_acao_sem_callback_nao_quebra(registry):
    assert "não sei" in GestureActions(GuardFalso(), registry).executar("punho", "parar")


# ---------------------------------------------------- ações via guard


def test_volume_passa_pelo_guard_e_executa(registry):
    guard = GuardFalso()
    GestureActions(guard, registry).executar("polegar_cima", "volume_mais")
    assert guard.avaliadas == [("ajustar_volume", {"acao": "aumentar"})]
    assert registry.chamadas == [{"acao": "aumentar"}]


def test_gesto_nunca_escala_para_nivel_2(registry):
    """O teste mais importante do arquivo.

    Se o guard pede confirmação, o gesto morre aqui — não vira pergunta. Uma
    mão na câmera pode ser de qualquer pessoa; promovê-la a uma confirmação
    transformaria "qualquer um na sala" em "qualquer um que consiga dizer sim".
    """
    guard = GuardFalso({"ajustar_volume": Decision.CONFIRM})
    resultado = GestureActions(guard, registry).executar("polegar_cima", "volume_mais")
    assert "exige confirmação" in resultado
    assert registry.chamadas == []


def test_gesto_bloqueado_nao_executa(registry):
    guard = GuardFalso({"ajustar_volume": Decision.BLOCK})
    GestureActions(guard, registry).executar("polegar_cima", "volume_mais")
    assert registry.chamadas == []


def test_acao_desconhecida_nao_chama_nada(registry):
    guard = GuardFalso()
    resultado = GestureActions(guard, registry).executar("punho", "mover_arquivo")
    assert "desconhecida" in resultado
    assert guard.avaliadas == []


def test_ferramenta_ausente_do_catalogo(registry):
    vazio = ToolRegistry()
    resultado = GestureActions(GuardFalso(), vazio).executar("v", "mudo")
    assert "indisponível" in resultado


# ----------------------------------------------------- ferramentas de modo


@pytest.fixture
def catalogo():
    manager = ModeManager([ModoFalso()])
    registry = ToolRegistry()
    mode_tools.register(registry, Config({}), Guard(Config({})), manager)
    return registry, manager


def test_ativar_modo_liga(catalogo):
    registry, manager = catalogo
    resultado = registry.get("ativar_modo").handler({"modo": "gestos"})
    assert resultado.ok is True
    assert manager.ativos() == ["gestos"]


def test_ativar_modo_desconhecido_falha(catalogo):
    registry, _ = catalogo
    assert registry.get("ativar_modo").handler({"modo": "holograma"}).ok is False


def test_ativar_sem_nome_falha(catalogo):
    registry, _ = catalogo
    assert registry.get("ativar_modo").handler({}).ok is False


def test_desativar_modo(catalogo):
    registry, manager = catalogo
    registry.get("ativar_modo").handler({"modo": "gestos"})
    resultado = registry.get("desativar_modo").handler({"modo": "gestos"})
    assert resultado.ok is True
    assert manager.ativos() == []


def test_desativar_sem_nome_desliga_tudo(catalogo):
    """Quem manda desligar quer o hardware livre, não uma pergunta de volta."""
    registry, manager = catalogo
    registry.get("ativar_modo").handler({"modo": "gestos"})
    resultado = registry.get("desativar_modo").handler({})
    assert resultado.ok is True
    assert manager.ativos() == []


def test_desativar_com_nada_ligado_nao_e_erro(catalogo):
    registry, _ = catalogo
    assert registry.get("desativar_modo").handler({}).ok is True


def test_listar_modos_diz_o_que_esta_ligado(catalogo):
    registry, _ = catalogo
    registry.get("ativar_modo").handler({"modo": "gestos"})
    resultado = registry.get("listar_modos").handler({})
    assert "gestos" in resultado.speech
    assert resultado.data[0]["estado"] == "ligado"


def test_enum_do_schema_lista_os_modos_reais(catalogo):
    registry, _ = catalogo
    schema = registry.get("ativar_modo").parameters
    assert schema["properties"]["modo"]["enum"] == ["gestos"]


def test_sem_gerente_nenhuma_ferramenta_e_registrada():
    registry = ToolRegistry()
    mode_tools.register(registry, Config({}), Guard(Config({})), None)
    assert registry.names == ()


def test_sem_modos_o_schema_nao_tem_enum_vazio():
    """Um enum vazio recusa qualquer valor e alguns provedores rejeitam a tool."""
    registry = ToolRegistry()
    mode_tools.register(registry, Config({}), Guard(Config({})), ModeManager())
    assert "enum" not in registry.get("ativar_modo").parameters["properties"]["modo"]


# ---------------------------------------------------------- guard de modos


def test_ligar_modo_sensivel_pede_confirmacao():
    guard = Guard(Config({"permissions": {"modos_sensiveis": ["gestos"]}}))
    assert guard.evaluate("ativar_modo", {"modo": "gestos"}).decision is Decision.CONFIRM


def test_ligar_modo_comum_e_direto():
    guard = Guard(Config({"permissions": {"modos_sensiveis": ["gestos"]}}))
    assert guard.evaluate("ativar_modo", {"modo": "leitura"}).decision is Decision.ALLOW


def test_desligar_nunca_e_bloqueado():
    """Um freio que às vezes não funciona não é freio."""
    guard = Guard(Config({"permissions": {"modos_sensiveis": ["gestos"]}}))
    for nome in ("gestos", "", "qualquer coisa", "../../etc"):
        assert guard.evaluate("desativar_modo", {"modo": nome}).decision is Decision.ALLOW


def test_ligar_sem_nome_e_bloqueado():
    guard = Guard(Config({}))
    assert guard.evaluate("ativar_modo", {"modo": ""}).decision is Decision.BLOCK


def test_confirmacao_diz_que_a_camera_fica_aberta():
    """Quem confirma precisa saber que está autorizando algo contínuo."""
    guard = Guard(Config({"permissions": {"modos_sensiveis": ["gestos"]}}))
    falado = guard.evaluate("ativar_modo", {"modo": "gestos"}).spoken
    assert "câmera" in falado and "desligar" in falado
