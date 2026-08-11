"""Tools de sistema: hora, estado da máquina e volume.

Todas devolvem o resultado já em linguagem falada, então não precisam de um
segundo ciclo com o modelo — são o caso típico de fire-and-forget (1 requisição
em vez de 2), e pelo roteador local chegam a custar zero.
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import sys
from datetime import datetime

from james.logs import get_logger
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.system")

# Teclas multimídia do Windows.
_VK_VOLUME_MUTE = 0xAD
_VK_VOLUME_DOWN = 0xAE
_VK_VOLUME_UP = 0xAF
_KEYEVENTF_KEYUP = 0x0002
# Cada toque move ~2% do volume; 4 toques dão uma mudança perceptível.
_VOLUME_STEPS = 4

_UNITS = {
    0: "meia-noite", 1: "uma", 2: "duas", 3: "três", 4: "quatro", 5: "cinco",
    6: "seis", 7: "sete", 8: "oito", 9: "nove", 10: "dez", 11: "onze",
    12: "meio-dia",
}


def format_time_pt(moment: datetime) -> str:
    """Hora em português falado, não em dígitos.

    Um TTS lendo "14:35" costuma sair como "quatorze dois pontos trinta e
    cinco"; escrever por extenso é o que faz soar natural.
    """
    hour24 = moment.hour
    minute = moment.minute

    if hour24 == 0 and minute == 0:
        return "É meia-noite em ponto"
    if hour24 == 12 and minute == 0:
        return "É meio-dia em ponto"

    hour12 = hour24 % 12
    spoken_hour = _UNITS.get(hour12 if hour12 else 12, str(hour12))
    if hour24 == 0:
        spoken_hour = "meia-noite"
    elif hour24 == 12:
        spoken_hour = "meio-dia"

    singular = hour24 in (1, 13) or spoken_hour in ("meia-noite", "meio-dia")
    verb = "É" if singular else "São"
    unit = "" if spoken_hour in ("meia-noite", "meio-dia") else (
        " hora" if singular else " horas"
    )

    if minute == 0:
        return f"{verb} {spoken_hour}{unit} em ponto"
    if minute == 30:
        return f"{verb} {spoken_hour}{unit} e meia"
    return f"{verb} {spoken_hour}{unit} e {minute}"


def _period_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "da manhã"
    if 12 <= hour < 18:
        return "da tarde"
    if 18 <= hour < 24:
        return "da noite"
    return "da madrugada"


def que_horas_sao(args: dict) -> ToolResult:
    now = datetime.now()
    spoken = f"{format_time_pt(now)}, {_period_of_day(now.hour)}"
    return ToolResult(
        ok=True,
        speech=spoken,
        data={"hora": now.strftime("%H:%M"), "data": now.strftime("%d/%m/%Y")},
    )


def _memory_gb() -> tuple[float, float] | None:
    """(total, disponível) em GB. None quando não dá para descobrir."""
    if sys.platform == "win32":
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
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
        except (AttributeError, OSError) as exc:
            logger.debug("GlobalMemoryStatusEx indisponível: %s", exc)
            return None
        gb = 1024 ** 3
        return status.ullTotalPhys / gb, status.ullAvailPhys / gb

    try:
        values: dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    values[key] = int(parts[0])
        total = values.get("MemTotal")
        available = values.get("MemAvailable", values.get("MemFree"))
        if total is None or available is None:
            return None
        return total / (1024 ** 2), available / (1024 ** 2)
    except (OSError, ValueError) as exc:
        logger.debug("Leitura de /proc/meminfo falhou: %s", exc)
        return None


def info_sistema(args: dict) -> ToolResult:
    partes: list[str] = []
    data: dict[str, object] = {
        "sistema": f"{platform.system()} {platform.release()}",
        "processador": platform.processor() or "desconhecido",
        "nucleos": os.cpu_count() or 0,
    }

    memory = _memory_gb()
    if memory is not None:
        total, available = memory
        data["memoria_total_gb"] = round(total, 1)
        data["memoria_livre_gb"] = round(available, 1)
        partes.append(
            f"{available:.1f} de {total:.1f} gigabytes de memória livres"
        )

    try:
        usage = shutil.disk_usage(os.path.abspath(os.sep))
        free_gb = usage.free / (1024 ** 3)
        data["disco_livre_gb"] = round(free_gb, 1)
        partes.append(f"{free_gb:.0f} gigabytes livres no disco")
    except OSError as exc:
        logger.debug("Leitura de espaço em disco falhou: %s", exc)

    nucleos = data["nucleos"]
    if nucleos:
        partes.append(f"{nucleos} núcleos de processamento")

    if partes:
        spoken = "O sistema está com " + ", ".join(partes) + "."
    else:
        spoken = "Não consegui ler os sensores do sistema."

    return ToolResult(ok=True, speech=spoken, data=data)


def _press_key(virtual_key: int, times: int = 1) -> bool:
    if sys.platform != "win32":
        logger.warning("Controle de volume por tecla só está implementado no Windows.")
        return False
    try:
        user32 = ctypes.windll.user32
        for _ in range(max(1, times)):
            user32.keybd_event(virtual_key, 0, 0, 0)
            user32.keybd_event(virtual_key, 0, _KEYEVENTF_KEYUP, 0)
        return True
    except (AttributeError, OSError) as exc:
        logger.error("Falha ao enviar tecla de volume: %s", exc)
        return False


def ajustar_volume(args: dict) -> ToolResult:
    acao = str(args.get("acao", "")).strip().lower()
    mapping = {
        "aumentar": (_VK_VOLUME_UP, _VOLUME_STEPS, "Aumentando o volume."),
        "diminuir": (_VK_VOLUME_DOWN, _VOLUME_STEPS, "Diminuindo o volume."),
        "mutar": (_VK_VOLUME_MUTE, 1, "Alternando o som."),
    }
    if acao not in mapping:
        return ToolResult.failure("Não entendi o que fazer com o volume.")

    key, times, ack = mapping[acao]
    if not _press_key(key, times):
        return ToolResult.failure("Não consegui ajustar o volume nesta máquina.")
    return ToolResult(ok=True, ack=ack, data={"acao": acao})


def register(registry: ToolRegistry, config, guard) -> None:
    registry.register(
        Tool(
            name="que_horas_sao",
            description="Diz a hora e a data atuais.",
            parameters={"type": "object", "properties": {}},
            handler=que_horas_sao,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="info_sistema",
            description="Relata memória livre, espaço em disco e processador da máquina.",
            parameters={"type": "object", "properties": {}},
            handler=info_sistema,
            fire_and_forget=True,
        )
    )
    registry.register(
        Tool(
            name="ajustar_volume",
            description="Aumenta, diminui ou silencia o volume do sistema.",
            parameters={
                "type": "object",
                "properties": {
                    "acao": {
                        "type": "string",
                        "enum": ["aumentar", "diminuir", "mutar"],
                        "description": "O que fazer com o volume.",
                    }
                },
                "required": ["acao"],
            },
            handler=ajustar_volume,
            fire_and_forget=True,
        )
    )
