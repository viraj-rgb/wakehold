"""WakeHold: keeps your PC awake while apps are busy, then lets it sleep.

Usage:
    python app.py                      run the tray app
    python app.py --watchdog PID CT    (internal) lid-setting watchdog
"""
from __future__ import annotations

import logging
import sys
import time
from logging.handlers import RotatingFileHandler

import paths

SAMPLE_INTERVAL_MS = 5000
PAUSE_SECONDS = 3600


def setup_logging(watchdog: bool) -> None:
    target = paths.log_path().with_name("watchdog.log") if watchdog else paths.log_path()
    handler = RotatingFileHandler(target, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run_watchdog(argv: list[str]) -> int:
    import lidwatch
    try:
        pid, create_time = int(argv[0]), float(argv[1])
    except (IndexError, ValueError):
        logging.error("Bad watchdog arguments: %r", argv)
        return 2
    lidwatch.run(pid, create_time, paths.lid_state_path())
    return 0


def run_app() -> int:
    from PyQt6.QtCore import QLockFile
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from tray import TrayApp

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(paths.APP_NAME)
    lock = QLockFile(str(paths.lock_path()))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, paths.APP_NAME, "WakeHold is already running in the system tray.")
        return 0
    tray = TrayApp(app, sample_interval_ms=SAMPLE_INTERVAL_MS, pause_seconds=PAUSE_SECONDS)
    tray.start()
    code = app.exec()
    tray.shutdown()
    lock.unlock()
    return code


def main(argv: list[str]) -> int:
    is_watchdog = len(argv) > 1 and argv[1] == "--watchdog"
    setup_logging(is_watchdog)
    if is_watchdog:
        return run_watchdog(argv[2:])
    logging.info("WakeHold starting at %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    return run_app()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
