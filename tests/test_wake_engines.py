"""Três caminhos para chamar o James, um contrato só.

Existe porque o console da Picovoice **recusa e-mail pessoal** — tentar criar
conta com Gmail devolve "Please enter a valid company email". Isso é uma
barreira comercial, não técnica, e não deveria decidir se o assistente liga.

Os três motores entregam a mesma coisa: `sample_rate`, `frame_length`,
`process()` que devolve >= 0 ao detectar, e `delete()`. Quem consome não sabe
qual está rodando.

As bibliotecas são falsificadas aqui de propósito. `openwakeword` e
`pvporcupine` são extras opcionais — se o teste só rodasse com elas
instaladas, a parte do código que mais quebra (o `__init__` de cada motor)
ficaria sem cobertura justamente na máquina onde ela não está instalada.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from james.config import Config
from james.runtime.wake_engines import (
    HotkeyEngine,
    OpenWakeWordEngine,
    PorcupineEngine,
    WakeWordUnavailable,
    build_wake_engine,
)

CONTRATO = ("sample_rate", "frame_length", "process", "delete")


# ----------------------------------------------------------- bibliotecas falsas


class _ModeloFalso:
    """O suficiente da API do openWakeWord para exercitar o motor."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.pontuacao = 0.0
        self.resets = 0
        # A lib real expõe os modelos carregados aqui, e a chave de `predict()`
        # é a mesma. Verificado contra o openwakeword 0.6.0.
        self.models = {nome: object() for nome in kwargs.get("wakeword_models", [])}

    def predict(self, samples):
        return {nome: self.pontuacao for nome in self.models}

    def reset(self):
        self.resets += 1


@pytest.fixture
def openwakeword_falso(monkeypatch):
    """Injeta um `openwakeword` de mentira em `sys.modules`."""
    criados = []

    def fabrica(**kwargs):
        modelo = _ModeloFalso(**kwargs)
        criados.append(modelo)
        return modelo

    raiz = types.ModuleType("openwakeword")
    raiz.utils = types.SimpleNamespace(download_models=lambda: None)
    submodulo = types.ModuleType("openwakeword.model")
    submodulo.Model = fabrica
    raiz.model = submodulo

    monkeypatch.setitem(sys.modules, "openwakeword", raiz)
    monkeypatch.setitem(sys.modules, "openwakeword.model", submodulo)
    return criados


class _PorcupineFalso:
    sample_rate = 16000
    frame_length = 512

    def __init__(self):
        self.deletado = False
        self.resultado = -1

    def process(self, samples):
        return self.resultado

    def delete(self):
        self.deletado = True


@pytest.fixture
def pvporcupine_falso(monkeypatch):
    criados = []

    def create(**kwargs):
        motor = _PorcupineFalso()
        motor.kwargs = kwargs
        criados.append(motor)
        return motor

    modulo = types.ModuleType("pvporcupine")
    modulo.create = create
    monkeypatch.setitem(sys.modules, "pvporcupine", modulo)
    return criados


# ------------------------------------------------------------------ o contrato


@pytest.fixture
def tres_motores(openwakeword_falso, pvporcupine_falso):
    """Uma instância viva de cada motor — o contrato vale para o objeto.

    Checar no *tipo* não serviria: `sample_rate` e `frame_length` só existem
    depois do `__init__`, e é exatamente esse o par que o `WakeListener` lê
    para montar o formato do áudio.
    """
    return [
        HotkeyEngine("ctrl+alt+espaco"),
        OpenWakeWordEngine(),
        PorcupineEngine(access_key="chave-de-teste"),
    ]


def test_todo_motor_cumpre_o_mesmo_contrato(tres_motores):
    """O `WakeListener` não sabe qual motor está rodando — e não deve saber."""
    for motor in tres_motores:
        for membro in CONTRATO:
            assert hasattr(motor, membro), f"{type(motor).__name__} sem {membro}"


def test_todo_motor_declara_formato_de_audio_usavel(tres_motores):
    """Estes dois números viram o formato do microfone. Zero ou None quebraria
    a captura com um erro obscuro, longe daqui."""
    for motor in tres_motores:
        assert isinstance(motor.sample_rate, int) and motor.sample_rate > 0
        assert isinstance(motor.frame_length, int) and motor.frame_length > 0


def test_todo_motor_silencioso_devolve_negativo(tres_motores):
    """Silêncio não é detecção — em nenhum dos três."""
    silencio = np.zeros(1280, dtype=np.int16)
    for motor in tres_motores:
        assert motor.process(silencio) < 0, type(motor).__name__


# ------------------------------------------------------------------ openWakeWord


def test_frame_de_80ms_a_16khz():
    """O modelo quer blocos múltiplos de 80 ms; a 16 kHz são 1280 amostras.

    Errar aqui faz a inferência recusar todo quadro — e a palavra de ativação
    simplesmente nunca dispara, sem erro visível.
    """
    assert OpenWakeWordEngine.FRAME_LENGTH == 1280
    assert OpenWakeWordEngine.SAMPLE_RATE == 16000
    assert OpenWakeWordEngine.FRAME_LENGTH / OpenWakeWordEngine.SAMPLE_RATE == 0.08


def test_detecta_acima_do_limiar(openwakeword_falso):
    motor = OpenWakeWordEngine(limiar=0.5)
    openwakeword_falso[0].pontuacao = 0.9
    assert motor.process(np.zeros(1280, dtype=np.int16)) == 0


def test_uma_palavra_dispara_uma_vez(openwakeword_falso):
    """Sem o `reset()`, os quadros seguintes continuam acima do limiar e a
    mesma palavra falada dispara a escuta várias vezes seguidas."""
    motor = OpenWakeWordEngine(limiar=0.5)
    modelo = openwakeword_falso[0]
    modelo.pontuacao = 0.9
    motor.process(np.zeros(1280, dtype=np.int16))
    assert modelo.resets == 1


def test_usa_onnx(openwakeword_falso):
    """No Windows o ONNX é o único caminho suportado — e é o que a Fase 0
    confirma. Deixar no padrão (tflite) quebraria só na máquina do usuário."""
    OpenWakeWordEngine(modelo="hey_jarvis")
    assert openwakeword_falso[0].kwargs["inference_framework"] == "onnx"
    assert openwakeword_falso[0].kwargs["wakeword_models"] == ["hey_jarvis"]


def test_nome_de_modelo_que_nao_bate_falha_na_partida(openwakeword_falso, monkeypatch):
    """O pior tipo de defeito: o que não dá erro.

    A pontuação é lida com `.get(nome, 0.0)`. Nome errado = zero para sempre =
    palavra de ativação que nunca dispara, sem log e sem exceção. Melhor
    quebrar na partida do que gritar "Jarvis" para uma máquina muda.
    """
    def modelo_com_outro_nome(**kwargs):
        modelo = _ModeloFalso(**kwargs)
        modelo.models = {"hey_jarvis_v0.1": object()}
        return modelo

    sys.modules["openwakeword.model"].Model = modelo_com_outro_nome
    with pytest.raises(WakeWordUnavailable, match="hey_jarvis"):
        OpenWakeWordEngine(modelo="hey_jarvis")


def test_erro_de_inferencia_nao_derruba_a_escuta(openwakeword_falso):
    """Um quadro ruim não pode matar o processo que fica vivo o dia todo."""
    motor = OpenWakeWordEngine()

    def explode(samples):
        raise RuntimeError("quadro inválido")

    openwakeword_falso[0].predict = explode
    assert motor.process(np.zeros(1280, dtype=np.int16)) < 0


def test_sem_a_biblioteca_a_mensagem_diz_como_instalar(monkeypatch):
    monkeypatch.setitem(sys.modules, "openwakeword", None)
    monkeypatch.setitem(sys.modules, "openwakeword.model", None)
    with pytest.raises(WakeWordUnavailable, match="wakeword"):
        OpenWakeWordEngine()


def test_falha_de_download_explica_que_precisa_de_rede(monkeypatch, openwakeword_falso):
    def sem_rede():
        raise OSError("conexão recusada")

    sys.modules["openwakeword"].utils.download_models = sem_rede
    with pytest.raises(WakeWordUnavailable, match="internet"):
        OpenWakeWordEngine()


# -------------------------------------------------------------------- atalho


def test_atalho_nao_precisa_de_nada():
    """O ponto do modo atalho: funciona sem instalar nem cadastrar nada."""
    motor = HotkeyEngine("ctrl+alt+espaco")
    assert motor.atalho == "ctrl+alt+espaco"
    assert motor.sample_rate == 16000
    motor.delete()


def test_atalho_nunca_detecta_sozinho():
    """Ele não é um detector: é a ausência de um."""
    assert HotkeyEngine().process(b"\x00" * 1024) < 0


# --------------------------------------------------------------------- fábrica


def test_escolhe_o_atalho_quando_pedido():
    config = Config({"wake_word": {"motor": "atalho", "atalho": "ctrl+shift+j"}})
    motor = build_wake_engine(config, lambda _: None)
    assert isinstance(motor, HotkeyEngine)
    assert motor.atalho == "ctrl+shift+j"


def test_escolhe_openwakeword_quando_pedido(openwakeword_falso):
    config = Config(
        {"wake_word": {"motor": "openwakeword", "openwakeword": {"limiar": 0.7}}}
    )
    motor = build_wake_engine(config, lambda _: None)
    assert isinstance(motor, OpenWakeWordEngine)
    assert motor.limiar == 0.7


def test_motor_desconhecido_diz_quais_existem():
    config = Config({"wake_word": {"motor": "inventado"}})
    with pytest.raises(WakeWordUnavailable) as erro:
        build_wake_engine(config, lambda _: None)
    for nome in ("openwakeword", "porcupine", "atalho"):
        assert nome in str(erro.value)


def test_porcupine_sem_chave_sugere_a_alternativa(pvporcupine_falso):
    """A mensagem tem que apontar a saída, não só constatar a falta.

    Quem chega aqui provavelmente acabou de levar "Please enter a valid company
    email" no console da Picovoice, e precisa saber que há outro caminho.
    """
    config = Config({"wake_word": {"motor": "porcupine"}})
    with pytest.raises(WakeWordUnavailable) as erro:
        build_wake_engine(config, lambda _: None)
    mensagem = str(erro.value)
    assert "openwakeword" in mensagem or "atalho" in mensagem


def test_sem_motor_definido_tenta_o_que_nao_pede_conta_primeiro(monkeypatch):
    """A ordem importa: quem não exige cadastro vem antes."""
    tentados = []

    import james.runtime.wake_engines as mod

    def falso_oww(config):
        tentados.append("openwakeword")
        raise WakeWordUnavailable("não instalado")

    def falso_porcupine(config, get_secret):
        tentados.append("porcupine")
        raise WakeWordUnavailable("sem chave")

    monkeypatch.setattr(mod, "_openwakeword", falso_oww)
    monkeypatch.setattr(mod, "_porcupine", falso_porcupine)

    with pytest.raises(WakeWordUnavailable):
        build_wake_engine(Config({}), lambda _: None)
    assert tentados == ["openwakeword", "porcupine"]


def test_quando_tudo_falha_a_mensagem_oferece_o_atalho(monkeypatch):
    """Sem esta linha, a pessoa fica sem saber que existe saída sem instalar."""
    import james.runtime.wake_engines as mod

    monkeypatch.setattr(
        mod, "_openwakeword",
        lambda c: (_ for _ in ()).throw(WakeWordUnavailable("sem openwakeword")),
    )
    monkeypatch.setattr(
        mod, "_porcupine",
        lambda c, g: (_ for _ in ()).throw(WakeWordUnavailable("sem chave")),
    )
    with pytest.raises(WakeWordUnavailable, match="atalho"):
        build_wake_engine(Config({}), lambda _: None)
