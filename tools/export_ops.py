"""Export operations: format conversion, GIF, thumbnails, and frame extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools import ffmpeg_utils as ff
from tools.results import ensure_file, error_result, success_result

FORMAT_EXTENSIONS = {
    "mp4": ".mp4",
    "mov": ".mov",
    "mkv": ".mkv",
    "webm": ".webm",
    "hevc": ".mp4",
    "gif": ".gif",
}
QUALITY_CRF = {"low": "28", "medium": "23", "high": "18", "ultra": "14"}
QUALITY_PRESETS = {"low": "veryfast", "medium": "fast", "high": "medium", "ultra": "slow"}


def convert_video(
    path: str | Path, output: str | Path, format: str = "mp4", quality: str = "high"
) -> dict:
    """Convert a clip to mp4, mov, mkv, webm, hevc, or gif at a quality preset."""
    source = ensure_file(path)
    choice = str(format or "mp4").strip().lower()
    if choice not in FORMAT_EXTENSIONS:
        return error_result(f"Format must be one of: {', '.join(sorted(FORMAT_EXTENSIONS))}.")
    level = str(quality or "high").strip().lower()
    if level not in QUALITY_CRF:
        return error_result(f"Quality must be one of: {', '.join(sorted(QUALITY_CRF))}.")
    out = _prepare_output(output)
    extension = FORMAT_EXTENSIONS[choice]
    if out.suffix.lower() != extension:
        out = out.with_suffix(extension)
    if choice == "gif":
        result = create_gif(source, out)
        if result["success"]:
            result["message"] = f"Converted to GIF, saved {out.name}."
        return result
    crf = QUALITY_CRF[level]
    preset = QUALITY_PRESETS[level]
    args = ["-i", str(source)]
    if choice == "webm":
        args += ["-c:v", "libvpx-vp9", "-crf", crf, "-b:v", "0", "-row-mt", "1"]
        args += ["-c:a", "libopus", "-b:a", "128k"]
    elif choice == "hevc":
        args += ["-c:v", "libx265", "-preset", preset, "-crf", crf, "-tag:v", "hvc1"]
        args += ["-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]
        args += ["-c:a", "aac", "-b:a", "192k"]
        if choice in {"mp4", "mov"}:
            args += ["-movflags", "+faststart"]
    args.append(str(out))
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Convert failed: {ff.error_message(stderr)}")
    return success_result(out, f"Converted to {choice}, saved {out.name}.", format=choice, quality=level)


def create_gif(
    path: str | Path,
    output: str | Path,
    start: Any = None,
    duration: Any = None,
    fps: Any = 12,
    width: Any = 480,
) -> dict:
    """Turn all or part of a clip into an optimised animated GIF."""
    source = ensure_file(path)
    out = _prepare_output(output)
    if out.suffix.lower() != ".gif":
        out = out.with_suffix(".gif")
    try:
        rate = max(1, min(30, int(float(fps))))
        target_width = ff.even(width)
    except (TypeError, ValueError):
        return error_result("FPS and width must be numbers.")
    start_seconds = ff.parse_time(start) or 0.0
    duration_seconds = ff.parse_time(duration)
    graph = (
        f"[0:v]fps={rate},scale={target_width}:-1:flags=lanczos,split[a][b];"
        f"[a]palettegen[p];[b][p]paletteuse"
    )
    args = ["-ss", f"{start_seconds:.3f}", "-i", str(source)]
    if duration_seconds:
        args += ["-t", f"{duration_seconds:.3f}"]
    args += ["-filter_complex", graph, "-an", str(out)]
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"GIF failed: {ff.error_message(stderr)}")
    return success_result(
        out, f"GIF created at {out.name}.", fps=rate, width=target_width, start=start_seconds
    )


def create_thumbnail(
    path: str | Path, output: str | Path, time: Any = "00:00:01", width: Any = 640
) -> dict:
    """Save a single frame from a clip as a jpg or png thumbnail."""
    source = ensure_file(path)
    out = _prepare_output(output)
    if out.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        out = out.with_suffix(".jpg")
    try:
        target_width = ff.even(width)
    except (TypeError, ValueError):
        return error_result("Width must be a number.")
    total = ff.media_duration(ff.probe(source))
    position = ff.parse_time(time) or 0.0
    if total > 0:
        position = min(position, max(0.0, total - 0.05))
    args = [
        "-ss",
        f"{position:.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale={target_width}:-2",
        "-q:v",
        "3",
        str(out),
    ]
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Thumbnail failed: {ff.error_message(stderr)}")
    return success_result(out, f"Thumbnail saved to {out.name}.", time=position)


def extract_frames(
    path: str | Path,
    output_dir: str | Path,
    mode: str = "interval",
    value: Any = 1.0,
    start: Any = None,
    end: Any = None,
) -> dict:
    """Extract frames as png files by interval, frame count, or frames per second."""
    source = ensure_file(path)
    choice = str(mode or "interval").strip().lower()
    if choice not in {"interval", "count", "fps"}:
        return error_result("Mode must be interval, count, or fps.")
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return error_result("Value must be a number.")
    if amount <= 0:
        return error_result("Value must be greater than zero.")
    total = ff.media_duration(ff.probe(source))
    start_seconds = ff.parse_time(start) or 0.0
    end_seconds = ff.parse_time(end)
    segment = (end_seconds - start_seconds) if end_seconds else max(0.0, (total or 0.0) - start_seconds)
    if segment <= 0:
        return error_result("The selected range is empty.")
    if choice == "interval":
        rate = 1.0 / amount
    elif choice == "count":
        rate = amount / segment
    else:
        rate = amount
    out_dir = Path(str(output_dir)).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    args = ["-ss", f"{start_seconds:.3f}", "-i", str(source)]
    if end_seconds:
        args += ["-t", f"{segment:.3f}"]
    args += ["-vf", f"fps={rate:.6f}", "-q:v", "2", str(out_dir / "frame_%04d.png")]
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Frame extraction failed: {ff.error_message(stderr)}")
    files = sorted(item.name for item in out_dir.glob("frame_*.png"))
    if not files:
        return error_result("No frames were extracted from the selected range.")
    return success_result(
        out_dir, f"Extracted {len(files)} frames to {out_dir.name}.", count=len(files), files=files
    )


def _prepare_output(output: str | Path) -> Path:
    """Resolve the output path and create its parent directory."""
    target = Path(str(output)).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
