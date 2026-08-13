"""Modos: capacidades que ligam sob comando e ficam desligadas por padrão."""

from james.modes.actions import GestureActions
from james.modes.base import Mode, ModeError, ModeInfo, ModeManager, ModeState


def build_manager(config, *, on_acao=None) -> ModeManager:
    """Monta o gerente com os modos habilitados no `config.yaml`.

    Nada aqui abre hardware: construir um modo é barato, e o custo real só
    aparece quando alguém manda ligar. Um modo desabilitado no config nem é
    registrado — assim "ativa a webcam" responde "não conheço esse modo" em
    vez de falhar no meio do caminho.
    """
    from james.modes.gestures import (
        GestureMode,
        MediaPipeHands,
        OpenCVCamera,
        carregar_mapeamento,
    )

    manager = ModeManager()
    gestos = config.section("modos.gestos")
    if bool(gestos.get("enabled", True)) and on_acao is not None:
        indice = int(config.get("camera.device_index", 0))
        modelo = str(gestos.get("modelo", "models/hand_landmarker.task"))
        caminho = config.resolve_path("modos.gestos.modelo", modelo)
        manager.register(
            GestureMode(
                on_acao=on_acao,
                fps=float(gestos.get("fps", 6)),
                estabilidade=int(gestos.get("estabilidade", 4)),
                descanso_s=float(gestos.get("descanso_s", 1.5)),
                timeout_min=float(gestos.get("timeout_min", 10)),
                mapeamento=carregar_mapeamento(gestos.get("mapeamento")),
                camera_factory=lambda: OpenCVCamera(indice),
                detector_factory=lambda: MediaPipeHands(str(caminho)),
            )
        )
    return manager


__all__ = [
    "GestureActions",
    "Mode",
    "ModeError",
    "ModeInfo",
    "ModeManager",
    "ModeState",
    "build_manager",
]
