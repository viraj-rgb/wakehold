"""Icons drawn at runtime: a coffee cup whose steam rises while the PC is held awake."""
from __future__ import annotations

import math
import winreg

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)

SIZE = 64
STEAM_FRAMES = 10
STEAM_COLOR = QColor("#F28C28")
DIM_COLOR = QColor("#9AA0A6")
CUP_ON_TILE = QColor("#2B2B2B")
TILE_COLOR = QColor("#FAFAFA")

ANIMATED_STATES = frozenset({"busy", "grace", "manual"})
WISP_X = (20.5, 30.0, 39.5)
STEAM_TOP, STEAM_BOTTOM = 3.0, 24.0


def taskbar_is_light() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def _cup_path() -> QPainterPath:
    body = QPainterPath()
    body.moveTo(12, 27)
    body.lineTo(48, 27)
    body.lineTo(45, 47)
    body.quadTo(44, 52, 39, 52)
    body.lineTo(21, 52)
    body.quadTo(16, 52, 15, 47)
    body.closeSubpath()
    handle = QPainterPath()
    handle.addEllipse(QRectF(39, 30, 18, 16))
    hole = QPainterPath()
    hole.addEllipse(QRectF(44, 34.5, 8.5, 7))
    saucer = QPainterPath()
    saucer.addRoundedRect(QRectF(7, 55, 50, 4.5), 2.25, 2.25)
    return body.united(handle.subtracted(hole)).united(saucer)


def _wisp(x0: float, phase: float) -> QPainterPath:
    path = QPainterPath()
    steps = 24
    for i in range(steps + 1):
        y = STEAM_BOTTOM - (STEAM_BOTTOM - STEAM_TOP) * i / steps
        rise = (STEAM_BOTTOM - y) / (STEAM_BOTTOM - STEAM_TOP)
        x = x0 + (0.6 + 1.9 * rise) * math.sin(rise * 2.0 * math.pi - phase)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    return path


def _draw_steam(painter: QPainter, frame: int, color: QColor) -> None:
    fade = QLinearGradient(QPointF(0, STEAM_BOTTOM), QPointF(0, STEAM_TOP))
    fade.setColorAt(0.0, color)
    top = QColor(color)
    top.setAlpha(40)
    fade.setColorAt(1.0, top)
    pen = QPen(QBrush(fade), 3.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    base = 2 * math.pi * frame / STEAM_FRAMES
    for index, x0 in enumerate(WISP_X):
        painter.drawPath(_wisp(x0, base + index * 1.9))


def _new_canvas() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(SIZE, SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


def make_icon(state: str, frame: int = 0) -> QIcon:
    """Tray icon for a controller state; `frame` animates the steam."""
    neutral = QColor("#202124") if taskbar_is_light() else QColor("#F1F3F4")
    pixmap, painter = _new_canvas()
    cup = _cup_path()
    if state in ANIMATED_STATES:
        painter.fillPath(cup, QBrush(neutral))
        _draw_steam(painter, frame % STEAM_FRAMES, STEAM_COLOR)
    elif state in ("paused", "off", "battery"):
        dim = QColor(DIM_COLOR)
        dim.setAlpha(140 if state == "off" else 255)
        painter.fillPath(cup, QBrush(dim))
        if state == "paused":
            painter.fillRect(QRectF(46, 4, 5, 16), dim)
            painter.fillRect(QRectF(55, 4, 5, 16), dim)
    else:
        painter.fillPath(cup, QBrush(neutral))
    painter.end()
    return QIcon(pixmap)


def app_pixmap(size: int) -> QPixmap:
    """Full-colour app icon: cup with steam on a light rounded tile."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / SIZE, size / SIZE)
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(1, 1, 62, 62), 14, 14)
    painter.fillPath(tile, QBrush(TILE_COLOR))
    painter.translate(SIZE * 0.1, SIZE * 0.08)
    painter.scale(0.8, 0.8)
    painter.fillPath(_cup_path(), QBrush(CUP_ON_TILE))
    _draw_steam(painter, 0, STEAM_COLOR)
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_pixmap(size))
    return icon
