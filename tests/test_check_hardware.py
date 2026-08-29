"""Relatório da Fase 0: dizer o que está errado é metade do trabalho.

O relatório é a primeira coisa que alguém roda, e por muito tempo ele tratava
"a biblioteca não está instalada" e "a biblioteca quebrou nesta máquina" como o
mesmo `[FALHOU]`. Na prática são problemas opostos: o primeiro se resolve com
uma linha de `pip install`, o segundo pode significar que o projeto inteiro
precisa mudar de rumo.

Alguém abriu o relatório, viu quatro falhas críticas e concluiu "bastante
problema" — quando três delas eram a mesma linha de instalação faltando.
"""

import james.diagnostics.check_hardware as ch
from james.diagnostics.check_hardware import CheckResult, _print_report, _verdict


def cpu_ok(**metricas):
    base = {"avx": False, "ram_gb": 16, "nucleos": 4}
    base.update(metricas)
    return CheckResult("cpu", True, detail="cpu", metrics=base)


# ----------------------------------------------------- ausente vs quebrado


def test_dependencia_ausente_vem_primeiro_no_veredito():
    """Antes de discutir AVX, diga que ninguém terminou de instalar."""
    veredito = _verdict([
        cpu_ok(),
        CheckResult("webrtcvad", False, error="não instalado", missing_dep=True),
        CheckResult("qt", False, error="PySide6 não instalado", missing_dep=True),
    ])
    primeira = veredito["observacoes"][0]
    assert "PRIMEIRO ISTO" in primeira
    assert "pip install" in primeira
    assert "webrtcvad" in primeira and "qt" in primeira


def test_veredito_diz_que_a_maquina_nao_esta_quebrada():
    """A frase que evita a conclusão errada sobre a própria máquina."""
    veredito = _verdict([
        cpu_ok(),
        CheckResult("porcupine", False, error="não instalado", missing_dep=True),
    ])
    assert "Nada está quebrado" in veredito["observacoes"][0]


def test_falha_real_nao_vira_recado_de_instalacao():
    """Piper sem voz é problema de verdade: não some atrás de um pip install."""
    veredito = _verdict([
        cpu_ok(),
        CheckResult("piper", False, error="voz não encontrada"),
    ])
    assert not any("PRIMEIRO ISTO" in nota for nota in veredito["observacoes"])
    assert veredito["precisa_instalar"] is False
    assert any("Piper não passou" in nota for nota in veredito["observacoes"])


def test_opcional_ausente_nao_alarma():
    """MediaPipe faltando não é urgência: o modo de gestos nasce desligado."""
    veredito = _verdict([
        cpu_ok(),
        CheckResult("gestos", False, error="sem mediapipe", critical=False, missing_dep=True),
    ])
    nota = veredito["observacoes"][0]
    assert "PRIMEIRO ISTO" not in nota
    assert "Opcionais não instalados" in nota
    assert veredito["precisa_instalar"] is False


def test_tudo_instalado_nao_gera_recado():
    veredito = _verdict([cpu_ok(), CheckResult("webrtcvad", True, detail="ok")])
    assert not any("instal" in nota.lower() for nota in veredito["observacoes"])
    assert veredito["dependencias_ausentes"] == []


# --------------------------------------------------------------- impressão


def test_relatorio_marca_ausente_diferente_de_falhou(capsys):
    _print_report([
        CheckResult("webrtcvad", False, error="não instalado", missing_dep=True),
        CheckResult("piper", False, error="voz não encontrada"),
    ])
    saida = capsys.readouterr().out
    assert "[FALTA ] webrtcvad" in saida
    assert "[FALHOU] piper" in saida


def test_colunas_do_relatorio_ficam_alinhadas(capsys):
    """Marcador de tamanho diferente desalinha a tabela inteira."""
    _print_report([
        CheckResult("a", True, detail="ok"),
        CheckResult("b", False, error="x", missing_dep=True),
        CheckResult("c", False, error="y"),
        CheckResult("d", False, error="z", critical=False),
    ])
    marcadores = [
        linha[: linha.index("]") + 1]
        for linha in capsys.readouterr().out.splitlines()
        if linha.startswith("[")
    ]
    assert len(set(len(m) for m in marcadores)) == 1, marcadores


# ------------------------------------------------------------- os achados


def test_avx_presente_levanta_a_bandeira():
    """A premissa 'sem AVX' moveu decisões de arquitetura; se for falsa, avise."""
    veredito = _verdict([cpu_ok(avx=True)])
    assert veredito["tem_avx"] is True
    assert any("TEM AVX" in nota for nota in veredito["observacoes"])


def test_todo_check_registrado_devolve_CheckResult():
    """Um check que devolve outra coisa quebraria o relatório inteiro."""
    assert set(ch.CHECKS) >= {"cpu", "onnxruntime", "webrtcvad", "porcupine", "piper"}
    for nome, funcao in ch.CHECKS.items():
        assert callable(funcao), nome
