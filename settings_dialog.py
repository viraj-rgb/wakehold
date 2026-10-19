"""Settings window: General, Battery, Ignored Apps and a live Activity view."""
from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from activity import AGENT_KIND, AppReading
from config import GRACE_CHOICES, Settings
from winevents import HOTKEY_TEXT


def grace_text(seconds: int) -> str:
    return f"{seconds} seconds" if seconds < 60 else f"{seconds // 60} minute{'s' if seconds >= 120 else ''}"


def rate_text(bytes_per_s: float) -> str:
    if bytes_per_s >= 1024 * 1024:
        return f"{bytes_per_s / (1024 * 1024):.1f} MB/s"
    return f"{bytes_per_s / 1024:.0f} KB/s"


def kind_text(key: str) -> str:
    return "AI agent" if key.startswith("agent:") else "App"


class SettingsDialog(QDialog):
    settings_changed = pyqtSignal(object)  # Settings

    ACTIVITY_TAB = 3

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.setWindowTitle("WakeHold Settings")
        self.setMinimumSize(560, 460)
        self._settings = settings
        self._loading = False
        self._busy_keys: set[str] = set()
        self._readings: list[AppReading] = []

        self._tabs = QTabWidget()
        self._tabs.addTab(self._general_tab(), "General")
        self._tabs.addTab(self._battery_tab(), "Battery")
        self._tabs.addTab(self._ignored_tab(), "Ignored Apps")
        self._tabs.addTab(self._activity_tab(), "Activity")
        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)
        self._load(settings)

    # ---- building -------------------------------------------------------
    def _general_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self._enabled = QCheckBox(f"Keep my PC awake while apps are busy   ({HOTKEY_TEXT} toggles)")
        self._grace = QComboBox()
        for seconds in GRACE_CHOICES:
            self._grace.addItem(grace_text(seconds), seconds)
        self._screen = QCheckBox("Keep the screen on too")
        self._lid = QCheckBox("Keep working with the lid closed")
        lid_note = QLabel("While something is busy, the lid close action is set to “Do nothing”. "
                          "It is put back as soon as the work ends; a watchdog restores it if "
                          "WakeHold quits unexpectedly. When work finishes with the lid shut, the PC sleeps.")
        lid_note.setWordWrap(True)
        lid_note.setStyleSheet("color: gray;")
        self._notify = QCheckBox("Show a notification when WakeHold starts holding")
        self._login = QCheckBox("Start WakeHold when I sign in")
        form.addRow(self._enabled)
        form.addRow("Wait after work stops:", self._grace)
        form.addRow(self._screen)
        form.addRow(self._lid)
        form.addRow(lid_note)
        form.addRow(self._notify)
        form.addRow(self._login)
        for box in (self._enabled, self._screen, self._lid, self._notify, self._login):
            box.toggled.connect(self._emit)
        self._grace.currentIndexChanged.connect(self._emit)
        return page

    def _battery_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self._plugged = QCheckBox("Only keep awake when plugged in")
        self._min_battery = QSpinBox()
        self._min_battery.setRange(0, 100)
        self._min_battery.setSuffix(" %")
        self._saver = QCheckBox("Stop while Battery saver is on")
        form.addRow(self._plugged)
        form.addRow("Stop below battery level:", self._min_battery)
        form.addRow(self._saver)
        self._plugged.toggled.connect(self._emit)
        self._saver.toggled.connect(self._emit)
        self._min_battery.valueChanged.connect(self._emit)
        return page

    def _ignored_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("WakeHold stops watching these apps entirely."))
        self._ignored = QListWidget()
        self._ignored.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._ignored)
        row = QHBoxLayout()
        add, remove = QPushButton("+ Add an app…"), QPushButton("Remove")
        add.clicked.connect(self._add_ignored)
        remove.clicked.connect(self._remove_ignored)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        layout.addLayout(row)
        return page

    def _activity_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Live per-app activity. Bold rows are holding your PC awake."))
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["App", "Kind", "CPU", "Disk + network", "Processes"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)
        ignore = QPushButton("Ignore selected")
        ignore.clicked.connect(self._ignore_selected_activity)
        layout.addWidget(ignore, alignment=Qt.AlignmentFlag.AlignLeft)
        return page

    # ---- state ----------------------------------------------------------
    def _load(self, settings: Settings) -> None:
        self._loading = True
        self._settings = settings
        self._enabled.setChecked(settings.enabled)
        self._grace.setCurrentIndex(max(0, self._grace.findData(settings.grace_seconds)))
        self._screen.setChecked(settings.keep_screen_on)
        self._lid.setChecked(settings.lid_mode)
        self._notify.setChecked(settings.notify)
        self._login.setChecked(settings.start_at_login)
        self._plugged.setChecked(settings.only_when_plugged_in)
        self._min_battery.setValue(settings.min_battery_percent)
        self._saver.setChecked(settings.respect_battery_saver)
        self._ignored.clear()
        for key in settings.ignored:
            item = QListWidgetItem(f"{key.removeprefix('agent:')}   ·   {kind_text(key)}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._ignored.addItem(item)
        self._loading = False

    def set_settings(self, settings: Settings) -> None:
        if settings != self._settings:
            self._load(settings)

    def _emit(self) -> None:
        if self._loading:
            return
        updated = replace(
            self._settings,
            enabled=self._enabled.isChecked(),
            grace_seconds=int(self._grace.currentData()),
            keep_screen_on=self._screen.isChecked(),
            lid_mode=self._lid.isChecked(),
            notify=self._notify.isChecked(),
            start_at_login=self._login.isChecked(),
            only_when_plugged_in=self._plugged.isChecked(),
            min_battery_percent=self._min_battery.value(),
            respect_battery_saver=self._saver.isChecked(),
        )
        self._settings = updated
        self.settings_changed.emit(updated)

    def _apply(self, settings: Settings) -> None:
        self._load(settings)
        self.settings_changed.emit(settings)

    def _add_ignored(self) -> None:
        running = sorted({r.key for r in self._readings} - set(self._settings.ignored))
        key, ok = QInputDialog.getItem(self, "Ignore an app",
                                       "Pick a running app or type an exe name (e.g. vmware.exe):",
                                       running, 0, True)
        key = key.strip().lower()
        if ok and key:
            self._apply(self._settings.with_ignored(key))

    def _remove_ignored(self) -> None:
        settings = self._settings
        for item in self._ignored.selectedItems():
            settings = settings.without_ignored(item.data(Qt.ItemDataRole.UserRole))
        self._apply(settings)

    def _ignore_selected_activity(self) -> None:
        settings = self._settings
        for index in self._table.selectionModel().selectedRows():
            item = self._table.item(index.row(), 0)
            settings = settings.with_ignored(item.data(Qt.ItemDataRole.UserRole))
        self._apply(settings)

    # ---- live activity --------------------------------------------------
    def show_tab(self, index: int) -> None:
        self._tabs.setCurrentIndex(index)
        self.show()
        self.raise_()
        self.activateWindow()

    def update_activity(self, readings: list[AppReading], busy_keys: set[str]) -> None:
        self._readings = readings
        self._busy_keys = busy_keys
        if not self.isVisible() or self._tabs.currentIndex() != self.ACTIVITY_TAB:
            return
        rows = sorted(readings, key=lambda r: (r.key not in busy_keys, -(r.cpu_percent + r.io_bytes_per_s / 65536)))
        rows = [r for r in rows if r.kind == AGENT_KIND or r.cpu_percent >= 0.5 or r.io_bytes_per_s >= 4096][:60]
        self._table.setRowCount(len(rows))
        for row, reading in enumerate(rows):
            cells = [reading.label, "AI agent" if reading.kind == AGENT_KIND else "App",
                     f"{reading.cpu_percent:.0f}%", rate_text(reading.io_bytes_per_s),
                     str(reading.process_count)]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, reading.key)
                if reading.key in busy_keys:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self._table.setItem(row, col, item)
