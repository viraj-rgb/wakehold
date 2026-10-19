"""Locations of WakeHold's per-user files."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "WakeHold"


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_dir() / "settings.json"


def lid_state_path() -> Path:
    return app_dir() / "lid_state.json"


def log_path() -> Path:
    return app_dir() / "wakehold.log"


def lock_path() -> Path:
    return app_dir() / "wakehold.lock"
