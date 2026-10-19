"""Native Windows messages: the global hotkey and lid open/close notifications."""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal
from PyQt6.QtWidgets import QWidget

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 0x5748  # "WH"
HOTKEY_VK = ord("H")
HOTKEY_TEXT = "Ctrl+Alt+H"
DEVICE_NOTIFY_WINDOW_HANDLE = 0
SETTING_DATA_OFFSET = 20  # sizeof(GUID) + sizeof(DWORD) in POWERBROADCAST_SETTING


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "GUID":
        hexes = text.replace("-", "")
        tail = bytes.fromhex(hexes[16:])
        return cls(int(hexes[0:8], 16), int(hexes[8:12], 16), int(hexes[12:16], 16),
                   (ctypes.c_ubyte * 8)(*tail))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, GUID) and bytes(self) == bytes(other)


GUID_LIDSWITCH_STATE_CHANGE = GUID.parse("ba3e0f4d-b817-4094-a2d1-d56379e6a0f3")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
_user32.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), wintypes.DWORD]
_user32.UnregisterPowerSettingNotification.argtypes = [wintypes.HANDLE]
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]


class WindowsEvents(QObject, QAbstractNativeEventFilter):
    hotkey_pressed = pyqtSignal()
    lid_changed = pyqtSignal(bool)  # True when the lid is closed

    def __init__(self) -> None:
        QObject.__init__(self)
        QAbstractNativeEventFilter.__init__(self)
        self._window = QWidget()
        self._hwnd = int(self._window.winId())
        self._power_handle = None
        self._hotkey = False
        self.lid_closed = False

    def start(self) -> None:
        self._power_handle = _user32.RegisterPowerSettingNotification(
            self._hwnd, ctypes.byref(GUID_LIDSWITCH_STATE_CHANGE), DEVICE_NOTIFY_WINDOW_HANDLE)
        if not self._power_handle:
            log.warning("Lid notifications unavailable (error %d)", ctypes.get_last_error())
        self._hotkey = bool(_user32.RegisterHotKey(
            self._hwnd, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, HOTKEY_VK))
        if not self._hotkey:
            log.warning("%s is taken by another app (error %d)", HOTKEY_TEXT, ctypes.get_last_error())

    @property
    def hotkey_registered(self) -> bool:
        return self._hotkey

    def stop(self) -> None:
        if self._power_handle:
            _user32.UnregisterPowerSettingNotification(self._power_handle)
            self._power_handle = None
        if self._hotkey:
            _user32.UnregisterHotKey(self._hwnd, HOTKEY_ID)
            self._hotkey = False

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt override)
        if event_type != b"windows_generic_MSG":
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
            self.hotkey_pressed.emit()
        elif msg.message == WM_POWERBROADCAST and msg.wParam == PBT_POWERSETTINGCHANGE and msg.lParam:
            setting = GUID.from_address(msg.lParam)
            if setting == GUID_LIDSWITCH_STATE_CHANGE:
                state = wintypes.DWORD.from_address(msg.lParam + SETTING_DATA_OFFSET).value
                closed = state == 0
                if closed != self.lid_closed:
                    self.lid_closed = closed
                    log.info("Lid %s", "closed" if closed else "opened")
                    self.lid_changed.emit(closed)
        return False, 0
