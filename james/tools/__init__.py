"""Catálogo de tools do James (MCP in-process).

O transporte SSE foi retirado do MVP (C9): numa máquina que já é o gargalo, um
terceiro processo mais HTTP local custa caro e não compra nada, já que só o
próprio James consome estas tools. A arquitetura MCP continua — registro,
schema declarativo, desacoplamento do modelo — apenas sem a camada de rede.
Ela volta quando os nós remotos entrarem.
"""

from james.tools.registry import Tool, ToolRegistry, ToolResult


def build_registry(
    config, guard, memory=None, facts=None, skills=None, modes=None
) -> ToolRegistry:
    """Monta o catálogo completo. Cada módulo registra as suas."""
    from james.tools import (
        apps, briefing, files, investing, office, research, sequence, system, vision, web,
    )

    registry = ToolRegistry()
    apps.register(registry, config, guard)
    web.register(registry, config, guard)
    system.register(registry, config, guard)
    files.register(registry, config, guard)
    office.register(registry, config, guard)
    vision.register(registry, config, guard)
    briefing.register(registry, config, guard, memory)
    investing.register(registry, config, guard)
    research.register(registry, config, guard)

    if memory is not None:
        from james.tools import memory as memory_tools

        memory_tools.register(registry, config, guard, memory)

    if facts is not None:
        from james.tools import knowledge

        knowledge.register_facts(registry, config, guard, facts)

    if skills is not None:
        from james.tools import knowledge

        knowledge.register_skills(registry, config, guard, skills)

    if modes is not None:
        from james.tools import modes as mode_tools

        mode_tools.register(registry, config, guard, modes)

    # Por último: sequência e delegação consultam o catálogo — a primeira
    # para validar os passos, a segunda para montar os recortes por perfil.
    from james.tools import delegation

    sequence.register(registry, config, guard)
    delegation.register(registry, config, guard)
    return registry


__all__ = ["Tool", "ToolRegistry", "ToolResult", "build_registry"]
