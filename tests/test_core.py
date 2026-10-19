import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from activity import AGENT_KIND, APP_KIND, AppReading, _Snapshot, _walk_up, match_agent  # noqa: E402
from config import Settings  # noqa: E402
from controller import AC_POWER, HoldController, PowerSnapshot  # noqa: E402
from lid import LidError, LidGuard, LidSetting, parse_lid_query  # noqa: E402
from power import ES_CONTINUOUS, ES_DISPLAY_REQUIRED, ES_SYSTEM_REQUIRED, KeepAwake  # noqa: E402
from tracker import BusyTracker  # noqa: E402
from downloads import DOWNLOAD_KEY, active_downloads, download_reading  # noqa: E402

CPU, IO = 15.0, 256 * 1024


def reading(key, cpu=0.0, io=0.0, kind=APP_KIND):
    return AppReading(key, key, kind, cpu, io, 1)


# ---- config ---------------------------------------------------------------
def test_settings_roundtrip(tmp_path):
    path = tmp_path / "s.json"
    original = Settings(grace_seconds=300, lid_mode=True, ignored=("docker.exe",))
    config.save(original, path)
    assert config.load(path) == original


def test_load_rejects_bad_types_and_clamps(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"enabled": "yes", "grace_seconds": 45, "min_battery_percent": 400,
                                "ignored": ["  UTM.exe ", 3], "unknown": 1}))
    loaded = config.load(path)
    assert loaded.enabled is True            # bad type ignored, default kept
    assert loaded.grace_seconds in (30, 60)  # snapped to a choice
    assert loaded.min_battery_percent == 100
    assert loaded.ignored == ()              # list with non-strings rejected


def test_load_corrupt_file_returns_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert config.load(path) == Settings()


def test_ignore_helpers_do_not_mutate():
    base = Settings()
    added = base.with_ignored("vmware.exe")
    assert base.ignored == () and added.ignored == ("vmware.exe",)
    assert added.without_ignored("vmware.exe").ignored == ()


# ---- agent recognition ----------------------------------------------------
@pytest.mark.parametrize("name, exe, cmd, expected", [
    ("claude.exe", r"C:\Users\u\.local\bin\claude.exe", ["claude"], "claude"),
    ("claude.exe", r"C:\Users\u\AppData\Local\AnthropicClaude\app-1\claude.exe", [], None),
    ("claude.exe", r"C:\x\claude.exe", ["claude.exe", "--type=renderer"], None),
    ("node.exe", r"C:\node\node.exe", ["node", r"C:\npm\node_modules\@openai\codex\bin.js".replace("\\", "/")], "codex"),
    ("node.exe", r"C:\node\node.exe", ["node", "server.js"], None),
    ("aider.exe", None, None, "aider"),
    ("chrome.exe", None, None, None),
])
def test_match_agent(name, exe, cmd, expected):
    assert match_agent(name, exe, cmd) == expected


def test_descendants_attributed_to_agent():
    procs = {
        10: _Snapshot("claude.exe", 1, 100.0, 0, 0),
        11: _Snapshot("bash.exe", 10, 101.0, 0, 0),
        12: _Snapshot("npm.exe", 11, 102.0, 0, 0),
        20: _Snapshot("chrome.exe", 1, 50.0, 0, 0),
        # pid 30's "parent" 10 was created after it: a recycled pid, not a real parent
        30: _Snapshot("old.exe", 10, 90.0, 0, 0),
    }
    direct = {10: "claude"}
    assert _walk_up(12, procs, direct) == "claude"
    assert _walk_up(20, procs, direct) is None
    assert _walk_up(30, procs, direct) is None


# ---- busy tracking --------------------------------------------------------
def test_blip_is_ignored_sustained_counts():
    tracker = BusyTracker(window=6, required=4)
    assert tracker.update([reading("x", cpu=90)], CPU, IO, 0) == []
    for t in range(1, 3):
        assert tracker.update([reading("x", cpu=0)], CPU, IO, t) == []
    for t in range(3, 6):
        busy = tracker.update([reading("x", cpu=50)], CPU, IO, t)
    assert [b.key for b in busy] == ["x"]


def test_io_alone_counts_as_busy():
    tracker = BusyTracker(window=4, required=3)
    for t in range(3):
        busy = tracker.update([reading("dl", io=2 * 1024 * 1024)], CPU, IO, t)
    assert [b.key for b in busy] == ["dl"]


def test_agent_survives_thinking_pause_then_lets_go():
    tracker = BusyTracker(window=4, required=2, agent_hold_s=60)
    agent = lambda cpu: reading("agent:claude", cpu=cpu, kind=AGENT_KIND)  # noqa: E731
    assert tracker.update([agent(40)], CPU, IO, 0)       # agents need only required // 2
    for t in (5, 10, 15, 20, 50):
        assert tracker.update([agent(0)], CPU, IO, t), f"dropped at {t}"
    # last active sample left the window at t=15, so the hold ends after t=75
    assert tracker.update([agent(0)], CPU, IO, 80) == []


def test_agents_sort_first_and_vanished_apps_drop():
    tracker = BusyTracker(window=2, required=1)
    busy = tracker.update([reading("a.exe", cpu=99), reading("agent:codex", cpu=20, kind=AGENT_KIND)], CPU, IO, 0)
    assert [b.key for b in busy] == ["agent:codex", "a.exe"]
    assert tracker.update([], CPU, IO, 1) == []


def test_tracker_rejects_bad_window():
    with pytest.raises(ValueError):
        BusyTracker(window=2, required=3)


# ---- hold decisions -------------------------------------------------------
def test_busy_then_grace_then_idle():
    c, s = HoldController(), Settings(grace_seconds=60)
    assert c.decide(s, ["HandBrake"], AC_POWER, 0).state == "busy"
    grace = c.decide(s, [], AC_POWER, 30)
    assert grace.hold and grace.state == "grace"
    assert c.decide(s, [], AC_POWER, 61).state == "idle"


def test_off_and_pause_beat_busy():
    c = HoldController()
    assert not c.decide(Settings(enabled=False), ["x"], AC_POWER, 0).hold
    c.pause(until=100)
    assert c.decide(Settings(), ["x"], AC_POWER, 50).state == "paused"
    assert c.decide(Settings(), ["x"], AC_POWER, 101).state == "busy"


def test_manual_keeps_awake_without_work():
    c = HoldController()
    c.set_manual(True)
    assert c.decide(Settings(), [], AC_POWER, 0).state == "manual"


@pytest.mark.parametrize("settings, snap, blocked", [
    (Settings(only_when_plugged_in=True), PowerSnapshot(True, 90, False), True),
    (Settings(min_battery_percent=20), PowerSnapshot(True, 15, False), True),
    (Settings(min_battery_percent=20), PowerSnapshot(True, 50, False), False),
    (Settings(respect_battery_saver=True), PowerSnapshot(True, 50, True), True),
    (Settings(respect_battery_saver=False), PowerSnapshot(True, 50, True), False),
    (Settings(only_when_plugged_in=True), PowerSnapshot(False, 10, False), False),
])
def test_battery_guard(settings, snap, blocked):
    decision = HoldController().decide(settings, ["x"], snap, 0)
    assert (decision.state == "battery") is blocked


# ---- keep-awake flags -----------------------------------------------------
def test_keep_awake_flags_and_dedup():
    calls = []
    keep = KeepAwake(setter=lambda f: calls.append(f) or 1)
    keep.apply(True, False)
    keep.apply(True, False)
    keep.apply(True, True)
    keep.release()
    assert calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED,
                     ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED,
                     ES_CONTINUOUS]
    assert not keep.holding


# ---- lid mode -------------------------------------------------------------
SAMPLE_QUERY = """Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)
  Subgroup GUID: 4f971e89-eebd-4455-a8de-9e59040e7347  (Power buttons and lid)
    Power Setting GUID: 5ca83367-6e45-459f-a27b-476b1d01c936  (Lid close action)
      Possible Setting Index: 000
    Current AC Power Setting Index: 0x00000001
    Current DC Power Setting Index: 0x00000002
"""


def test_parse_lid_query():
    assert parse_lid_query(SAMPLE_QUERY) == LidSetting("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", 1, 2)
    with pytest.raises(LidError):
        parse_lid_query("garbage")


class FakePowercfg:
    def __init__(self):
        self.value = LidSetting("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", 1, 2)
        self.writes = []

    def read(self):
        return self.value

    def write(self, setting):
        self.writes.append(setting)
        self.value = setting


def test_lid_override_restore(tmp_path):
    fake, state = FakePowercfg(), tmp_path / "lid.json"
    guard = LidGuard(state, fake.read, fake.write)
    guard.override()
    assert (fake.value.ac, fake.value.dc) == (0, 0) and state.exists()
    guard.override()  # idempotent
    assert len(fake.writes) == 1
    guard.restore()
    assert (fake.value.ac, fake.value.dc) == (1, 2) and not state.exists()


def test_crash_recovery_from_state_file(tmp_path):
    fake, state = FakePowercfg(), tmp_path / "lid.json"
    LidGuard(state, fake.read, fake.write).override()   # process "crashes" here
    survivor = LidGuard(state, fake.read, fake.write)
    assert survivor.recover_stale() is True
    assert (fake.value.ac, fake.value.dc) == (1, 2) and not state.exists()
    assert survivor.recover_stale() is False


def test_tampered_state_file_is_discarded(tmp_path):
    fake, state = FakePowercfg(), tmp_path / "lid.json"
    state.write_text(json.dumps({"scheme": "x & del", "ac": 9, "dc": 0}))
    assert LidGuard(state, fake.read, fake.write).recover_stale() is False
    assert fake.writes == [] and not state.exists()


def test_browser_counters_never_count_but_downloads_do():
    tracker = BusyTracker(window=2, required=2)
    browsing = AppReading("chrome.exe", "chrome.exe", APP_KIND, 60.0, 5e6, 20, write_bytes_per_s=3e6)
    for t in range(3):
        assert tracker.update([browsing], CPU, IO, t) == []
    dl = AppReading(DOWNLOAD_KEY, "Browser download", APP_KIND, 0.0, 0.0, 1)
    for t in range(3, 5):
        busy = tracker.update([browsing, dl], CPU, IO, t)
    assert [b.key for b in busy] == [DOWNLOAD_KEY]


def test_active_downloads_only_fresh_partial_files(tmp_path):
    import os
    fresh, stale, done = tmp_path / "a.iso.crdownload", tmp_path / "b.zip.part", tmp_path / "c.zip"
    for f in (fresh, stale, done):
        f.write_bytes(b"x")
    os.utime(stale, (1000, 1000))
    assert active_downloads(tmp_path) == ["a.iso.crdownload"]
    assert download_reading(tmp_path).key == DOWNLOAD_KEY
    assert download_reading(tmp_path / "missing") is None

def test_override_with_leftover_marker_keeps_true_original(tmp_path):
    fake, state = FakePowercfg(), tmp_path / "lid.json"
    LidGuard(state, fake.read, fake.write).override()        # crash: live value now 0/0
    guard = LidGuard(state, fake.read, fake.write)
    guard.override()                                          # must not record 0/0 as original
    guard.restore()
    assert (fake.value.ac, fake.value.dc) == (1, 2)


def test_corrupt_marker_with_lid_stuck_falls_back_to_sleep(tmp_path):
    fake, state = FakePowercfg(), tmp_path / "lid.json"
    fake.value = LidSetting(fake.value.scheme, 0, 0)
    state.write_text("{garbage")
    assert LidGuard(state, fake.read, fake.write).recover_stale() is True
    assert (fake.value.ac, fake.value.dc) == (1, 1) and not state.exists()


def test_failed_save_leaves_lid_untouched(tmp_path):
    fake = FakePowercfg()
    guard = LidGuard(tmp_path / "missing-dir" / "lid.json", fake.read, fake.write)
    with pytest.raises(LidError):
        guard.override()
    assert fake.writes == [] and not guard.overridden


def test_non_finite_numbers_in_settings_are_ignored(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"cpu_threshold": NaN, "grace_seconds": Infinity, "min_battery_percent": 1e999}')
    assert config.load(path) == Settings()
