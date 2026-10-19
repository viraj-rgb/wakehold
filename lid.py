"""Lid-closed mode: set the lid close action to "Do nothing" only while work runs.

The original setting is written to disk *before* it is changed, so the
watchdog process (or the next launch) can put it back after a crash.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

SUB_BUTTONS = "4f971e89-eebd-4455-a8de-9e59040e7347"
LIDACTION = "5ca83367-6e45-459f-a27b-476b1d01c936"
DO_NOTHING = 0
SLEEP = 1  # Windows' default lid action
POWERCFG_TIMEOUT_S = 10

_GUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
_HEX = re.compile(r"0x([0-9a-fA-F]{8})")


class LidError(RuntimeError):
    pass


@dataclass(frozen=True)
class LidSetting:
    scheme: str
    ac: int
    dc: int


def _powercfg(*args: str) -> str:
    try:
        done = subprocess.run(
            ["powercfg", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=POWERCFG_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LidError(f"powercfg failed: {exc}") from exc
    if done.returncode != 0:
        raise LidError(f"powercfg {' '.join(args)} failed: {done.stdout.strip() or done.stderr.strip()}")
    return done.stdout


def parse_lid_query(output: str) -> LidSetting:
    """Parse `powercfg /qh SCHEME_CURRENT SUB_BUTTONS LIDACTION` (any UI language)."""
    guids = _GUID.findall(output)
    values = _HEX.findall(output)
    if not guids or len(values) < 2:
        raise LidError("Could not read the lid close action from powercfg")
    return LidSetting(scheme=guids[0].lower(), ac=int(values[-2], 16), dc=int(values[-1], 16))


def read_lid_setting() -> LidSetting:
    return parse_lid_query(_powercfg("/qh", "SCHEME_CURRENT", SUB_BUTTONS, LIDACTION))


def write_lid_setting(setting: LidSetting) -> None:
    _powercfg("/setacvalueindex", setting.scheme, SUB_BUTTONS, LIDACTION, str(setting.ac))
    _powercfg("/setdcvalueindex", setting.scheme, SUB_BUTTONS, LIDACTION, str(setting.dc))
    _powercfg("/setactive", "SCHEME_CURRENT")


class LidGuard:
    def __init__(
        self,
        state_path: Path,
        reader: Callable[[], LidSetting] = read_lid_setting,
        writer: Callable[[LidSetting], None] = write_lid_setting,
    ) -> None:
        self._state_path = state_path
        self._reader = reader
        self._writer = writer
        self._original: LidSetting | None = None

    @property
    def overridden(self) -> bool:
        return self._original is not None

    def override(self) -> None:
        if self._original is not None:
            return
        # A leftover marker means the live value may already be "Do nothing";
        # saving that as the original would make the override permanent.
        self.recover_stale()
        original = self._reader()
        self._save(original)
        self._original = original
        self._writer(LidSetting(original.scheme, DO_NOTHING, DO_NOTHING))
        log.info("Lid close action set to Do nothing (was AC=%d DC=%d)", original.ac, original.dc)

    def restore(self) -> None:
        if self._original is None:
            return
        self._writer(self._original)
        log.info("Lid close action restored (AC=%d DC=%d)", self._original.ac, self._original.dc)
        self._original = None
        self._clear()

    def recover_stale(self) -> bool:
        """Restore a setting left behind by a run that ended without cleaning up."""
        if not self._state_path.exists():
            return False
        saved = self._load()
        if saved is None:
            saved = self._fallback()
            if saved is None:
                return False
        self._writer(saved)
        self._clear()
        log.warning("Recovered lid close action left by a previous run (AC=%d DC=%d)", saved.ac, saved.dc)
        return True

    def _fallback(self) -> LidSetting | None:
        """The marker was unreadable: if the lid is still on "Do nothing", go back to Sleep."""
        current = self._reader()
        self._clear()
        if (current.ac, current.dc) != (DO_NOTHING, DO_NOTHING):
            return None
        log.warning("Original lid action unknown; falling back to Sleep")
        return LidSetting(current.scheme, SLEEP, SLEEP)

    def _save(self, setting: LidSetting) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"scheme": setting.scheme, "ac": setting.ac, "dc": setting.dc}),
                           encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError as exc:
            raise LidError(f"Could not save the lid state file: {exc}") from exc

    def _load(self) -> LidSetting | None:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            setting = LidSetting(str(raw["scheme"]), int(raw["ac"]), int(raw["dc"]))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.error("Lid state file unreadable (%s)", exc)
            return None
        if not _GUID.fullmatch(setting.scheme) or not all(0 <= v <= 3 for v in (setting.ac, setting.dc)):
            log.error("Lid state file has invalid values")
            return None
        return setting

    def _clear(self) -> None:
        try:
            self._state_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise LidError(f"Could not remove the lid state file: {exc}") from exc
