"""Processo 1 — a lógica pura, sem microfone nem Porcupine.

O `wake_listener.py` estava com 0% de cobertura, e é o processo que a pessoa
efetivamente inicia. O que dá para testar sem hardware é justamente o que
quebraria silencioso: o reagrupamento de frames e a conta do tamanho exigido
pelo Porcupine.
"""

from __future__ import annotations

import pytest

from james.config import AudioFormat
from james.runtime.wake_listener import _FrameRechunker


# --------------------------------------------------- reagrupamento de frames


def test_junta_pedacos_menores_que_o_frame():
    """O driver entrega o bloco do tamanho que quiser; o Porcupine não aceita."""
    r = _FrameRechunker(10)
    assert list(r.feed(b"abc")) == []
    assert list(r.feed(b"defg")) == []
    assert list(r.feed(b"hij")) == [b"abcdefghij"]


def test_divisao_exata():
    r = _FrameRechunker(4)
    assert list(r.feed(b"123456789012")) == [b"1234", b"5678", b"9012"]


def test_sobra_fica_para_o_proximo_bloco():
    """Descartar a sobra perderia áudio e picotaria a palavra de ativação."""
    r = _FrameRechunker(4)
    assert list(r.feed(b"12345")) == [b"1234"]
    assert list(r.feed(b"678")) == [b"5678"]


def test_reset_descarta_a_sobra():
    """Ao fechar o microfone, a sobra é de antes: colá-la ao áudio novo criaria
    um frame com um pedaço de cada momento."""
    r = _FrameRechunker(4)
    list(r.feed(b"12"))
    r.reset()
    assert list(r.feed(b"3456")) == [b"3456"]


def test_bloco_vazio_nao_produz_nada():
    r = _FrameRechunker(4)
    assert list(r.feed(b"")) == []


def test_frame_bytes_precisa_ser_positivo():
    with pytest.raises(ValueError):
        _FrameRechunker(0)


def test_nenhuma_amostra_e_perdida():
    """Somando tudo que sai mais a sobra, tem que dar o que entrou."""
    r = _FrameRechunker(7)
    entrada = bytes(range(256)) * 3
    saida = b""
    for i in range(0, len(entrada), 13):        # blocos irregulares, como o driver
        saida += b"".join(r.feed(entrada[i : i + 13]))
    assert entrada.startswith(saida)
    assert len(entrada) - len(saida) < 7        # só a sobra parcial fica


# ------------------------------------------- o tamanho que o Porcupine exige


def test_frame_do_porcupine_bate_com_a_derivacao_em_ms():
    """A 16 kHz a conta fecha — 512 amostras dão 32 ms redondos.

    É o caso real de hoje, e é por isso que o bug latente abaixo nunca apareceu.
    """
    fmt = AudioFormat(sample_rate=16000, channels=1, sample_width=2, frame_ms=32)
    assert fmt.frame_bytes == 512 * 2


@pytest.mark.parametrize(
    "frame_length, sample_rate, fecha",
    [
        (512, 16000, True),    # o caso real do Porcupine
        (320, 16000, True),
        (256, 16000, True),
        (512, 22050, False),   # 23,2 ms — arredondar perde 5 amostras
    ],
)
def test_derivar_o_frame_por_milissegundos_e_lossy(frame_length, sample_rate, fecha):
    """Por que o listener guarda `frame_bytes` em vez de recalcular.

    O Porcupine exige um número EXATO de amostras. Derivá-lo de volta a partir
    de `frame_ms` passa por milissegundos inteiros, e nem toda combinação cai
    num número redondo. Quando não cai, todo frame é rejeitado — e nada no
    caminho perceberia, porque a conta *parece* certa.
    """
    frame_ms = int(round(frame_length * 1000 / sample_rate))
    fmt = AudioFormat(
        sample_rate=sample_rate, channels=1, sample_width=2, frame_ms=frame_ms
    )
    derivado = fmt.frame_bytes // 2
    exato = frame_length
    assert (derivado == exato) is fecha
