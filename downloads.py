"""Detects in-progress browser downloads from their partial files.

Browsers write to their cache constantly, so their I/O counters cannot tell
a download from normal browsing. A growing .crdownload / .part file can.
"""
from __future__ import annotations

import logging
import os
import time
import winreg
from pathlib import Path

from activity import APP_KIND, AppReading

log = logging.getLogger(__name__)

DOWNLOAD_KEY = "browser-downloads"
DOWNLOAD_LABEL = "Browser download"
PARTIAL_SUFFIXES = (".crdownload", ".part", ".partial", ".download", ".opdownload")
FRESH_SECONDS = 20.0
DOWNLOADS_GUID = "{374DE290-123F-4565-9164-39C4925E467B}"
SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"


def downloads_dir() -> Path:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SHELL_FOLDERS) as key:
            return Path(os.path.expandvars(winreg.QueryValueEx(key, DOWNLOADS_GUID)[0]))
    except OSError:
        return Path.home() / "Downloads"


def active_downloads(folder: Path, now: float | None = None, fresh_s: float = FRESH_SECONDS) -> list[str]:
    """Names of partial download files modified within the last `fresh_s` seconds."""
    now = time.time() if now is None else now
    found = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if not entry.name.lower().endswith(PARTIAL_SUFFIXES):
                    continue
                try:
                    if entry.is_file() and now - entry.stat().st_mtime <= fresh_s:
                        found.append(entry.name)
                except OSError:
                    continue
    except OSError as exc:
        log.debug("Downloads folder unreadable: %s", exc)
    return found


def download_reading(folder: Path) -> AppReading | None:
    names = active_downloads(folder)
    if not names:
        return None
    return AppReading(DOWNLOAD_KEY, DOWNLOAD_LABEL, APP_KIND, 0.0, 0.0, len(names))
