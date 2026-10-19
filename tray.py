"""The tray icon, its menu, and the loop that ties sampling to power control."""
from __future__ import annotations

import logging
import time
from dataclasses import replace

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import config
import paths
import power
import startup
import lidwatch
from activity import ActivitySampler, AppReading
from controller import Decision, HoldController
from downloads import download_reading, downloads_dir
from icons import ANIMATED_STATES, STEAM_FRAMES, app_icon, make_icon
from lid import LidError, LidGuard
from settings_dialog import SettingsDialog, rate_text
from tracker import BusyApp, BusyTracker
from winevents import HOTKEY_TEXT, WindowsEvents

log = logging.getLogger(__name__)

MAX_MENU_APPS = 8
STEAM_FRAME_MS = 140
LID_RETRY_SECONDS = 60


class SamplerWorker(QObject):
    """Runs psutil sampling off the GUI thread."""
    sampled = pyqtSignal(list)

    def __init__(self, interval_ms: int) -> None:
        super().__init__()
        self._interval_ms = interval_ms
        self._sampler = ActivitySampler()
        self._downloads = downloads_dir()
        self._timer: QTimer | None = None

    @pyqtSlot()
    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._interval_ms)
        self._tick()

    @pyqtSlot(int)
    def exclude_pid(self, pid: int) -> None:
        self._sampler.exclude_pid(pid)

    @pyqtSlot()
    def stop(self) -> None:
        if self._timer:
            self._timer.stop()

    def _tick(self) -> None:
        try:
            readings = self._sampler.sample()
            download = download_reading(self._downloads)
            self.sampled.emit(readings + [download] if download else readings)
        except Exception:  # keep sampling alive; one bad pass must not stop the app
            log.exception("Sampling failed")


class TrayApp(QObject):
    exclude_pid = pyqtSignal(int)
    stop_worker = pyqtSignal()

    def __init__(self, app: QApplication, sample_interval_ms: int, pause_seconds: int) -> None:
        super().__init__()
        self._app = app
        self._pause_seconds = pause_seconds
        self._settings = config.load(paths.settings_path())
        self._tracker = BusyTracker()
        self._controller = HoldController()
        self._keep_awake = power.KeepAwake()
        self._lid = LidGuard(paths.lid_state_path())
        self._lid_failed = False
        self._lid_retry_at = 0.0
        self._decision = Decision(False, "idle", "Starting…")
        self._busy: list[BusyApp] = []
        self._readings: list[AppReading] = []
        self._watchdog = None
        self._stopped = False

        self._events = WindowsEvents()
        self._events.hotkey_pressed.connect(self._toggle_enabled)
        self._events.lid_changed.connect(lambda _closed: self._evaluate())

        self._tray = QSystemTrayIcon(make_icon("idle"))
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._build_menu)
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)
        self._dialog: SettingsDialog | None = None
        self._app.setWindowIcon(app_icon())
        self._frame = 0
        self._icon_cache: dict[tuple[str, int], object] = {}
        self._icon_state = ""
        self._steam = QTimer(self)
        self._steam.setInterval(STEAM_FRAME_MS)
        self._steam.timeout.connect(self._animate)

        self._thread = QThread()
        self._worker = SamplerWorker(sample_interval_ms)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._worker.sampled.connect(self._on_sampled)
        self.exclude_pid.connect(self._worker.exclude_pid)
        self.stop_worker.connect(self._worker.stop)

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        try:
            self._lid.recover_stale()
        except (LidError, OSError) as exc:
            log.error("Could not recover lid setting: %s", exc)
        self._app.installNativeEventFilter(self._events)
        self._events.start()
        self._watchdog_alive()
        startup.set_start_at_login(self._settings.start_at_login)
        self._app.aboutToQuit.connect(self.shutdown)
        self._thread.start()
        self._refresh_tray()
        self._tray.show()

    def shutdown(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.stop_worker.emit()
        self._thread.quit()
        self._thread.wait(3000)
        self._keep_awake.release()
        self._restore_lid()
        self._steam.stop()
        self._events.stop()
        # If the lid could not be restored, leave the watchdog running to retry.
        if self._watchdog and self._watchdog.poll() is None and not self._lid.overridden:
            self._watchdog.terminate()
        self._tray.hide()
        log.info("WakeHold stopped")

    # ---- main loop ------------------------------------------------------
    @pyqtSlot(list)
    def _on_sampled(self, readings: list[AppReading]) -> None:
        ignored = set(self._settings.ignored)
        self._readings = [r for r in readings if r.key not in ignored]
        self._busy = self._tracker.update(
            self._readings, self._settings.cpu_threshold,
            self._settings.io_threshold_kbps * 1024, time.monotonic())
        self._evaluate()
        if self._dialog:
            self._dialog.update_activity(self._readings, {b.key for b in self._busy})

    def _evaluate(self) -> None:
        was = self._decision
        decision = self._controller.decide(
            self._settings, [b.label for b in self._busy], power.power_snapshot(), time.time())
        self._decision = decision
        self._keep_awake.apply(decision.hold, self._settings.keep_screen_on)
        self._apply_lid(decision)
        if decision.state != was.state:
            log.info("State %s -> %s: %s", was.state, decision.state, decision.status)
            if decision.state == "busy" and self._settings.notify:
                self._tray.showMessage(paths.APP_NAME, decision.status, make_icon("busy"), 4000)
        self._refresh_tray()

    def _watchdog_alive(self) -> bool:
        if self._watchdog is not None and self._watchdog.poll() is None:
            return True
        self._watchdog = lidwatch.spawn()
        if self._watchdog is None:
            return False
        self.exclude_pid.emit(self._watchdog.pid)
        return True

    def _apply_lid(self, decision: Decision) -> None:
        wants_override = decision.hold and self._settings.lid_mode and not self._lid_failed
        if wants_override:
            if not self._lid.overridden:
                self._override_lid()
            return
        if self._lid.overridden and time.monotonic() >= self._lid_retry_at:
            if self._restore_lid() and self._events.lid_closed and not decision.hold:
                log.info("Work done with the lid closed; sleeping now")
                power.sleep_now()

    def _override_lid(self) -> None:
        try:
            if not self._watchdog_alive():
                raise LidError("the crash-safety watchdog could not be started")
            self._lid.override()
        except (LidError, OSError) as exc:
            self._lid_failed = True
            log.error("Lid mode unavailable: %s", exc)
            self._tray.showMessage(paths.APP_NAME, f"Lid-closed mode is unavailable: {exc}",
                                   QSystemTrayIcon.MessageIcon.Warning, 6000)
            self._restore_lid()

    def _restore_lid(self) -> bool:
        try:
            self._lid.restore()
            return True
        except (LidError, OSError) as exc:
            self._lid_retry_at = time.monotonic() + LID_RETRY_SECONDS
            log.error("Could not restore lid setting (retrying in %ds): %s", LID_RETRY_SECONDS, exc)
            return False

    # ---- settings -------------------------------------------------------
    def _set_settings(self, settings: config.Settings) -> None:
        if settings == self._settings:
            return
        old = self._settings
        self._settings = settings
        for key in set(settings.ignored) - set(old.ignored):
            self._tracker.forget(key)
        if settings.lid_mode and not old.lid_mode:
            self._lid_failed = False
        if settings.start_at_login != old.start_at_login:
            startup.set_start_at_login(settings.start_at_login)
        try:
            config.save(settings, paths.settings_path())
        except OSError as exc:
            log.error("Could not save settings: %s", exc)
        if self._dialog:
            self._dialog.set_settings(settings)
        self._busy = [b for b in self._busy if b.key not in settings.ignored]
        self._evaluate()

    def _open_settings(self, tab: int = 0) -> None:
        if self._dialog is None:
            self._dialog = SettingsDialog(self._settings)
            self._dialog.settings_changed.connect(self._set_settings)
        self._dialog.update_activity(self._readings, {b.key for b in self._busy})
        self._dialog.show_tab(tab)
        self._dialog.update_activity(self._readings, {b.key for b in self._busy})

    # ---- actions --------------------------------------------------------
    def _toggle_enabled(self) -> None:
        enabled = not self._settings.enabled
        self._set_settings(replace(self._settings, enabled=enabled))
        self._tray.showMessage(paths.APP_NAME, "WakeHold is on" if enabled else "WakeHold is off",
                               make_icon("idle" if enabled else "off"), 2000)

    def _toggle_manual(self, on: bool) -> None:
        self._controller.set_manual(on)
        self._evaluate()

    def _toggle_pause(self) -> None:
        if self._controller.paused_until and self._controller.paused_until > time.time():
            self._controller.resume()
        else:
            self._controller.pause(time.time() + self._pause_seconds)
        self._evaluate()

    def _ignore(self, key: str) -> None:
        self._set_settings(self._settings.with_ignored(key))

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._menu.popup(self._tray.geometry().center())
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._open_settings(SettingsDialog.ACTIVITY_TAB)

    # ---- UI -------------------------------------------------------------
    def _icon(self, state: str, frame: int):
        key = (state, frame if state in ANIMATED_STATES else 0)
        if key not in self._icon_cache:
            self._icon_cache[key] = make_icon(state, frame)
        return self._icon_cache[key]

    def _animate(self) -> None:
        self._frame = (self._frame + 1) % STEAM_FRAMES
        self._tray.setIcon(self._icon(self._decision.state, self._frame))

    def _refresh_tray(self) -> None:
        state = self._decision.state
        if state != self._icon_state:
            self._icon_state = state
            self._icon_cache.clear()  # also picks up taskbar theme changes
            self._tray.setIcon(self._icon(state, self._frame))
        if state in ANIMATED_STATES:
            if not self._steam.isActive():
                self._steam.start()
        else:
            self._steam.stop()
        self._tray.setToolTip(f"{paths.APP_NAME}\n{self._decision.status}"[:127])

    def _build_menu(self) -> None:
        menu = self._menu
        menu.clear()
        status = menu.addAction(self._decision.status)
        status.setEnabled(False)
        if self._busy:
            menu.addSeparator()
            for app in self._busy[:MAX_MENU_APPS]:
                sub = menu.addMenu(f"{app.label}   {app.cpu_percent:.0f}% CPU · {rate_text(app.io_bytes_per_s)}")
                sub.addAction(f"Ignore {app.label}").triggered.connect(
                    lambda _=False, k=app.key: self._ignore(k))
        menu.addSeparator()
        manual = QAction("Keep awake until I turn this off", menu, checkable=True)
        manual.setChecked(self._controller.manual)
        manual.toggled.connect(self._toggle_manual)
        menu.addAction(manual)
        paused = self._controller.paused_until and self._controller.paused_until > time.time()
        menu.addAction("Resume" if paused else "Pause for 1 hour").triggered.connect(lambda _=False: self._toggle_pause())
        enabled = QAction(f"Enabled\t{HOTKEY_TEXT}", menu, checkable=True)
        enabled.setChecked(self._settings.enabled)
        enabled.triggered.connect(lambda _=False: self._toggle_enabled())
        menu.addAction(enabled)
        menu.addSeparator()
        menu.addAction("Activity…").triggered.connect(lambda: self._open_settings(SettingsDialog.ACTIVITY_TAB))
        menu.addAction("Settings…").triggered.connect(lambda: self._open_settings(0))
        menu.addSeparator()
        menu.addAction("Quit WakeHold").triggered.connect(lambda _=False: self._app.quit())
