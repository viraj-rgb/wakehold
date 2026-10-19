"""User settings: an immutable dataclass persisted as JSON."""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

log = logging.getLogger(__name__)

GRACE_CHOICES = (30, 60, 120, 300, 600)


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    grace_seconds: int = 60
    keep_screen_on: bool = False
    lid_mode: bool = False
    only_when_plugged_in: bool = False
    min_battery_percent: int = 20
    respect_battery_saver: bool = True
    notify: bool = False
    start_at_login: bool = False
    ignored: tuple[str, ...] = ()
    cpu_threshold: float = 15.0
    io_threshold_kbps: float = 256.0

    def with_ignored(self, key: str) -> "Settings":
        if key in self.ignored:
            return self
        return replace(self, ignored=tuple(sorted({*self.ignored, key})))

    def without_ignored(self, key: str) -> "Settings":
        return replace(self, ignored=tuple(k for k in self.ignored if k != key))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def validate(raw: dict) -> Settings:
    """Build Settings from untrusted JSON, dropping unknown or ill-typed fields."""
    defaults = Settings()
    values = {}
    for f in fields(Settings):
        if f.name not in raw:
            continue
        value, default = raw[f.name], getattr(defaults, f.name)
        if isinstance(default, bool):
            if isinstance(value, bool):
                values[f.name] = value
        elif isinstance(default, tuple):
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                values[f.name] = tuple(sorted({v.strip().lower() for v in value if v.strip()}))
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            values[f.name] = type(default)(value)
    settings = replace(defaults, **values)
    grace = min(GRACE_CHOICES, key=lambda g: abs(g - settings.grace_seconds))
    return replace(
        settings,
        grace_seconds=grace,
        min_battery_percent=int(_clamp(settings.min_battery_percent, 0, 100)),
        cpu_threshold=_clamp(settings.cpu_threshold, 1.0, 400.0),
        io_threshold_kbps=_clamp(settings.io_threshold_kbps, 1.0, 1_000_000.0),
    )


def load(path: Path) -> Settings:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError) as exc:
        log.warning("Settings unreadable (%s); using defaults", exc)
        return Settings()
    if not isinstance(raw, dict):
        log.warning("Settings file is not an object; using defaults")
        return Settings()
    try:
        return validate(raw)
    except (ValueError, OverflowError) as exc:
        log.warning("Settings invalid (%s); using defaults", exc)
        return Settings()


def save(settings: Settings, path: Path) -> None:
    data = asdict(settings)
    data["ignored"] = list(settings.ignored)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)
