from james.ui.states import UiState, caption_for, palette_for


def build_interface(config, app_name: str = "James"):
    """Cria a interface conforme `interface.mode`. None se desabilitada.

    Os dois modos expõem os mesmos sinais, então o orquestrador não precisa
    saber qual está ativo.
    """
    if not config.get("interface.enabled", True):
        return None

    mode = str(config.get("interface.mode", "window")).lower()
    fps = int(config.get("interface.fps", 30))

    if mode == "hud":
        from james.ui.overlay import Overlay

        return Overlay(
            app_name=app_name,
            size=int(config.get("interface.size", 260)),
            position=str(config.get("interface.position", "bottom-right")),
            margin=int(config.get("interface.margin", 40)),
            fps=fps,
        )

    from james.ui.window import MainWindow

    return MainWindow(
        app_name=app_name,
        fps=fps,
        show_transcript=bool(config.get("interface.show_transcript", True)),
        start_minimized=bool(config.get("interface.start_minimized", True)),
        raise_on_wake=bool(config.get("interface.raise_on_wake", True)),
    )


__all__ = ["UiState", "build_interface", "caption_for", "palette_for"]
