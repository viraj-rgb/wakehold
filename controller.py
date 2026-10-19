"""Decides whether to hold the PC awake. Pure logic, no OS calls."""
from __future__ import annotations

import time
from dataclasses import dataclass

from config import Settings


@dataclass(frozen=True)
class PowerSnapshot:
    on_battery: bool
    percent: int | None
    battery_saver: bool


AC_POWER = PowerSnapshot(on_battery=False, percent=None, battery_saver=False)


@dataclass(frozen=True)
class Decision:
    hold: bool
    state: str   # off | paused | manual | battery | busy | grace | idle
    status: str


def _join(labels: list[str]) -> str:
    if len(labels) <= 2:
        return " and ".join(labels)
    return f"{labels[0]}, {labels[1]} and {len(labels) - 2} more"


def battery_block(settings: Settings, power: PowerSnapshot) -> str | None:
    if not power.on_battery:
        return None
    if settings.only_when_plugged_in:
        return "Battery guard: only works when plugged in"
    if settings.respect_battery_saver and power.battery_saver:
        return "Battery guard: Battery saver is on"
    if power.percent is not None and power.percent < settings.min_battery_percent:
        return f"Battery guard: below {settings.min_battery_percent}%"
    return None


class HoldController:
    def __init__(self) -> None:
        self._last_busy_at: float | None = None
        self._paused_until: float | None = None
        self._manual = False

    @property
    def manual(self) -> bool:
        return self._manual

    @property
    def paused_until(self) -> float | None:
        return self._paused_until

    def set_manual(self, on: bool) -> None:
        self._manual = on

    def pause(self, until: float) -> None:
        self._paused_until = until

    def resume(self) -> None:
        self._paused_until = None

    def decide(
        self, settings: Settings, busy_labels: list[str], power: PowerSnapshot, now: float
    ) -> Decision:
        if busy_labels:
            self._last_busy_at = now
        if not settings.enabled:
            return Decision(False, "off", "WakeHold is off")
        if self._paused_until is not None:
            if now < self._paused_until:
                until = time.strftime("%H:%M", time.localtime(self._paused_until))
                return Decision(False, "paused", f"Paused until {until}")
            self._paused_until = None
        if self._manual:
            return Decision(True, "manual", "Kept awake on request")
        blocked = battery_block(settings, power)
        if blocked:
            return Decision(False, "battery", blocked)
        if busy_labels:
            return Decision(True, "busy", f"Keeping your PC awake for {_join(busy_labels)}")
        if self._last_busy_at is not None:
            left = settings.grace_seconds - (now - self._last_busy_at)
            if left > 0:
                return Decision(True, "grace", f"Work finished · sleep allowed in {int(left + 0.999)}s")
        return Decision(False, "idle", "Your PC can sleep")
