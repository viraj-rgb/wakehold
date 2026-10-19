"""Companion process that restores the lid setting if WakeHold dies.

Started once per launch as `WakeHold.exe --watchdog <pid> <create_time>`.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

import psutil

from lid import LidError, LidGuard

log = logging.getLogger(__name__)

POLL_SECONDS = 2.0
RESTORE_ATTEMPTS = 30
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def parent_alive(pid: int, create_time: float) -> bool:
    try:
        return abs(psutil.Process(pid).create_time() - create_time) < 1.0
    except psutil.Error:
        return False


def run(pid: int, create_time: float, state_path: Path, poll: float = POLL_SECONDS) -> None:
    log.info("Watchdog watching pid %d", pid)
    while parent_alive(pid, create_time):
        time.sleep(poll)
    guard = LidGuard(state_path)
    for attempt in range(1, RESTORE_ATTEMPTS + 1):
        try:
            if guard.recover_stale():
                log.warning("WakeHold exited without restoring the lid setting; watchdog restored it")
            return
        except (LidError, OSError) as exc:
            log.error("Restore attempt %d failed: %s", attempt, exc)
            time.sleep(poll)
    log.error("Watchdog gave up; the next WakeHold launch will retry")


def launch_command(pid: int, create_time: float) -> list[str]:
    if getattr(sys, "frozen", False):
        base = [sys.executable]
    else:
        base = [sys.executable, str(Path(__file__).with_name("app.py"))]
    return [*base, "--watchdog", str(pid), repr(create_time)]


def spawn() -> subprocess.Popen | None:
    me = psutil.Process()
    command = launch_command(me.pid, me.create_time())
    base_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    for flags in (base_flags | CREATE_BREAKAWAY_FROM_JOB, base_flags):
        try:
            return subprocess.Popen(command, creationflags=flags, close_fds=True,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError as exc:
            log.warning("Watchdog launch with flags %#x failed: %s", flags, exc)
    log.error("Watchdog could not be started; lid mode is unprotected")
    return None
