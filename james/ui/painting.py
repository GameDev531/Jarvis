"""Primitivas de desenho compartilhadas entre a janela e o HUD."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QRadialGradient


def pulse(elapsed: float, rate: float) -> float:
    """Pulsação por ondas sobrepostas, entre 0 e 1.

    Um seno simples fica mecânico e previsível. Somando frequências diferentes
    e elevando uma delas a uma potência alta (surtos raros e súbitos), o brilho
    ganha uma respiração que não parece um loop.
    """
    if rate <= 0:
        return 0.5
    base = 0.5 + 0.5 * math.sin(elapsed * rate)
    fast = 0.5 + 0.5 * math.sin(elapsed * rate * 2.7 + 1.3)
    surge = max(0.0, math.sin(elapsed * rate * 0.37)) ** 8
    return min(1.0, 0.5 * base + 0.22 * fast + 0.35 * surge)


def ease_out(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def ease_in(t: float) -> float:
    return t * t * t


def angular_path(rect: QRectF, cut: float) -> QPainterPath:
    """Retângulo com cantos chanfrados — a silhueta angular do HUD."""
    path = QPainterPath()
    left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
    cut = min(cut, rect.width() / 3.0, rect.height() / 3.0)
    path.moveTo(left + cut, top)
    path.lineTo(right - cut, top)
    path.lineTo(right, top + cut)
    path.lineTo(right, bottom - cut)
    path.lineTo(right - cut, bottom)
    path.lineTo(left + cut, bottom)
    path.lineTo(left, bottom - cut)
    path.lineTo(left, top + cut)
    path.closeSubpath()
    return path


def draw_orb(
    painter,
    center: QPointF,
    radius: float,
    palette,
    intensity: float,
    alpha_scale: float = 1.0,
    flicker: float = 1.0,
) -> None:
    """Halo com gradiente radial mais um núcleo sólido."""
    halo_radius = radius * (1.9 + 0.55 * intensity)
    gradient = QRadialGradient(center, halo_radius)

    core = QColor(*palette.core)
    core.setAlpha(_clamp_alpha(215 * (0.55 + 0.45 * intensity) * alpha_scale * flicker))
    mid = QColor(*palette.glow)
    mid.setAlpha(_clamp_alpha(110 * alpha_scale * intensity))
    edge = QColor(*palette.glow)
    edge.setAlpha(0)

    gradient.setColorAt(0.0, core)
    gradient.setColorAt(0.42, mid)
    gradient.setColorAt(1.0, edge)

    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(center, halo_radius, halo_radius)

    solid = QColor(*palette.core)
    solid.setAlpha(_clamp_alpha(235 * alpha_scale * flicker))
    painter.setBrush(solid)
    painter.drawEllipse(center, radius * 0.42, radius * 0.42)


def draw_scan_ring(
    painter, center: QPointF, radius: float, palette, elapsed: float, alpha_scale: float = 1.0
) -> None:
    """Arco girando — sinaliza trabalho em andamento."""
    ring = QColor(*palette.core)
    ring.setAlpha(_clamp_alpha(200 * alpha_scale))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(ring, 2.0, Qt.SolidLine, Qt.RoundCap))
    box = QRectF(
        center.x() - radius * 1.55,
        center.y() - radius * 1.55,
        radius * 3.1,
        radius * 3.1,
    )
    start_angle = int((-elapsed * 210) % 360) * 16
    painter.drawArc(box, start_angle, 110 * 16)


def draw_scanlines(painter, rect: QRectF, alpha: int = 46, spacing: float = 3.0) -> None:
    """Linhas horizontais de tubo. Custo desprezível, efeito grande."""
    painter.setPen(QPen(QColor(0, 0, 0, alpha), 1.0))
    y = rect.top()
    while y < rect.bottom():
        painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        y += spacing


def draw_vignette(painter, rect: QRectF, alpha: int = 120) -> None:
    gradient = QRadialGradient(rect.center(), max(rect.width(), rect.height()) * 0.62)
    gradient.setColorAt(0.55, QColor(0, 0, 0, 0))
    gradient.setColorAt(1.0, QColor(0, 0, 0, alpha))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawRect(rect)


def _clamp_alpha(value: float) -> int:
    return max(0, min(255, int(value)))
