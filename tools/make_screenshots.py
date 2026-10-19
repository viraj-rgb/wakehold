"""Renders the README images from the app's real widgets, using sample data.

Usage: python tools/make_screenshots.py
Writes docs/images/*.png and docs/images/steam.gif
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "images"

from PIL import Image  # noqa: E402
from PyQt6.QtCore import QBuffer, QIODevice, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu  # noqa: E402

from activity import AGENT_KIND, APP_KIND, AppReading  # noqa: E402
from config import Settings  # noqa: E402
from icons import STEAM_FRAMES, app_pixmap, make_icon  # noqa: E402
from settings_dialog import SettingsDialog  # noqa: E402

SAMPLE = [
    AppReading("agent:claude", "Claude Code", AGENT_KIND, 42.0, 900_000, 37),
    AppReading("handbrake.exe", "HandBrake.exe", APP_KIND, 380.0, 6_000_000, 1),
    AppReading("browser-downloads", "Browser download", APP_KIND, 0.0, 0.0, 1),
    AppReading("explorer.exe", "explorer.exe", APP_KIND, 1.2, 14_000, 2),
    AppReading("svchost.exe", "svchost.exe", APP_KIND, 6.0, 30_000, 87),
]
BUSY = {"agent:claude", "handbrake.exe", "browser-downloads"}
BG = QColor("#1F1F1F")


def settings_tabs(app: QApplication) -> None:
    dialog = SettingsDialog(Settings(lid_mode=True, notify=True, ignored=("utm.exe", "agent:aider")))
    dialog.resize(620, 470)
    for index, name in enumerate(["general", "battery", "ignored", "activity"]):
        dialog.show_tab(index)
        dialog.update_activity(SAMPLE, BUSY)
        app.processEvents()
        dialog.grab().save(str(OUT / f"settings-{name}.png"))
    dialog.close()


def tray_menu(app: QApplication) -> None:
    menu = QMenu()
    menu.addAction("Keeping your PC awake for Claude Code, HandBrake.exe and 1 more").setEnabled(False)
    menu.addSeparator()
    for text in ("Claude Code   42% CPU · 879 KB/s", "HandBrake.exe   380% CPU · 5.9 MB/s",
                 "Browser download   0% CPU · 0 KB/s"):
        menu.addMenu(text)
    menu.addSeparator()
    manual = menu.addAction("Keep awake until I turn this off")
    manual.setCheckable(True)
    menu.addAction("Pause for 1 hour")
    enabled = menu.addAction("Enabled\tCtrl+Alt+H")
    enabled.setCheckable(True)
    enabled.setChecked(True)
    menu.addSeparator()
    menu.addAction("Activity…")
    menu.addAction("Settings…")
    menu.addSeparator()
    menu.addAction("Quit WakeHold")
    menu.ensurePolished()
    menu.adjustSize()
    menu.show()
    app.processEvents()
    menu.grab().save(str(OUT / "tray-menu.png"))
    menu.close()


def icon_states() -> None:
    cell, label_h = 120, 26
    states = [("busy", "Holding awake"), ("idle", "Can sleep"), ("paused", "Paused"), ("off", "Off")]
    sheet = QPixmap(cell * len(states), cell + label_h)
    sheet.fill(BG)
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#CCCCCC"))
    font = QFont("Segoe UI", 9)
    painter.setFont(font)
    for i, (state, label) in enumerate(states):
        painter.drawPixmap(i * cell + 28, 12, make_icon(state, 3).pixmap(64, 64))
        painter.drawText(QRectF(i * cell, cell - 30, cell, label_h + 30),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, label)
    painter.drawPixmap(0, 0, QPixmap())
    painter.end()
    sheet.save(str(OUT / "tray-icon-states.png"))
    app_pixmap(256).save(str(OUT / "app-icon.png"))


def to_pil(pixmap: QPixmap) -> Image.Image:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    from io import BytesIO
    return Image.open(BytesIO(bytes(buffer.data()))).convert("RGBA")


def steam_gif() -> None:
    frames = []
    for f in range(STEAM_FRAMES):
        canvas = QPixmap(160, 160)
        canvas.fill(BG)
        painter = QPainter(canvas)
        painter.drawPixmap(16, 16, make_icon("busy", f).pixmap(64, 64).scaled(
            128, 128, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        painter.end()
        frames.append(to_pil(canvas).convert("P", palette=Image.Palette.ADAPTIVE, colors=128))
    frames[0].save(OUT / "steam.gif", save_all=True, append_images=frames[1:], duration=140, loop=0,
                   disposal=1)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    settings_tabs(app)
    tray_menu(app)
    icon_states()
    steam_gif()
    for path in sorted(OUT.iterdir()):
        print(path.name, path.stat().st_size)


if __name__ == "__main__":
    main()
