"""Writes assets/wakehold.ico (PNG-compressed, 16-256 px) from icons.app_pixmap.

Usage: python tools/make_icon.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402

from icons import app_pixmap  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not app_pixmap(size).save(buffer, "PNG"):
        raise RuntimeError(f"Could not encode {size}px icon")
    return bytes(buffer.data())


def build_ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in ICO headers
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def main() -> None:
    app = QGuiApplication(sys.argv)  # noqa: F841 (QPixmap needs an application)
    out = ROOT / "assets" / "wakehold.ico"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(build_ico([(size, png_bytes(size)) for size in SIZES]))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
