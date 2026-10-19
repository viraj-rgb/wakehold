"""Windows power calls: keep-awake requests, battery state, sleep now."""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Callable

import psutil

from controller import PowerSnapshot

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
BATTERY_SAVER_FLAG = 0x01


def _set_thread_execution_state(flags: int) -> int:
    return ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))


class KeepAwake:
    """Holds a system (and optionally display) wake request.

    SetThreadExecutionState is per-thread, so apply() must always be called
    from the same thread (the Qt GUI thread).
    """

    def __init__(self, setter: Callable[[int], int] = _set_thread_execution_state) -> None:
        self._setter = setter
        self._flags: int | None = None

    @property
    def holding(self) -> bool:
        return bool(self._flags and self._flags & ES_SYSTEM_REQUIRED)

    def apply(self, hold: bool, keep_display: bool) -> None:
        flags = ES_CONTINUOUS
        if hold:
            flags |= ES_SYSTEM_REQUIRED
            if keep_display:
                flags |= ES_DISPLAY_REQUIRED
        if flags == self._flags:
            return
        if self._setter(flags) == 0:
            log.error("SetThreadExecutionState(%#x) failed", flags)
            return
        self._flags = flags
        log.info("Wake request %s", "held" if hold else "released")

    def release(self) -> None:
        self.apply(False, False)


class _SystemPowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


def _battery_saver_on() -> bool:
    status = _SystemPowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return False
    return bool(status.SystemStatusFlag & BATTERY_SAVER_FLAG)


def power_snapshot() -> PowerSnapshot:
    try:
        battery = psutil.sensors_battery()
    except (psutil.Error, OSError) as exc:
        log.warning("Battery state unavailable: %s", exc)
        battery = None
    if battery is None:
        return PowerSnapshot(on_battery=False, percent=None, battery_saver=False)
    plugged = battery.power_plugged is not False
    return PowerSnapshot(
        on_battery=not plugged,
        percent=int(battery.percent),
        battery_saver=_battery_saver_on(),
    )


def sleep_now() -> bool:
    """Put the PC to sleep (hibernates instead if the system is set up that way)."""
    ok = bool(ctypes.windll.powrprof.SetSuspendState(False, False, False))
    if not ok:
        log.error("SetSuspendState failed (error %d)", ctypes.get_last_error())
    return ok
