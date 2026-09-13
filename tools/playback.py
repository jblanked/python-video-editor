"""Headless audio playback through ffplay, reporting a clock for frame sync."""

from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path

from tools import ffmpeg_utils as ff

CLOCK_PATTERN = re.compile(rb"\s*-?([0-9]+(?:\.[0-9]+)?)")

_processes: set[subprocess.Popen] = set()
_lock = threading.Lock()


def available() -> bool:
    """Return True when an ffplay binary can be found."""
    return bool(ff.get_ffplay_path())


def read_clock(line: bytes) -> float | None:
    """Parse the playback position in seconds from an ffplay stats line."""
    match = CLOCK_PATTERN.match(line)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def start_audio(path: str | Path) -> subprocess.Popen | None:
    """Start headless audio playback with a stats clock; None when unavailable."""
    binary = ff.get_ffplay_path()
    if not binary:
        return None
    source = Path(str(path)).expanduser()
    if not source.exists():
        return None
    try:
        process = subprocess.Popen(
            [
                binary,
                "-hide_banner",
                "-loglevel",
                "quiet",
                "-stats",
                "-autoexit",
                "-nodisp",
                str(source),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=ff.WINDOWS_FLAGS,
        )
    except OSError:
        return None
    _track(process)
    return process


def kill(process: subprocess.Popen | None, timeout: float = 2.0) -> None:
    """Kill a process immediately; frame pipes cannot flush a blocked write."""
    if process is None:
        return
    try:
        process.kill()
    except OSError:
        return
    try:
        process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return


def stop_all() -> None:
    """Terminate every running player process."""
    with _lock:
        processes = list(_processes)
    for process in processes:
        terminate(process)


def stop_audio(process: subprocess.Popen | None) -> None:
    """Stop an audio player and forget it."""
    if process is None:
        return
    kill(process)
    _untrack(process)


def terminate(process: subprocess.Popen | None, grace: float = 0.3) -> None:
    """Ask a process to stop gracefully, killing it when it lingers."""
    if process is None:
        return
    try:
        process.terminate()
    except OSError:
        return
    try:
        process.wait(timeout=grace)
        return
    except (subprocess.TimeoutExpired, OSError):
        pass
    try:
        process.kill()
    except OSError:
        return
    try:
        process.wait(timeout=2)
    except (subprocess.TimeoutExpired, OSError):
        return


def _track(process: subprocess.Popen) -> None:
    """Remember a running player process."""
    with _lock:
        _processes.add(process)


def _untrack(process: subprocess.Popen) -> None:
    """Forget a player process that has finished."""
    with _lock:
        _processes.discard(process)
