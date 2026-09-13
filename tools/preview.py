"""Embedded preview engine: frame pipes, still frames, and in-app playback."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from PIL import Image

from tools import audio_ops
from tools import ffmpeg_utils as ff
from tools import playback

PREVIEW_WIDTH = 480
PREVIEW_HEIGHT = 270
PREVIEW_FPS = 24
QUEUE_LIMIT = 24
CLOCK_TIMEOUT = 2.5
AUDIO_FILE = Path(tempfile.gettempdir()) / f"video_editor_preview_{os.getpid()}.wav"


class PreviewPlayer:
    """Plays timeline segments as decoded frames timed by the audio clock."""

    def __init__(
        self,
        size: tuple[int, int] = (PREVIEW_WIDTH, PREVIEW_HEIGHT),
        fps: int = PREVIEW_FPS,
    ) -> None:
        """Create a player that renders frames at the given size and frame rate."""
        self.size = size
        self.fps = fps
        self._segments: list[dict] = []
        self._starts: list[float] = []
        self._total = 0.0
        self._range_start = 0.0
        self._frames: deque[tuple[float, bytes]] = deque()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._reader: threading.Thread | None = None
        self._clock_reader: threading.Thread | None = None
        self._pipe: subprocess.Popen | None = None
        self._audio: subprocess.Popen | None = None
        self._audio_active = False
        self._clock_seen = False
        self._clock = 0.0
        self._wall_start: float | None = None
        self._started_at = 0.0
        self._reader_done = True
        self._playing = False

    @property
    def finished(self) -> bool:
        """Return True when decoding stopped and every queued frame was shown."""
        with self._lock:
            empty = not self._frames
        return self._playing and self._reader_done and empty

    @property
    def playing(self) -> bool:
        """Return True while playback is running."""
        return self._playing

    def load(self, segments: list[dict]) -> None:
        """Set the segments to play, in order, starting at local time zero."""
        self._segments = [dict(item) for item in segments]
        self._starts = []
        position = 0.0
        for segment in self._segments:
            self._starts.append(position)
            length = max(
                0.0, float(segment.get("end") or 0.0) - float(segment.get("start") or 0.0)
            )
            position += length
        self._total = position

    def poll(self) -> tuple[float, bytes | None] | None:
        """Return the current position and the frame that is due to be shown."""
        if not self._playing:
            return None
        elapsed = self._elapsed()
        frame: bytes | None = None
        with self._lock:
            while self._frames and self._frames[0][0] <= elapsed + 0.03:
                frame = self._frames.popleft()[1]
        return self.position(), frame

    def position(self) -> float:
        """Return the current playback position on the timeline."""
        return self._range_start + min(self._elapsed(), self._total)

    def start(self, position: float, muted: bool = False, audio_path: Path | None = None) -> None:
        """Start playback from a timeline position, with optional audio sync."""
        self.stop()
        if not self._segments:
            return
        self._range_start = float(position)
        self._cancel.clear()
        with self._lock:
            self._frames.clear()
        self._clock = 0.0
        self._clock_seen = False
        self._wall_start = None
        self._audio_active = False
        self._started_at = time.time()
        self._reader_done = False
        self._playing = True
        if not muted and audio_path is not None:
            self._audio = playback.start_audio(audio_path)
            if self._audio is not None and self._audio.stderr is not None:
                self._audio_active = True
                self._clock_reader = threading.Thread(
                    target=self._read_clock, args=(self._audio,), daemon=True
                )
                self._clock_reader.start()
        self._reader = threading.Thread(target=self._read_frames, daemon=True)
        self._reader.start()

    def stop(self) -> float:
        """Stop playback and return the timeline position where it stopped."""
        position = self.position() if self._playing else self._range_start
        self._playing = False
        self._cancel.set()
        with self._lock:
            self._frames.clear()
            pipe = self._pipe
            self._pipe = None
        playback.kill(pipe)
        playback.stop_audio(self._audio)
        self._audio = None
        for thread in (self._reader, self._clock_reader):
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)
        self._reader = None
        self._clock_reader = None
        self._reader_done = True
        return position

    def _elapsed(self) -> float:
        """Return seconds played, using the audio clock when it is available."""
        if self._audio_active:
            if not self._clock_seen and time.time() - self._started_at > CLOCK_TIMEOUT:
                self._audio_active = False
                self._wall_start = time.time()
            else:
                return max(0.0, self._clock)
        if self._wall_start is None:
            return 0.0
        return max(0.0, time.time() - self._wall_start)

    def _push(self, due: float, data: bytes) -> None:
        """Queue a decoded frame, waiting while the buffer is full."""
        while not self._cancel.is_set():
            with self._lock:
                if len(self._frames) < QUEUE_LIMIT:
                    self._frames.append((due, data))
                    if self._wall_start is None and not self._audio_active:
                        self._wall_start = time.time()
                    return
            time.sleep(0.01)

    def _read_clock(self, process: subprocess.Popen) -> None:
        """Read ffplay stats output to keep the audio clock up to date."""
        stream = process.stderr
        if stream is None:
            return
        try:
            descriptor = stream.fileno()
        except (OSError, ValueError):
            return
        buffer = b""
        while not self._cancel.is_set():
            try:
                chunk = os.read(descriptor, 256)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            buffer += chunk
            parts = re.split(rb"[\r\n]", buffer)
            buffer = parts.pop()
            for part in parts:
                value = playback.read_clock(part)
                if value is not None:
                    self._clock = max(0.0, value)
                    self._clock_seen = True

    def _read_frames(self) -> None:
        """Decode every segment into the frame queue as fast as ffmpeg delivers."""
        frame_bytes = self.size[0] * self.size[1] * 3
        for index, segment in enumerate(self._segments):
            if self._cancel.is_set():
                break
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or 0.0)
            if end - start <= 0.05:
                continue
            process = frame_pipe(segment.get("path"), start, end, self.size, self.fps)
            if process is None or process.stdout is None:
                continue
            with self._lock:
                self._pipe = process
            base = self._starts[index]
            frame_index = 0
            while not self._cancel.is_set():
                data = process.stdout.read(frame_bytes)
                if not data or len(data) < frame_bytes:
                    break
                self._push(base + frame_index / self.fps, data)
                frame_index += 1
            self._release_pipe(process)
        self._reader_done = True

    def _release_pipe(self, process: subprocess.Popen) -> None:
        """Drop a finished frame pipe, killing it when it is still running."""
        with self._lock:
            if self._pipe is process:
                self._pipe = None
        if process.poll() is None:
            playback.kill(process)


def audio_track(segments: list[dict], output: str | Path = AUDIO_FILE) -> Path | None:
    """Render the played range as one wav file; None when it has no audio."""
    return audio_ops.render_audio_range(segments, output, sample_rate=44100, channels=2)


def available() -> bool:
    """Return True when the ffmpeg binary can be found."""
    binary = ff.get_ffmpeg_path()
    return bool(shutil.which(binary) or Path(binary).exists())


def frame_pipe(
    path: str | Path,
    start: Any,
    end: Any,
    size: tuple[int, int] = (PREVIEW_WIDTH, PREVIEW_HEIGHT),
    fps: int = PREVIEW_FPS,
) -> subprocess.Popen | None:
    """Start an ffmpeg process that streams raw preview frames on stdout."""
    width, height = size
    length = max(0.1, float(end) - float(start))
    args = [
        ff.get_ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, float(start)):.3f}",
        "-i",
        str(path),
        "-t",
        f"{length:.3f}",
        "-vf",
        _canvas_filter(width, height) + f",fps={fps}",
        "-an",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    try:
        return subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=ff.WINDOWS_FLAGS,
        )
    except OSError:
        return None


def still_frame(
    path: str | Path,
    position: Any,
    size: tuple[int, int] = (PREVIEW_WIDTH, PREVIEW_HEIGHT),
) -> Image.Image | None:
    """Grab one preview frame at a source position for scrubbing."""
    width, height = size
    args = [
        ff.get_ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, float(position)):.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        _canvas_filter(width, height),
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    try:
        result = subprocess.run(
            args, capture_output=True, timeout=30, creationflags=ff.WINDOWS_FLAGS
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    try:
        with Image.open(io.BytesIO(result.stdout)) as image:
            return image.convert("RGB")
    except (OSError, ValueError):
        return None


def _canvas_filter(width: int, height: int) -> str:
    """Return a scale and pad filter that fits any clip on the preview canvas."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
