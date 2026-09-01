"""Controle do navegador — o Ultron mexendo no Chrome de verdade."""

from james.browser.driver import BrowserUnavailable, NavegadorDriver
from james.browser.inspector import inspecionar

__all__ = ["BrowserUnavailable", "NavegadorDriver", "inspecionar"]
