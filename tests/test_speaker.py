"""Fala em streaming, com TTS e saída de áudio falsos."""

import threading
import time

import pytest

from james.voice.speaker import Speaker


class FakeTTS:
    """Sintetiza "áudio" instantâneo e registra o que foi pedido."""

    sample_rate = 22050

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.requested: list[str] = []
        self._lock = threading.Lock()

    def synthesize(self, text: str) -> bytes:
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.requested.append(text)
        return b"\x00\x00" * 100


class FakePlayer:
    """Modela o contrato do AudioPlayer real.

    Ponto importante: `play()` limpa a marca de parada ao começar, igual ao
    player de verdade. `stop()` aborta apenas a reprodução em curso — não
    inutiliza o player para sempre.
    """

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.played: list[int] = []
        self._stop = threading.Event()

    def play(self, pcm, sample_rate, channels=1) -> bool:
        self._stop.clear()
        if self.delay:
            time.sleep(self.delay)
        if self._stop.is_set():
            return False
        self.played.append(len(pcm))
        return True

    def stop(self) -> None:
        self._stop.set()


@pytest.fixture
def rig():
    tts = FakeTTS()
    player = FakePlayer()
    return tts, player, Speaker(tts, player)


def test_fala_simples_sintetiza_e_toca(rig):
    tts, player, speaker = rig
    assert speaker.say("Boa noite, senhor.") is True
    assert tts.requested == ["Boa noite, senhor."]
    assert len(player.played) == 1


def test_streaming_emite_sentenca_completa_antes_do_texto_acabar(rig):
    """É esta antecipação que reduz o tempo até a primeira palavra."""
    tts, _, speaker = rig
    speaker.begin()
    speaker.feed("Boa noite, senhor. ")
    speaker.feed("Já verifiquei")

    # A primeira sentença já pode ser sintetizada; a segunda ainda não terminou.
    assert wait_until(lambda: tts.requested == ["Boa noite, senhor."])

    speaker.feed(" o sistema inteiro para o senhor.")
    speaker.finish()
    assert len(tts.requested) == 2


def test_fragmento_incompleto_espera_o_resto(rig):
    tts, _, speaker = rig
    speaker.begin()
    speaker.feed("Uma frase sem terminar ainda")
    time.sleep(0.1)
    assert tts.requested == []

    speaker.finish()
    assert tts.requested == ["Uma frase sem terminar ainda"]


def test_ordem_das_sentencas_e_preservada(rig):
    tts, _, speaker = rig
    speaker.begin()
    speaker.feed("Primeira frase bem completa. Segunda frase igualmente completa. ")
    speaker.feed("Terceira frase para fechar o teste.")
    speaker.finish()
    assert tts.requested[0].startswith("Primeira")
    assert tts.requested[-1].startswith("Terceira")


def test_stop_interrompe_no_meio():
    tts = FakeTTS()
    player = FakePlayer(delay=0.3)
    speaker = Speaker(tts, player)

    speaker.begin()
    speaker.feed("Primeira frase bem longa aqui. Segunda frase bem longa aqui. ")
    speaker.feed("Terceira frase bem longa aqui.")
    time.sleep(0.1)
    speaker.stop()

    tocadas = len(player.played)
    time.sleep(0.4)
    # Nada mais toca depois do stop.
    assert len(player.played) == tocadas
    assert tocadas < 3


def test_finish_apos_interrupcao_devolve_falso():
    tts = FakeTTS()
    player = FakePlayer(delay=0.2)
    speaker = Speaker(tts, player)
    speaker.begin()
    speaker.feed("Uma frase razoavelmente longa para dar tempo. ")
    time.sleep(0.05)
    speaker.stop()
    assert speaker.finish(timeout=2.0) is False


def test_texto_falado_registra_apenas_o_que_saiu(rig):
    """O histórico deve guardar o que o usuário ouviu, não o que foi gerado."""
    tts, player, speaker = rig
    speaker.begin()
    speaker.feed("Primeira frase completa aqui. Segunda frase completa aqui.")
    speaker.finish()
    assert "Primeira" in speaker.spoken_text
    assert "Segunda" in speaker.spoken_text


def test_texto_vazio_nao_chama_o_tts(rig):
    tts, _, speaker = rig
    assert speaker.say("") is True
    assert speaker.say("   ") is True
    assert tts.requested == []


def test_falha_do_tts_nao_derruba_a_fala():
    class QuebraTTS(FakeTTS):
        def synthesize(self, text: str) -> bytes:
            from james.voice.tts import TTSUnavailable

            if "quebra" in text:
                raise TTSUnavailable("falha proposital")
            return super().synthesize(text)

    tts = QuebraTTS()
    player = FakePlayer()
    speaker = Speaker(tts, player)

    speaker.begin()
    speaker.feed("Esta frase quebra o sintetizador agora. Esta frase funciona normalmente.")
    assert speaker.finish() is True
    # A segunda frase é tocada mesmo com a primeira falhando.
    assert len(player.played) == 1


def test_begin_cancela_a_fala_anterior():
    tts = FakeTTS()
    player = FakePlayer(delay=0.2)
    speaker = Speaker(tts, player)

    speaker.begin()
    speaker.feed("Frase antiga bem longa para segurar. Outra frase antiga bem longa.")
    time.sleep(0.05)

    speaker.begin()   # novo turno: o anterior morre
    speaker.feed("Frase nova completamente diferente.")
    speaker.finish()
    assert "nova" in speaker.spoken_text
    assert "antiga" not in speaker.spoken_text


def test_callback_de_legenda_recebe_cada_sentenca():
    tts = FakeTTS()
    player = FakePlayer()
    legendas: list[str] = []
    speaker = Speaker(tts, player, on_sentence=legendas.append)

    speaker.say("Primeira frase completa aqui. Segunda frase completa aqui.")
    assert len(legendas) == 2


def test_callback_que_falha_nao_quebra_a_fala():
    def quebra(text):
        raise RuntimeError("erro proposital")

    tts = FakeTTS()
    player = FakePlayer()
    speaker = Speaker(tts, player, on_sentence=quebra)
    assert speaker.say("Uma frase completa qualquer.") is True
    assert len(player.played) == 1


def wait_until(predicate, timeout=3.0, interval=0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
