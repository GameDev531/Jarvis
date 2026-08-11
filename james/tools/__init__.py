"""Catálogo de tools do James (MCP in-process).

O transporte SSE foi retirado do MVP (C9): numa máquina que já é o gargalo, um
terceiro processo mais HTTP local custa caro e não compra nada, já que só o
próprio James consome estas tools. A arquitetura MCP continua — registro,
schema declarativo, desacoplamento do modelo — apenas sem a camada de rede.
Ela volta quando os nós remotos entrarem (Fase 7), que é quando é necessária.
"""

from james.tools.registry import Tool, ToolRegistry, ToolResult


def build_registry(config, guard) -> ToolRegistry:
    """Monta o catálogo completo. Cada módulo registra as suas."""
    from james.tools import apps, system, web

    registry = ToolRegistry()
    apps.register(registry, config, guard)
    web.register(registry, config, guard)
    system.register(registry, config, guard)
    return registry


__all__ = ["Tool", "ToolRegistry", "ToolResult", "build_registry"]
