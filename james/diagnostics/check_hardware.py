"""Fase 0 — prova de compatibilidade. Rode isto ANTES de qualquer outra coisa.

Por que esta etapa existe: o plano inteiro foi reconstruído em cima de uma
hipótese ("este processador não tem AVX") que nunca foi verificada — a evidência
era um travamento do Python ao carregar o LiveKit, que é compatível com falta de
AVX mas também com meia dúzia de outras causas. Trocar faster-whisper por
whisper.cpp e Silero por webrtcvad custa capacidade real, então a hipótese
precisa ser confirmada antes, não depois.

Por isso o primeiro teste imprime as flags REAIS do processador. Se houver AVX,
várias dessas trocas foram desnecessárias.

Cada teste roda num subprocesso isolado, de propósito: uma biblioteca compilada
com instruções que o processador não tem morre com "instrução ilegal", que é um
sinal do sistema operacional — não uma exceção Python. Sem o isolamento, o
primeiro teste que falhasse levaria o relatório inteiro junto.

    python check_hardware.py
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = PROJECT_ROOT / "hardware_report.json"

# Códigos de saída que significam "instrução ilegal" — a assinatura exata de
# uma biblioteca compilada para um processador mais novo que o desta máquina.
_SIGILL_POSIX = -4
_SIGILL_WINDOWS = 0xC000001D          # STATUS_ILLEGAL_INSTRUCTION
_SIGILL_WINDOWS_UNSIGNED = 3221225501

_SUBPROCESS_TIMEOUT_S = 180


class CheckResult(dict):
    """Resultado de um teste, serializável direto para o relatório."""

    def __init__(
        self,
        name: str,
        ok: bool,
        detail: str = "",
        metrics: dict[str, Any] | None = None,
        error: str = "",
        critical: bool = True,
    ) -> None:
        super().__init__(
            name=name,
            ok=ok,
            detail=detail,
            metrics=metrics or {},
            error=error,
            critical=critical,
        )


# --------------------------------------------------------------------- CPU

def _cpu_flags() -> tuple[dict[str, bool], str]:
    """Descobre suporte a AVX sem depender de pacote externo."""
    flags = {"avx": False, "avx2": False, "avx512": False}
    source = "desconhecido"

    if sys.platform == "win32":
        import ctypes

        # Constantes de winnt.h para IsProcessorFeaturePresent.
        features = {"avx": 39, "avx2": 40, "avx512": 41}
        try:
            kernel32 = ctypes.windll.kernel32
            for name, code in features.items():
                flags[name] = bool(kernel32.IsProcessorFeaturePresent(code))
            source = "IsProcessorFeaturePresent"
        except (AttributeError, OSError) as exc:
            source = f"indisponível ({exc})"
    else:
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
                content = handle.read().lower()
            for line in content.splitlines():
                if line.startswith("flags") or line.startswith("features"):
                    tokens = set(line.split(":")[-1].split())
                    flags["avx"] = "avx" in tokens
                    flags["avx2"] = "avx2" in tokens
                    flags["avx512"] = any(t.startswith("avx512") for t in tokens)
                    source = "/proc/cpuinfo"
                    break
        except OSError as exc:
            source = f"indisponível ({exc})"

    # py-cpuinfo, quando presente, é mais confiável que ambos.
    try:
        import cpuinfo

        info = cpuinfo.get_cpu_info()
        detected = {str(flag).lower() for flag in info.get("flags", [])}
        if detected:
            flags["avx"] = "avx" in detected
            flags["avx2"] = "avx2" in detected
            flags["avx512"] = any(flag.startswith("avx512") for flag in detected)
            source = "py-cpuinfo"
    except Exception:  # noqa: BLE001 — pacote opcional
        pass

    return flags, source


def _total_ram_gb() -> float | None:
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.ullTotalPhys / (1024 ** 3), 1)
        except (AttributeError, OSError):
            return None
        return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round(pages * page_size / (1024 ** 3), 1)
    except (AttributeError, ValueError, OSError):
        return None


def check_cpu() -> CheckResult:
    flags, source = _cpu_flags()
    ram = _total_ram_gb()
    cores = os.cpu_count() or 0

    detail_parts = [
        platform.processor() or platform.machine(),
        f"{cores} núcleos",
    ]
    if ram:
        detail_parts.append(f"{ram} GB de RAM")
    detail_parts.append("AVX: " + ("SIM" if flags["avx"] else "NÃO"))

    return CheckResult(
        name="cpu",
        ok=True,   # informativo: nunca "falha", mas muda todas as outras decisões
        detail=" | ".join(part for part in detail_parts if part),
        metrics={
            "processador": platform.processor() or platform.machine(),
            "sistema": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "nucleos": cores,
            "ram_gb": ram,
            "fonte_das_flags": source,
            **flags,
        },
    )


# ------------------------------------------------------------- onnxruntime

def check_onnxruntime() -> CheckResult:
    """A peça de que Piper, Porcupine e openWakeWord dependem.

    Se isto travar com instrução ilegal, os três caem junto e o plano precisa
    de TTS na nuvem. Se passar, o caminho local está de pé.
    """
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        return CheckResult("onnxruntime", False, error=f"não instalado: {exc}")

    started = time.monotonic()
    providers = ort.get_available_providers()

    # Um modelo mínimo, montado em memória: exercita o motor de inferência sem
    # precisar baixar nada.
    try:
        from onnx import TensorProto, helper

        node = helper.make_node("Add", ["a", "b"], ["c"])
        graph = helper.make_graph(
            [node],
            "soma",
            [
                helper.make_tensor_value_info("a", TensorProto.FLOAT, [4]),
                helper.make_tensor_value_info("b", TensorProto.FLOAT, [4]),
            ],
            [helper.make_tensor_value_info("c", TensorProto.FLOAT, [4])],
        )
        # Opset fixo e antigo de propósito: sem isto, o pacote `onnx` carimba o
        # opset mais novo que ele conhece e o runtime recusa o modelo — o teste
        # falharia por incompatibilidade de versão, não por falta de AVX, que é
        # o que realmente queremos medir aqui.
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
        model.ir_version = 8
        session = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"])
        output = session.run(
            None,
            {
                "a": np.array([1, 2, 3, 4], dtype=np.float32),
                "b": np.array([1, 1, 1, 1], dtype=np.float32),
            },
        )
        inferred = bool(np.allclose(output[0], [2, 3, 4, 5]))
        detail = "importou e executou inferência"
    except ImportError:
        # Sem o pacote `onnx` não dá para montar o modelo, mas importar o
        # runtime já teria travado se faltasse AVX — que é o que interessa aqui.
        inferred = False
        detail = "importou (sem o pacote 'onnx' para testar inferência)"

    return CheckResult(
        name="onnxruntime",
        ok=True,
        detail=detail,
        metrics={
            "versao": ort.__version__,
            "provedores": providers,
            "inferencia_ok": inferred,
            "tempo_s": round(time.monotonic() - started, 3),
        },
    )


# ---------------------------------------------------------------- webrtcvad

def check_webrtcvad() -> CheckResult:
    try:
        import webrtcvad
    except ImportError as exc:
        return CheckResult("webrtcvad", False, error=f"não instalado: {exc}")

    vad = webrtcvad.Vad(2)
    frame = b"\x00\x00" * 480          # 30 ms a 16 kHz, mono, 16-bit
    started = time.monotonic()
    for _ in range(100):
        vad.is_speech(frame, 16000)
    elapsed = time.monotonic() - started

    return CheckResult(
        name="webrtcvad",
        ok=True,
        detail=f"100 frames em {elapsed * 1000:.1f} ms",
        metrics={"ms_por_frame": round(elapsed * 10, 4)},
    )


# ---------------------------------------------------------------- porcupine

def check_porcupine() -> CheckResult:
    try:
        import numpy as np
        import pvporcupine
    except ImportError as exc:
        return CheckResult("porcupine", False, error=f"não instalado: {exc}")

    from james.config import get_secret, load_config

    config = load_config()
    access_key = get_secret(str(config.get("wake_word.access_key_env", "PORCUPINE_ACCESS_KEY")))
    if not access_key:
        return CheckResult(
            "porcupine",
            False,
            error="PORCUPINE_ACCESS_KEY ausente no .env (crie em console.picovoice.ai)",
        )

    keyword = str(config.get("wake_word.keyword", "jarvis")).lower()
    engine = pvporcupine.create(access_key=access_key, keywords=[keyword])
    try:
        silence = np.zeros(engine.frame_length, dtype=np.int16)
        started = time.monotonic()
        for _ in range(100):
            engine.process(silence)
        elapsed = time.monotonic() - started
        frame_ms = engine.frame_length * 1000 / engine.sample_rate
        # Fração de um núcleo consumida pela escuta contínua.
        cpu_share = (elapsed * 1000 / 100) / frame_ms
        return CheckResult(
            name="porcupine",
            ok=True,
            detail=f"'{keyword}' carregada; ~{cpu_share * 100:.1f}% de um núcleo em escuta contínua",
            metrics={
                "palavra": keyword,
                "taxa_hz": engine.sample_rate,
                "frame_length": engine.frame_length,
                "ms_por_frame": round(elapsed * 10, 3),
                "uso_de_nucleo": round(cpu_share, 4),
            },
        )
    finally:
        engine.delete()


# -------------------------------------------------------------------- piper

def check_piper() -> CheckResult:

    from james.config import load_config
    from james.voice.tts import PiperTTS, TTSUnavailable

    config = load_config()
    try:
        tts = PiperTTS(
            voice_path=config.resolve_path("tts.voice_path"),
            binary=config.resolve_path("tts.binary"),
            length_scale=float(config.get("tts.length_scale", 1.0)),
        )
    except TTSUnavailable as exc:
        return CheckResult("piper", False, error=str(exc))

    frase = (
        "Boa noite, senhor. Todos os sistemas estão operacionais e prontos para uso."
    )
    started = time.monotonic()
    pcm = tts.synthesize(frase)
    elapsed = time.monotonic() - started

    audio_seconds = len(pcm) / (tts.sample_rate * 2)
    ratio = elapsed / audio_seconds if audio_seconds > 0 else float("inf")

    # Acima de 1.0 o Piper sintetiza mais devagar do que a fala dura: o James
    # nunca acompanharia uma conversa e o TTS precisaria ir para a nuvem.
    ok = ratio < 1.0
    return CheckResult(
        name="piper",
        ok=ok,
        detail=(
            f"{audio_seconds:.1f}s de fala em {elapsed:.2f}s "
            f"({ratio:.2f}x tempo real, backend {tts.backend})"
        ),
        error="" if ok else f"lento demais ({ratio:.2f}x): o TTS local não acompanha a fala",
        metrics={
            "backend": tts.backend,
            "taxa_hz": tts.sample_rate,
            "segundos_de_audio": round(audio_seconds, 2),
            "segundos_de_sintese": round(elapsed, 2),
            "razao_tempo_real": round(ratio, 3),
        },
    )


# ------------------------------------------------------------------ whisper

def check_whisper() -> CheckResult:
    import math
    import struct

    from james.config import load_config
    from james.voice.stt import STTUnavailable, WhisperCppSTT

    config = load_config()
    try:
        stt = WhisperCppSTT(
            binary=config.resolve_path("stt.binary"),
            model=config.resolve_path("stt.model"),
            audio_format=config.audio,
            language=str(config.get("stt.language", "pt")),
            timeout_s=int(config.get("stt.timeout_s", 60)),
            threads=int(config.get("stt.threads", 4)),
        )
    except STTUnavailable as exc:
        # Não é crítico para a Fase 1 (o caminho comum manda o áudio direto ao
        # Gemini), mas custa duas capacidades reais — o veredito explica quais.
        return CheckResult(
            "whisper",
            False,
            error=str(exc),
            detail="sem whisper.cpp: sem modo offline e sem confirmação de ações de risco",
            critical=False,
        )

    # 5 segundos de tom modulado: não é fala, então o texto não importa — o que
    # medimos é o tempo de processamento por segundo de áudio.
    fmt = config.audio
    total = fmt.sample_rate * 5
    samples = bytearray()
    for index in range(total):
        value = int(9000 * math.sin(2 * math.pi * 220 * index / fmt.sample_rate))
        samples += struct.pack("<h", value)

    started = time.monotonic()
    stt.transcribe(bytes(samples))
    elapsed = time.monotonic() - started
    ratio = elapsed / 5.0

    # Acima de 1x, transcrever localmente já custa mais que a própria fala — é
    # exatamente por isso que o caminho comum manda o áudio direto ao Gemini.
    return CheckResult(
        name="whisper",
        ok=True,
        detail=f"5s de áudio em {elapsed:.1f}s ({ratio:.2f}x tempo real)",
        metrics={"segundos": round(elapsed, 2), "razao_tempo_real": round(ratio, 2)},
        critical=False,
    )


# --------------------------------------------------------------------- rede

def check_network() -> CheckResult:
    """Mede o custo de rede do caminho "áudio direto para a nuvem".

    Se a latência aqui for alta, a decisão de mandar o áudio para o Gemini perde
    a vantagem e o caminho local volta a fazer sentido.
    """
    try:
        import httpx
    except ImportError as exc:
        return CheckResult("rede", False, error=f"httpx não instalado: {exc}")

    host = "https://generativelanguage.googleapis.com/"
    try:
        started = time.monotonic()
        with httpx.Client(timeout=15.0) as client:
            client.get(host)
        handshake = time.monotonic() - started
    except Exception as exc:  # noqa: BLE001 — qualquer falha de rede
        return CheckResult("rede", False, error=f"sem acesso a {host}: {exc}", critical=False)

    # ~100 KB é a ordem de grandeza de 3 segundos de comando em WAV 16 kHz.
    payload = b"0" * 100_000
    upload_seconds = None
    try:
        started = time.monotonic()
        with httpx.Client(timeout=30.0) as client:
            client.post(host, content=payload)
        upload_seconds = time.monotonic() - started
    except Exception:  # noqa: BLE001 — o servidor pode cortar antes; medida é indicativa
        upload_seconds = None

    detail = f"conexão em {handshake * 1000:.0f} ms"
    if upload_seconds is not None:
        detail += f"; ~100 KB enviados em {upload_seconds * 1000:.0f} ms"

    return CheckResult(
        name="rede",
        ok=True,
        detail=detail,
        metrics={
            "handshake_ms": round(handshake * 1000, 1),
            "upload_100kb_ms": round(upload_seconds * 1000, 1) if upload_seconds else None,
        },
    )


# ----------------------------------------------------------------------- Qt

def check_qt() -> CheckResult:
    try:
        from PySide6.QtGui import QColor, QPainter, QPixmap
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        return CheckResult("qt", False, error=f"PySide6 não instalado: {exc}")

    app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(240, 200)
    pixmap.fill(QColor(0, 0, 0, 0))

    # Mede o custo de desenhar o HUD: é este número que decide se o overlay
    # roda liso ou se o perfil visual precisa ser reduzido.
    started = time.monotonic()
    frames = 60
    for index in range(frames):
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(QColor(0, 170, 225, 180))
        painter.drawEllipse(20 + index % 10, 20, 120, 120)
        painter.end()
    elapsed = time.monotonic() - started
    fps = frames / elapsed if elapsed > 0 else 0.0
    app.processEvents()

    ok = fps >= 60
    return CheckResult(
        name="qt",
        ok=ok,
        detail=f"desenho do HUD a ~{fps:.0f} quadros por segundo",
        error="" if ok else "desenho lento; considere reduzir overlay.fps",
        metrics={"fps_estimado": round(fps, 1)},
        critical=False,
    )


def check_gestures() -> CheckResult:
    """Mede o custo real do rastreamento de mão nesta máquina.

    É o número que decide uma questão em aberto: se o MediaPipe local trava a
    máquina mesmo a 6 quadros por segundo, o rastreamento precisa sair daqui e
    virar chamada a uma API. O modo de gestos foi escrito com essa saída pronta
    — trocar o `detector_factory` é tudo o que muda.

    O teste é **não crítico** de propósito: o modo nasce desligado, então uma
    máquina sem MediaPipe roda o James inteiro sem perder nada.
    """
    try:
        import numpy as np
    except ImportError as exc:
        return CheckResult(
            "gestos", False, error=f"numpy não instalado: {exc}", critical=False
        )
    try:
        import mediapipe  # noqa: F401
    except ImportError:
        return CheckResult(
            "gestos",
            False,
            detail="MediaPipe não instalado — o modo de gestos fica indisponível",
            error='instale com: pip install -e ".[gestures]"',
            critical=False,
        )

    from pathlib import Path

    modelo = Path("models/hand_landmarker.task")
    if not modelo.exists():
        return CheckResult(
            "gestos",
            False,
            detail=f"modelo não encontrado em {modelo}",
            error="baixe hand_landmarker.task (link no config.yaml)",
            critical=False,
        )

    from james.modes.gestures import MediaPipeHands

    started = time.monotonic()
    detector = MediaPipeHands(str(modelo))
    carga_s = time.monotonic() - started

    # Ruído, não imagem preta: um quadro uniforme sai barato demais e daria um
    # número otimista que não se repete com a webcam de verdade.
    gerador = np.random.default_rng(7)
    quadro = gerador.integers(0, 255, (480, 640, 3), dtype=np.uint8)

    quadros = 20
    started = time.monotonic()
    try:
        for indice in range(quadros):
            detector.detectar(quadro, indice * 100)
    finally:
        detector.fechar()
    elapsed = time.monotonic() - started

    por_quadro_ms = (elapsed / quadros) * 1000
    fps_possivel = quadros / elapsed if elapsed > 0 else 0.0
    # O padrão do config é 6 fps. Se a máquina não sustenta o dobro disso, o
    # modo vai competir com o pipeline de voz enquanto estiver ligado.
    ok = fps_possivel >= 12
    return CheckResult(
        name="gestos",
        ok=ok,
        detail=(
            f"{por_quadro_ms:.0f} ms por quadro (~{fps_possivel:.0f} fps possíveis; "
            f"o modo usa 6), modelo carregado em {carga_s:.1f} s"
        ),
        error=""
        if ok
        else "rastreamento local lento — considere trocar por uma API de visão",
        metrics={
            "ms_por_quadro": round(por_quadro_ms, 1),
            "fps_possivel": round(fps_possivel, 1),
            "carga_s": round(carga_s, 2),
        },
        critical=False,
    )


CHECKS: dict[str, Callable[[], CheckResult]] = {
    "cpu": check_cpu,
    "onnxruntime": check_onnxruntime,
    "webrtcvad": check_webrtcvad,
    "porcupine": check_porcupine,
    "piper": check_piper,
    "whisper": check_whisper,
    "rede": check_network,
    "qt": check_qt,
    "gestos": check_gestures,
}


# --------------------------------------------------------------- orquestração

def _run_isolated(name: str) -> CheckResult:
    """Roda um teste em subprocesso, sobrevivendo até a "instrução ilegal"."""
    command = [sys.executable, "-m", "james.diagnostics.check_hardware", "--run", name]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
            cwd=str(PROJECT_ROOT),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name, False, error=f"travou (mais de {_SUBPROCESS_TIMEOUT_S}s sem terminar)"
        )
    except OSError as exc:
        return CheckResult(name, False, error=f"não consegui iniciar o teste: {exc}")

    code = completed.returncode
    if code in (_SIGILL_POSIX, _SIGILL_WINDOWS, _SIGILL_WINDOWS_UNSIGNED):
        return CheckResult(
            name,
            False,
            error=(
                "INSTRUÇÃO ILEGAL — a biblioteca foi compilada para um processador "
                "com instruções que este não tem (tipicamente AVX)."
            ),
            metrics={"codigo_de_saida": code},
        )

    for line in reversed(completed.stdout.decode("utf-8", errors="replace").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return CheckResult(**json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue

    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    return CheckResult(
        name,
        False,
        error=f"terminou com código {code} sem devolver resultado. {stderr[-400:]}",
    )


def _print_report(results: list[CheckResult]) -> None:
    print()
    print("=" * 72)
    print("  JAMES — RELATÓRIO DE COMPATIBILIDADE (Fase 0)")
    print("=" * 72)
    for result in results:
        mark = "  OK  " if result["ok"] else ("FALHOU" if result["critical"] else " AVISO")
        print(f"[{mark}] {result['name']:<12} {result['detail'] or result['error']}")
        if result["error"] and result["detail"]:
            print(f"{'':>10}{result['error']}")
    print("-" * 72)


def _verdict(results: list[CheckResult]) -> dict[str, Any]:
    """Traduz os números em decisões concretas para as fases seguintes."""
    by_name = {result["name"]: result for result in results}
    notes: list[str] = []

    cpu = by_name.get("cpu", {}).get("metrics", {})
    has_avx = bool(cpu.get("avx"))
    if has_avx:
        notes.append(
            "Este processador TEM AVX. A troca de faster-whisper por whisper.cpp e "
            "de Silero por webrtcvad pode ter sido desnecessária — vale reavaliar, "
            "porque faster-whisper é bem mais rápido."
        )
    else:
        notes.append(
            "Sem AVX confirmado. whisper.cpp e webrtcvad são as escolhas certas."
        )

    ram = cpu.get("ram_gb") or 0
    qt_fps = by_name.get("qt", {}).get("metrics", {}).get("fps_estimado") or 0
    perfil = "light" if (ram and ram < 6) or (qt_fps and qt_fps < 120) else "full"
    notes.append(
        f"Perfil visual sugerido para o overlay: '{perfil}'."
        + (" QWebEngineView (Fase 3) tende a pesar aqui." if perfil == "light" else "")
    )

    if "piper" in by_name:
        piper = by_name["piper"]
        if piper.get("ok"):
            ratio = piper.get("metrics", {}).get("razao_tempo_real")
            notes.append(f"TTS local viável ({ratio}x tempo real).")
        else:
            notes.append(
                "Piper não passou: sem TTS local o James fica mudo. "
                "Resolva isto antes da Fase 1."
            )

    whisper = by_name.get("whisper")
    if whisper is None:
        pass
    elif not whisper.get("ok"):
        notes.append(
            "Sem whisper.cpp o James ainda conversa (o áudio vai direto ao Gemini), "
            "mas fica SEM modo offline e SEM confirmação de ações de risco — e, sem "
            "poder confirmar, toda ação de Nível 2 é recusada."
        )
    else:
        ratio = whisper.get("metrics", {}).get("razao_tempo_real", 0)
        if ratio and ratio > 1.0:
            notes.append(
                f"Transcrição local a {ratio}x tempo real: lenta demais para o caminho "
                "comum, o que confirma a decisão de mandar o áudio direto ao Gemini."
            )

    rede = by_name.get("rede", {}).get("metrics", {})
    handshake = rede.get("handshake_ms")
    if handshake and handshake > 600:
        notes.append(
            f"Rede lenta ({handshake} ms de conexão): o áudio direto para o Gemini "
            "perde vantagem e a meta de 3s de latência fica em risco."
        )

    criticos = [r["name"] for r in results if not r["ok"] and r["critical"]]
    return {
        "pronto_para_fase_1": not criticos,
        "testes_criticos_falhos": criticos,
        "tem_avx": has_avx,
        "perfil_visual_sugerido": perfil,
        "observacoes": notes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prova de compatibilidade do James.")
    parser.add_argument("--run", help="roda um único teste e imprime JSON (uso interno)")
    parser.add_argument("--only", nargs="*", help="roda apenas os testes indicados")
    args = parser.parse_args(argv)

    if args.run:
        check = CHECKS.get(args.run)
        if check is None:
            print(json.dumps(CheckResult(args.run, False, error="teste desconhecido")))
            return 1
        try:
            result = check()
        except Exception as exc:  # noqa: BLE001 — o resultado É o relatório
            result = CheckResult(args.run, False, error=f"{type(exc).__name__}: {exc}")
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0

    names = args.only if args.only else list(CHECKS)
    unknown = [name for name in names if name not in CHECKS]
    if unknown:
        print(f"Testes desconhecidos: {', '.join(unknown)}", file=sys.stderr)
        return 2

    print("Rodando a prova de compatibilidade. Cada teste roda isolado.\n")
    results: list[CheckResult] = []
    for name in names:
        print(f"  ... {name}", flush=True)
        results.append(_run_isolated(name))

    _print_report(results)
    verdict = _verdict(results)

    print("VEREDITO")
    for note in verdict["observacoes"]:
        print(f"  - {note}")
    print()
    if verdict["pronto_para_fase_1"]:
        print("  Nenhum teste crítico falhou: pode seguir para a Fase 1.")
    else:
        print(
            "  Testes críticos com falha: "
            + ", ".join(verdict["testes_criticos_falhos"])
        )
    print("=" * 72)

    report = {
        "gerado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "resultados": results,
        "veredito": verdict,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nRelatório salvo em {REPORT_PATH}\n")
    return 0 if verdict["pronto_para_fase_1"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
