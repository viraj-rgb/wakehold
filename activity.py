"""Per-app activity sampling.

Reads the same counters Task Manager shows (CPU time and I/O bytes per
process), groups processes by executable, and folds every process an AI
coding agent starts into that agent.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import PureWindowsPath

import psutil

log = logging.getLogger(__name__)

AGENT_KIND = "agent"
APP_KIND = "app"

# Executable stem -> (agent key, display name)
AGENT_EXES = {
    "claude": ("claude", "Claude Code"),
    "codex": ("codex", "Codex"),
    "gemini": ("gemini", "Gemini CLI"),
    "aider": ("aider", "Aider"),
    "opencode": ("opencode", "OpenCode"),
    "cursor-agent": ("cursor-agent", "Cursor Agent"),
    "copilot": ("copilot", "Copilot CLI"),
    "goose": ("goose", "Goose"),
}

# Agents launched through a script runtime are recognised by their command line.
RUNTIME_STEMS = frozenset({"node", "bun", "deno", "python", "pythonw", "python3", "uv", "uvx"})
AGENT_CMDLINE_TOKENS = (
    ("@anthropic-ai/claude-code", "claude"),
    ("claude-code", "claude"),
    ("@openai/codex", "codex"),
    ("@google/gemini-cli", "gemini"),
    ("gemini-cli", "gemini"),
    ("aider", "aider"),
    ("opencode", "opencode"),
    ("cursor-agent", "cursor-agent"),
    ("@github/copilot", "copilot"),
    ("goose", "goose"),
)

# Desktop apps that share an agent's exe name (e.g. the Claude desktop app).
DESKTOP_APP_MARKERS = ("anthropicclaude", "\\windowsapps\\", "--type=")

# Never counted: OS plumbing, plus media and call apps that hold the PC
# awake themselves while they play.
DEFAULT_EXCLUDED = frozenset({
    "system idle process", "system", "registry", "memory compression", "secure system",
    "csrss.exe", "dwm.exe", "smss.exe", "wininit.exe", "services.exe", "lsass.exe",
    "audiodg.exe", "fontdrvhost.exe", "msmpeng.exe", "searchindexer.exe",
    "searchprotocolhost.exe", "searchfilterhost.exe", "wmiprvse.exe", "taskmgr.exe",
    "spotify.exe", "vlc.exe", "music.ui.exe", "microsoft.media.player.exe",
    "zoom.exe", "ms-teams.exe", "teams.exe", "slack.exe", "discord.exe", "skype.exe",
    "wakehold.exe",
})

# Browsers burn CPU, network and cache writes just showing pages and video,
# so their counters never count; downloads are detected from partial files.
BROWSER_APPS = frozenset({
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe",
    "comet.exe", "arc.exe", "zen.exe", "waterfox.exe", "librewolf.exe",
})

MAX_ANCESTRY_DEPTH = 32


@dataclass(frozen=True)
class AppReading:
    key: str
    label: str
    kind: str
    cpu_percent: float
    io_bytes_per_s: float
    process_count: int
    write_bytes_per_s: float = 0.0


@dataclass(frozen=True)
class _Snapshot:
    name: str
    ppid: int
    create_time: float
    cpu_s: float
    io_bytes: int
    write_bytes: int = 0


def agent_key(key: str) -> str:
    return f"agent:{key}"


def match_agent(name: str, exe: str | None, cmdline: list[str] | None) -> str | None:
    """Return the agent key a process belongs to, or None."""
    stem = PureWindowsPath(name).stem.lower()
    joined = " ".join(cmdline or []).lower()
    if stem in AGENT_EXES:
        location = f"{(exe or '').lower()} {joined}"
        if any(marker in location for marker in DESKTOP_APP_MARKERS):
            return None
        return AGENT_EXES[stem][0]
    if stem in RUNTIME_STEMS and joined:
        for token, key in AGENT_CMDLINE_TOKENS:
            if token in joined:
                return key
    return None


def agent_label(key: str) -> str:
    for agent, label in AGENT_EXES.values():
        if agent == key:
            return label
    return key


class ActivitySampler:
    """Produces one AppReading per busy-able app each time sample() is called."""

    def __init__(self, exclude_pids: frozenset[int] = frozenset()) -> None:
        self._exclude = {os.getpid(), *exclude_pids}
        self._prev: dict[int, _Snapshot] = {}
        self._prev_mono: float | None = None
        self._prev_wall: float = 0.0
        self._agent_cache: dict[tuple[int, float], str | None] = {}

    def exclude_pid(self, pid: int) -> None:
        self._exclude.add(pid)

    def sample(self) -> list[AppReading]:
        mono, wall = time.monotonic(), time.time()
        current = self._snapshot()
        if self._prev_mono is None:
            self._commit(current, mono, wall)
            return []
        elapsed = max(mono - self._prev_mono, 1e-3)
        owners = self._agent_owners(current)
        totals: dict[str, list] = {}
        for pid, snap in current.items():
            if snap.name.lower() in DEFAULT_EXCLUDED and pid not in owners:
                continue
            cpu, io, written = self._delta(pid, snap)
            owner = owners.get(pid)
            key = agent_key(owner) if owner else snap.name.lower()
            label = agent_label(owner) if owner else snap.name
            entry = totals.setdefault(key, [label, AGENT_KIND if owner else APP_KIND, 0.0, 0, 0, 0])
            entry[2] += cpu
            entry[3] += io
            entry[4] += 1
            entry[5] += written
        self._commit(current, mono, wall)
        return [
            AppReading(key, label, kind, cpu / elapsed * 100.0, io / elapsed, count, written / elapsed)
            for key, (label, kind, cpu, io, count, written) in totals.items()
        ]

    def _commit(self, current: dict[int, _Snapshot], mono: float, wall: float) -> None:
        self._prev, self._prev_mono, self._prev_wall = current, mono, wall
        live = {(pid, snap.create_time) for pid, snap in current.items()}
        self._agent_cache = {k: v for k, v in self._agent_cache.items() if k in live}

    def _delta(self, pid: int, snap: _Snapshot) -> tuple[float, int, int]:
        prev = self._prev.get(pid)
        if prev is not None and prev.create_time == snap.create_time:
            return (max(snap.cpu_s - prev.cpu_s, 0.0), max(snap.io_bytes - prev.io_bytes, 0),
                    max(snap.write_bytes - prev.write_bytes, 0))
        if snap.create_time >= self._prev_wall:
            return snap.cpu_s, snap.io_bytes, snap.write_bytes
        return 0.0, 0, 0

    def _snapshot(self) -> dict[int, _Snapshot]:
        result: dict[int, _Snapshot] = {}
        attrs = ["name", "ppid", "create_time", "cpu_times", "io_counters"]
        for proc in psutil.process_iter(attrs, ad_value=None):
            info = proc.info
            if proc.pid in self._exclude or not info.get("name"):
                continue
            times, io = info.get("cpu_times"), info.get("io_counters")
            if times is None or io is None or info.get("create_time") is None:
                continue
            io_total = io.read_bytes + io.write_bytes + getattr(io, "other_bytes", 0)
            result[proc.pid] = _Snapshot(
                info["name"], info.get("ppid") or 0, info["create_time"],
                times.user + times.system, io_total, io.write_bytes,
            )
        return result

    def _identify(self, pid: int, snap: _Snapshot) -> str | None:
        cache_key = (pid, snap.create_time)
        if cache_key in self._agent_cache:
            return self._agent_cache[cache_key]
        stem = PureWindowsPath(snap.name).stem.lower()
        found = None
        if stem in AGENT_EXES or stem in RUNTIME_STEMS:
            try:
                proc = psutil.Process(pid)
                found = match_agent(snap.name, proc.exe(), proc.cmdline())
            except psutil.Error:
                found = None
        self._agent_cache[cache_key] = found
        return found

    def _agent_owners(self, current: dict[int, _Snapshot]) -> dict[int, str]:
        """Map pid -> agent key for agents and all of their descendants."""
        direct = {pid: key for pid, snap in current.items() if (key := self._identify(pid, snap))}
        owners: dict[int, str] = {}
        for pid in current:
            owners_key = _walk_up(pid, current, direct)
            if owners_key:
                owners[pid] = owners_key
        return owners


def _walk_up(pid: int, current: dict[int, _Snapshot], direct: dict[int, str]) -> str | None:
    seen: set[int] = set()
    node = pid
    for _ in range(MAX_ANCESTRY_DEPTH):
        if node in direct:
            return direct[node]
        snap = current.get(node)
        if snap is None or node in seen:
            return None
        seen.add(node)
        parent = current.get(snap.ppid)
        # A parent created after its child is a recycled pid, not the real parent.
        if parent is None or parent.create_time > snap.create_time:
            return None
        node = snap.ppid
    return None
