"""Turns noisy per-sample readings into a stable set of busy apps."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from activity import AGENT_KIND, BROWSER_APPS, AppReading
from downloads import DOWNLOAD_KEY

DEFAULT_WINDOW = 6          # samples considered (5 s apart: ~30 s)
DEFAULT_REQUIRED = 4        # active samples needed in the window
DEFAULT_AGENT_HOLD_S = 120  # thinking pauses an agent may take


@dataclass(frozen=True)
class BusyApp:
    key: str
    label: str
    kind: str
    cpu_percent: float
    io_bytes_per_s: float


class BusyTracker:
    def __init__(
        self,
        window: int = DEFAULT_WINDOW,
        required: int = DEFAULT_REQUIRED,
        agent_hold_s: float = DEFAULT_AGENT_HOLD_S,
    ) -> None:
        if not 1 <= required <= window:
            raise ValueError("required must be between 1 and window")
        self._window = window
        self._required = required
        self._agent_required = max(1, required // 2)
        self._agent_hold_s = agent_hold_s
        self._history: dict[str, deque[bool]] = {}
        self._agent_last_busy: dict[str, float] = {}

    def update(
        self,
        readings: list[AppReading],
        cpu_threshold: float,
        io_threshold_bytes: float,
        now: float,
    ) -> list[BusyApp]:
        present = {r.key: r for r in readings}
        for key, reading in present.items():
            active = _is_active(reading, cpu_threshold, io_threshold_bytes)
            self._history.setdefault(key, deque(maxlen=self._window)).append(active)
        for key in list(self._history):
            if key not in present:
                self._history[key].append(False)
                if not any(self._history[key]):
                    del self._history[key]
        self._agent_last_busy = {
            k: t for k, t in self._agent_last_busy.items() if k in present
        }

        busy = []
        for key, reading in present.items():
            if self._is_busy(reading, now):
                busy.append(BusyApp(key, reading.label, reading.kind,
                                    reading.cpu_percent, reading.io_bytes_per_s))
        return sorted(busy, key=lambda b: (b.kind != AGENT_KIND, -b.cpu_percent, b.label.lower()))

    def _is_busy(self, reading: AppReading, now: float) -> bool:
        active = sum(self._history.get(reading.key, ()))
        if reading.kind != AGENT_KIND:
            return active >= self._required
        if active >= self._agent_required:
            self._agent_last_busy[reading.key] = now
            return True
        last = self._agent_last_busy.get(reading.key)
        return last is not None and now - last <= self._agent_hold_s

    def forget(self, key: str) -> None:
        self._history.pop(key, None)
        self._agent_last_busy.pop(key, None)


def _is_active(reading: AppReading, cpu_threshold: float, io_threshold_bytes: float) -> bool:
    if reading.key == DOWNLOAD_KEY:
        return True
    if reading.key in BROWSER_APPS:
        return False
    return reading.cpu_percent >= cpu_threshold or reading.io_bytes_per_s >= io_threshold_bytes
