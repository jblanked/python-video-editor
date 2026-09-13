"""Media inspection: probe a file and return structured video details."""

from __future__ import annotations

from pathlib import Path

from tools import ffmpeg_utils as ff
from tools.results import ensure_file, success_result


def get_video_info(path: str | Path) -> dict:
    """Return duration, resolution, fps, codecs, and size for a media file."""
    source = ensure_file(path)
    info = ff.probe(source)
    video = ff.video_stream(info)
    audio = ff.audio_stream(info)
    duration = ff.media_duration(info)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = ff.stream_fps(video)
    size_bytes = source.stat().st_size
    summary = f"{source.name}: {ff.format_time(duration)}, {width}x{height}"
    if fps:
        summary += f", {fps:.2f} fps"
    if video.get("codec_name"):
        summary += f", {video['codec_name']}"
    if audio.get("codec_name"):
        summary += f" + {audio['codec_name']}"
    return success_result(
        None,
        summary,
        path=str(source),
        name=source.name,
        duration=duration,
        duration_text=ff.format_time(duration),
        width=width,
        height=height,
        fps=fps,
        video_codec=video.get("codec_name") or "",
        audio_codec=audio.get("codec_name") or "",
        has_audio=bool(audio),
        size_bytes=size_bytes,
        container=(info.get("format") or {}).get("format_name") or "",
    )


def _fraction(value: object) -> float:
    """Convert an ffprobe frame rate like '30000/1001' to a float."""
    text = str(value or "").strip()
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
