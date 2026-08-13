"""Catálogo de tools do James (MCP in-process).

O transporte SSE foi retirado do MVP (C9): numa máquina que já é o gargalo, um
terceiro processo mais HTTP local custa caro e não compra nada, já que só o
próprio James consome estas tools. A arquitetura MCP continua — registro,
schema declarativo, desacoplamento do modelo — apenas sem a camada de rede.
Ela volta quando os nós remotos entrarem.
"""

from james.tools.registry import Tool, ToolRegistry, ToolResult


def build_registry(config, guard, memory=None) -> ToolRegistry:
    """Monta o catálogo completo. Cada módulo registra as suas."""
    from james.tools import apps, briefing, files, office, system, vision, web

    registry = ToolRegistry()
    apps.register(registry, config, guard)
    web.register(registry, config, guard)
    system.register(registry, config, guard)
    files.register(registry, config, guard)
    office.register(registry, config, guard)
    vision.register(registry, config, guard)
    briefing.register(registry, config, guard, memory)

    if memory is not None:
        from james.tools import memory as memory_tools

        memory_tools.register(registry, config, guard, memory)

    return registry


__all__ = ["Tool", "ToolRegistry", "ToolResult", "build_registry"]
