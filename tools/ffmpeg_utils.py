"""Shared ffmpeg helpers: binary discovery, process runner, and time parsing."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = 900
WINDOWS_FLAGS = 0x08000000 if os.name == "nt" else 0
COMMON_BIN_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
)
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Verdana.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

_config: dict[str, Any] = {"ffmpeg": "", "ffprobe": "", "cancel_event": None}


def audio_stream(info: dict) -> dict:
    """Return the first audio stream from ffprobe output, or an empty dict."""
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "audio":
            return stream
    return {}


def binary_status() -> tuple[bool, str]:
    """Return (available, version line) for the configured ffmpeg binary."""
    try:
        result = subprocess.run(
            [get_ffmpeg_path(), "-version"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=WINDOWS_FLAGS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, error_message(result.stderr)
    return True, (result.stdout or "").splitlines()[0]


def clear_cancel_event() -> None:
    """Remove any cancel event so operations run to completion."""
    _config["cancel_event"] = None


def encode_args(output: str | Path) -> list[str]:
    """Return x264/aac encoding args, adding faststart for mp4 and mov output."""
    args = [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
    ]
    if Path(output).suffix.lower() in {".mp4", ".mov", ".m4v"}:
        args += ["-movflags", "+faststart"]
    return args


def error_message(stderr: str, limit: int = 300) -> str:
    """Extract a short, useful message from ffmpeg stderr output."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for line in reversed(lines):
        lowered = line.lower()
        if any(word in lowered for word in ("error", "invalid", "failed", "no such")):
            return line[-limit:]
    return lines[-1][-limit:] if lines else "ffmpeg failed"


def escape_filter_path(path: str | Path) -> str:
    """Escape a filesystem path for use inside an ffmpeg filter argument."""
    text = str(Path(path).resolve()).replace("\\", "/")
    return text.replace(":", "\\:").replace("'", "\\'")


def even(value: Any, minimum: int = 2) -> int:
    """Round a dimension down to the nearest even number, never below minimum."""
    number = int(float(value))
    number -= number % 2
    return max(minimum, number)


def find_font() -> str:
    """Return the first usable system font path, or an empty string."""
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return ""


def format_time(seconds: Any) -> str:
    """Format seconds as HH:MM:SS.mmm for display and ffmpeg arguments."""
    if seconds is None:
        return "00:00:00.000"
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    return f"{hours:02d}:{minutes:02d}:{total % 60:06.3f}"


def find_tool(name: str, fallback: str = "") -> str:
    """Locate a binary on PATH or in the usual macOS package folders."""
    found = shutil.which(name)
    if found:
        return found
    for folder in COMMON_BIN_DIRS:
        candidate = Path(folder) / name
        if candidate.is_file():
            return str(candidate)
    return fallback


def get_ffmpeg_path() -> str:
    """Return the ffmpeg binary path from settings, PATH, or the default name."""
    configured = _config.get("ffmpeg")
    if configured:
        return str(configured)
    return find_tool("ffmpeg", "ffmpeg")


def get_ffplay_path() -> str:
    """Return the ffplay binary path from PATH or next to the configured ffmpeg."""
    configured = _config.get("ffmpeg")
    if configured:
        sibling = Path(str(configured)).with_name("ffplay")
        if sibling.exists():
            return str(sibling)
    return find_tool("ffplay")


def get_ffprobe_path() -> str:
    """Return the ffprobe binary path from settings, PATH, or the default name."""
    configured = _config.get("ffprobe")
    if configured:
        return str(configured)
    if _config.get("ffmpeg"):
        sibling = Path(str(_config["ffmpeg"])).with_name("ffprobe")
        if sibling.exists():
            return str(sibling)
    return find_tool("ffprobe", "ffprobe")


def has_audio(info: dict) -> bool:
    """Return True when ffprobe output contains an audio stream."""
    return bool(audio_stream(info))


def media_duration(info: dict) -> float:
    """Return media duration in seconds from ffprobe output, or 0.0."""
    candidates = [info.get("format", {}).get("duration")]
    candidates.append(video_stream(info).get("duration"))
    candidates.append(audio_stream(info).get("duration"))
    for value in candidates:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def parse_time(value: Any) -> float | None:
    """Parse seconds or a timestamp like 'HH:MM:SS.mmm' into float seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        if ":" in text:
            seconds = 0.0
            for part in text.split(":"):
                seconds = seconds * 60 + float(part or 0)
            return seconds
        return float(text)
    except ValueError as exc:
        raise ValueError(
            f"Invalid time value: {value!r} (use seconds, MM:SS, or HH:MM:SS)"
        ) from exc


def probe(path: str | Path) -> dict:
    """Return ffprobe JSON metadata for a media file."""
    result = subprocess.run(
        [
            get_ffprobe_path(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        creationflags=WINDOWS_FLAGS,
    )
    if result.returncode != 0:
        raise ValueError(f"Cannot read media file: {error_message(result.stderr)}")
    return json.loads(result.stdout or "{}")


def run_ffmpeg(args: list[str], timeout: int = DEFAULT_TIMEOUT) -> tuple[bool, str]:
    """Run ffmpeg with the given arguments, honouring cancellation and timeout."""
    command = [get_ffmpeg_path(), "-hide_banner", "-nostdin", "-nostats", "-y", *args]
    started = time.time()
    with tempfile.TemporaryFile() as error_file:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=error_file,
                creationflags=WINDOWS_FLAGS,
            )
        except OSError as exc:
            return False, str(exc)
        cancel = _config.get("cancel_event")
        while True:
            try:
                process.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                return False, "Operation cancelled."
            if time.time() - started > timeout:
                process.kill()
                return False, "Operation timed out."
        error_file.seek(0)
        stderr = error_file.read().decode("utf-8", errors="replace")
    return process.returncode == 0, stderr


def set_cancel_event(event: Any) -> None:
    """Set a threading.Event checked by run_ffmpeg to stop running processes."""
    _config["cancel_event"] = event


def set_ffmpeg_path(path: str | Path) -> None:
    """Configure an explicit ffmpeg binary path; empty values use PATH lookup."""
    _config["ffmpeg"] = str(path) if path else ""
    _config["ffprobe"] = ""


def stream_fps(stream: dict) -> float:
    """Convert a stream frame rate like '30000/1001' into a float."""
    text = str(stream.get("r_frame_rate") or "").strip()
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        try:
            return float(numerator) / float(denominator or 1)
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def video_stream(info: dict) -> dict:
    """Return the first video stream from ffprobe output, or an empty dict."""
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return {}
