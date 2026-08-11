"""Detecção de fim de fala, sem hardware — o detector é injetado."""

import pytest

from james.audio.vad import RecorderState, SpeechRecorder, VadSettings
from james.config import AudioFormat


class ScriptedDetector:
    """Detector falso: segue um roteiro de fala/silêncio por frame."""

    def __init__(self, script: list[bool]) -> None:
        self.script = list(script)
        self.index = 0

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        if self.index < len(self.script):
            value = self.script[self.index]
        else:
            value = False
        self.index += 1
        return value


FMT = AudioFormat(sample_rate=16000, channels=1, sample_width=2, frame_ms=30)


def make_recorder(script, **overrides):
    raw = {
        "aggressiveness": 2,
        "start_frames": 3,
        "end_silence_ms": 300,   # 10 frames
        "preroll_ms": 90,        # 3 frames
        "min_command_ms": 120,   # 4 frames
        "max_command_ms": 3000,  # 100 frames
        "max_wait_speech_ms": 600,  # 20 frames
    }
    raw.update(overrides)
    settings = VadSettings(FMT, raw)
    return SpeechRecorder(FMT, settings, ScriptedDetector(script))


def frame() -> bytes:
    return b"\x00" * FMT.frame_bytes


def feed(recorder, count):
    estado = recorder.state
    for _ in range(count):
        estado = recorder.process(frame())
        if estado in (RecorderState.DONE, RecorderState.TIMEOUT):
            break
    return estado


def test_fala_seguida_de_silencio_encerra_o_comando():
    script = [False] * 2 + [True] * 20 + [False] * 15
    recorder = make_recorder(script)
    assert feed(recorder, len(script)) is RecorderState.DONE
    assert recorder.audio() is not None


def test_estalo_isolado_nao_comeca_gravacao():
    """Um único frame de fala é ruído, não início de comando."""
    script = [False, True, False] * 10
    recorder = make_recorder(script)
    assert feed(recorder, 30) is RecorderState.TIMEOUT


def test_silencio_total_da_timeout():
    recorder = make_recorder([False] * 40)
    assert feed(recorder, 40) is RecorderState.TIMEOUT
    assert recorder.audio() is None


def test_preroll_preserva_o_inicio_da_fala():
    """Sem preroll, a primeira sílaba é cortada."""
    script = [False] * 5 + [True] * 20 + [False] * 15
    recorder = make_recorder(script)
    feed(recorder, len(script))
    audio = recorder.audio()
    assert audio is not None
    # O comando precisa ser maior que só os frames de fala detectados depois
    # do gatilho: o preroll entra junto.
    assert len(audio) > 20 * FMT.frame_bytes * 0.8


def test_teto_de_duracao_encerra_mesmo_falando():
    """O James nunca grava indefinidamente."""
    recorder = make_recorder([True] * 500, max_command_ms=900)  # 30 frames
    assert feed(recorder, 500) is RecorderState.DONE
    assert recorder.frame_count <= 31


def test_comando_curto_demais_e_descartado():
    script = [True] * 4 + [False] * 15
    recorder = make_recorder(script, min_command_ms=600)  # 20 frames
    feed(recorder, len(script))
    assert recorder.audio() is None


def test_pausa_curta_no_meio_nao_encerra():
    """Respirar entre palavras não pode cortar o comando."""
    script = [True] * 10 + [False] * 5 + [True] * 10 + [False] * 15
    recorder = make_recorder(script)
    assert feed(recorder, len(script)) is RecorderState.DONE
    assert recorder.frame_count > 25


def test_frame_de_tamanho_errado_e_erro_explicito():
    """webrtcvad rejeita frame parcial com erro obscuro; falhamos antes."""
    recorder = make_recorder([True] * 10)
    with pytest.raises(ValueError, match="Frame com"):
        recorder.process(b"\x00" * 10)


def test_processar_depois_de_concluido_e_estavel():
    script = [True] * 10 + [False] * 15
    recorder = make_recorder(script)
    feed(recorder, len(script))
    assert recorder.process(frame()) is RecorderState.DONE


def test_reset_permite_reutilizar_o_gravador():
    script = [True] * 10 + [False] * 15
    recorder = make_recorder(script)
    feed(recorder, len(script))
    recorder.reset()
    assert recorder.state is RecorderState.WAITING
    assert recorder.frame_count == 0
    assert recorder.audio() is None


def test_configuracao_incoerente_e_rejeitada():
    with pytest.raises(ValueError):
        VadSettings(FMT, {"min_command_ms": 5000, "max_command_ms": 1000})


def test_agressividade_fora_da_faixa_e_limitada():
    settings = VadSettings(FMT, {"aggressiveness": 99})
    assert settings.aggressiveness == 3
