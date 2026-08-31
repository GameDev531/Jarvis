"""Sistema de modos: ligar sob comando, desligar sempre, um dono por recurso."""

import pytest

from james.modes.base import Mode, ModeError, ModeManager, ModeState


class ModoFalso(Mode):
    nome = "falso"
    recursos = ("camera",)

    def __init__(self, nome="falso", recursos=("camera",), erro_ao_ligar=None,
                 erro_ao_desligar=None):
        super().__init__()
        self.nome = nome
        self.recursos = recursos
        self.erro_ao_ligar = erro_ao_ligar
        self.erro_ao_desligar = erro_ao_desligar
        self.ligadas = 0
        self.desligadas = 0

    def _ligar(self):
        if self.erro_ao_ligar:
            raise self.erro_ao_ligar
        self.ligadas += 1
        return "ligado"

    def _desligar(self):
        if self.erro_ao_desligar:
            raise self.erro_ao_desligar
        self.desligadas += 1
        return "desligado"


# ------------------------------------------------------------ padrão desligado


def test_modo_nasce_desligado():
    """O ponto do módulo inteiro: nada consome recurso sem alguém pedir."""
    assert ModoFalso().estado is ModeState.DESLIGADO


def test_manager_nao_liga_nada_sozinho():
    m = ModeManager([ModoFalso("a"), ModoFalso("b", recursos=("microfone",))])
    assert m.ativos() == []


# --------------------------------------------------------------- ciclo básico


def test_ligar_e_desligar():
    modo = ModoFalso()
    assert modo.ligar() == "ligado"
    assert modo.ligado is True
    assert modo.desligar() == "desligado"
    assert modo.estado is ModeState.DESLIGADO


def test_ligar_duas_vezes_nao_reabre_o_recurso():
    modo = ModoFalso()
    modo.ligar()
    modo.ligar()
    assert modo.ligadas == 1


def test_desligar_o_que_ja_esta_desligado_nao_e_erro():
    """Quem pede para fechar a câmera quer ela fechada, não uma reclamação."""
    modo = ModoFalso()
    assert "não está ligado" in modo.desligar()
    assert modo.desligadas == 0


# ------------------------------------------------------------- falha ao ligar


def test_erro_ao_ligar_deixa_o_modo_marcado_como_falhou():
    modo = ModoFalso(erro_ao_ligar=RuntimeError("câmera em uso"))
    with pytest.raises(ModeError, match="câmera em uso"):
        modo.ligar()
    assert modo.estado is ModeState.FALHOU
    assert modo.ligado is False


def test_modo_que_falhou_ao_ligar_pode_tentar_de_novo():
    modo = ModoFalso(erro_ao_ligar=RuntimeError("ocupada"))
    with pytest.raises(ModeError):
        modo.ligar()
    modo.erro_ao_ligar = None
    modo.ligar()
    assert modo.ligado is True


# ---------------------------------------------------------- desligar é sagrado


def test_erro_ao_desligar_nao_levanta():
    """Se desligar pudesse falhar, existiria estado sem saída."""
    modo = ModoFalso(erro_ao_desligar=RuntimeError("driver travado"))
    modo.ligar()
    mensagem = modo.desligar()
    assert "erro na limpeza" in mensagem


def test_erro_ao_desligar_ainda_marca_como_desligado():
    """Ficar 'ligado' impediria uma segunda tentativa de liberar o recurso."""
    modo = ModoFalso(erro_ao_desligar=RuntimeError("x"))
    modo.ligar()
    modo.desligar()
    assert modo.estado is ModeState.DESLIGADO


# ------------------------------------------------------- um recurso, um dono


def test_dois_modos_nao_disputam_a_camera():
    a, b = ModoFalso("a"), ModoFalso("b")
    m = ModeManager([a, b])
    m.ligar("a")
    with pytest.raises(ModeError, match="camera"):
        m.ligar("b")
    assert b.ligadas == 0


def test_recursos_diferentes_convivem():
    m = ModeManager([ModoFalso("a"), ModoFalso("b", recursos=("microfone",))])
    m.ligar("a")
    m.ligar("b")
    assert sorted(m.ativos()) == ["a", "b"]


def test_recurso_liberado_permite_o_outro_ligar():
    m = ModeManager([ModoFalso("a"), ModoFalso("b")])
    m.ligar("a")
    m.desligar("a")
    m.ligar("b")
    assert m.ativos() == ["b"]


def test_modo_sem_recurso_nunca_conflita():
    m = ModeManager([ModoFalso("a"), ModoFalso("livre", recursos=())])
    m.ligar("a")
    m.ligar("livre")
    assert sorted(m.ativos()) == ["a", "livre"]


# ---------------------------------------------------------------- o gerente


def test_modo_desconhecido():
    m = ModeManager()
    with pytest.raises(ModeError, match="[Nn]ão conheço"):
        m.ligar("holograma")


def test_nome_e_normalizado():
    m = ModeManager([ModoFalso("Gestos")])
    m.ligar("  GESTOS ")
    assert m.get("gestos").ligado is True


def test_registro_duplicado_e_erro():
    m = ModeManager([ModoFalso("a")])
    with pytest.raises(ValueError):
        m.register(ModoFalso("a"))


def test_desligar_todos_solta_tudo():
    """É o que roda no encerramento e no kill switch."""
    a = ModoFalso("a")
    b = ModoFalso("b", recursos=("microfone",))
    m = ModeManager([a, b])
    m.ligar("a")
    m.ligar("b")
    assert sorted(m.desligar_todos()) == ["a", "b"]
    assert m.ativos() == []


def test_desligar_todos_com_um_modo_quebrado_ainda_desliga_o_resto():
    quebrado = ModoFalso("quebrado", erro_ao_desligar=RuntimeError("x"))
    ok = ModoFalso("ok", recursos=("microfone",))
    m = ModeManager([quebrado, ok])
    m.ligar("quebrado")
    m.ligar("ok")
    m.desligar_todos()
    assert m.ativos() == []


def test_listar_traz_estado_e_recursos():
    m = ModeManager([ModoFalso("a")])
    m.ligar("a")
    info = m.listar()[0]
    assert info.estado == "ligado"
    assert info.recursos == ["camera"]
    assert info.to_dict()["nome"] == "a"


# --------------------------------------------- modos que sobem com o James

# Alguém rodou `python wake_listener.py`, viu a janela Qt e perguntou por que
# não era a interface holográfica que tínhamos feito. A causa: o wake listener
# sobe o orquestrador SEM ARGUMENTO NENHUM, e o modo holograma nasce desligado.
# Pelo caminho normal de iniciar o James não havia como pedir outra coisa —
# `--holograma` só existia para quem chamasse o main.py na mão.

from james.config import Config
from james.runtime.orchestrator import modos_para_ligar


def test_sem_nada_configurado_nenhum_modo_sobe():
    """O padrão continua sendo o essencial: só a janela Qt."""
    assert modos_para_ligar(Config({}), [], False) == []


def test_config_liga_o_modo_sem_precisar_de_flag():
    """O ponto da correção: funciona para quem inicia por wake_listener.py."""
    config = Config({"modos": {"iniciar_com": ["holograma"]}})
    assert modos_para_ligar(config, [], False) == ["holograma"]


def test_flag_continua_funcionando():
    assert modos_para_ligar(Config({}), [], True) == ["holograma"]
    assert modos_para_ligar(Config({}), ["gestos"], False) == ["gestos"]


def test_config_e_flag_nao_ligam_duas_vezes():
    """Ligar de novo devolveria "já está ligado" — ruído sem informação."""
    config = Config({"modos": {"iniciar_com": ["holograma"]}})
    assert modos_para_ligar(config, ["holograma"], True) == ["holograma"]


def test_config_vem_antes_da_linha_de_comando():
    """A ordem decide quem fica com o recurso quando dois modos o disputam."""
    config = Config({"modos": {"iniciar_com": ["holograma"]}})
    assert modos_para_ligar(config, ["gestos"], False) == ["holograma", "gestos"]


def test_lista_nula_no_config_nao_explode():
    """`iniciar_com:` sem valor vira None no YAML, não lista vazia."""
    assert modos_para_ligar(Config({"modos": {"iniciar_com": None}}), [], False) == []
