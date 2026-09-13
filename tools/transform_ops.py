"""Transform operations: resize, crop, rotate, flip, speed, reverse, fade, blur."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from tools import ffmpeg_utils as ff
from tools.results import ensure_file, error_result, success_result


def blur_video(path: str | Path, output: str | Path, strength: Any = 10) -> dict:
    """Apply a Gaussian blur; strength is the sigma value in pixels."""
    source = ensure_file(path)
    out = _prepare_output(output)
    try:
        sigma = float(strength)
    except (TypeError, ValueError):
        return error_result("Strength must be a number.")
    if sigma <= 0:
        return error_result("Strength must be greater than zero.")
    return _apply_filter(source, out, f"gblur=sigma={sigma:g}", f"Blurred clip saved to {out.name}.")


def change_speed(path: str | Path, output: str | Path, factor: Any = 2.0) -> dict:
    """Speed up or slow down a clip; factor above 1 is faster."""
    source = ensure_file(path)
    out = _prepare_output(output)
    try:
        speed = float(factor)
    except (TypeError, ValueError):
        return error_result("Factor must be a number.")
    if speed <= 0 or speed > 16:
        return error_result("Factor must be between 0 and 16.")
    info = ff.probe(source)
    video_filter = f"setpts={1.0 / speed:.6f}*PTS"
    audio_filter = _atempo_chain(speed) if ff.has_audio(info) else None
    result = _apply_filter(
        source,
        out,
        video_filter,
        f"Speed changed to {speed:g}x, saved {out.name}.",
        audio_filter=audio_filter,
    )
    if result["success"]:
        total = ff.media_duration(info)
        result["details"] = {"factor": speed, "new_duration": total / speed if total else None}
    return result


def crop_video(
    path: str | Path,
    output: str | Path,
    width: Any,
    height: Any,
    x: Any = None,
    y: Any = None,
) -> dict:
    """Crop to width x height; x and y default to centring the crop area."""
    source = ensure_file(path)
    out = _prepare_output(output)
    try:
        crop_width = ff.even(width)
        crop_height = ff.even(height)
    except (TypeError, ValueError):
        return error_result("Width and height must be numbers.")
    if crop_width < 2 or crop_height < 2:
        return error_result("Width and height must be at least 2 pixels.")
    x_expr = str(int(float(x))) if x not in (None, "") else "(iw-ow)/2"
    y_expr = str(int(float(y))) if y not in (None, "") else "(ih-oh)/2"
    video_filter = f"crop={crop_width}:{crop_height}:{x_expr}:{y_expr}"
    result = _apply_filter(
        source, out, video_filter, f"Cropped to {crop_width}x{crop_height}, saved {out.name}."
    )
    if result["success"]:
        result["details"] = {"width": crop_width, "height": crop_height}
    return result


def fade_video(
    path: str | Path, output: str | Path, fade_in: Any = 1.0, fade_out: Any = 1.0
) -> dict:
    """Add fade in and fade out transitions at the start and end of a clip."""
    source = ensure_file(path)
    out = _prepare_output(output)
    try:
        fade_in_seconds = float(fade_in or 0)
        fade_out_seconds = float(fade_out or 0)
    except (TypeError, ValueError):
        return error_result("Fade values must be numbers.")
    if fade_in_seconds <= 0 and fade_out_seconds <= 0:
        return error_result("Provide a fade in or fade out duration.")
    info = ff.probe(source)
    total = ff.media_duration(info)
    filters: list[str] = []
    audio_filters: list[str] = []
    if fade_in_seconds > 0:
        filters.append(f"fade=t=in:st=0:d={fade_in_seconds:.3f}")
        audio_filters.append(f"afade=t=in:st=0:d={fade_in_seconds:.3f}")
    if fade_out_seconds > 0:
        start = max(0.0, (total or fade_out_seconds) - fade_out_seconds)
        filters.append(f"fade=t=out:st={start:.3f}:d={fade_out_seconds:.3f}")
        audio_filters.append(f"afade=t=out:st={start:.3f}:d={fade_out_seconds:.3f}")
    audio_filter = ",".join(audio_filters) if ff.has_audio(info) else None
    return _apply_filter(
        source,
        out,
        ",".join(filters),
        f"Fades applied, saved {out.name}.",
        audio_filter=audio_filter,
    )


def flip_video(path: str | Path, output: str | Path, direction: str = "horizontal") -> dict:
    """Mirror a clip horizontally, vertically, or both."""
    source = ensure_file(path)
    out = _prepare_output(output)
    choice = str(direction or "horizontal").strip().lower()
    options = {
        "horizontal": "hflip",
        "vertical": "vflip",
        "both": "hflip,vflip",
    }
    if choice not in options:
        return error_result("Direction must be horizontal, vertical, or both.")
    return _apply_filter(
        source, out, options[choice], f"Flipped {choice}, saved {out.name}."
    )


def resize_video(
    path: str | Path,
    output: str | Path,
    width: Any = None,
    height: Any = None,
    mode: str = "fit",
) -> dict:
    """Resize a clip to a width, height, or exact size; mode is fit or stretch."""
    source = ensure_file(path)
    out = _prepare_output(output)
    try:
        target_width = ff.even(width) if width not in (None, "") else None
        target_height = ff.even(height) if height not in (None, "") else None
    except (TypeError, ValueError):
        return error_result("Width and height must be numbers.")
    if target_width is None and target_height is None:
        return error_result("Provide a width, a height, or both.")
    if target_width and target_height:
        if mode == "stretch":
            video_filter = f"scale={target_width}:{target_height},setsar=1"
        else:
            video_filter = (
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
            )
    elif target_width:
        video_filter = f"scale={target_width}:-2"
    else:
        video_filter = f"scale=-2:{target_height}"
    result = _apply_filter(source, out, video_filter, f"Resized clip saved to {out.name}.")
    if result["success"]:
        result["details"] = {"width": target_width, "height": target_height, "mode": mode}
    return result


def reverse_video(path: str | Path, output: str | Path) -> dict:
    """Play a clip backwards, reversing both video and audio."""
    source = ensure_file(path)
    out = _prepare_output(output)
    audio_filter = "areverse" if ff.has_audio(ff.probe(source)) else None
    return _apply_filter(
        source, out, "reverse", f"Reversed clip saved to {out.name}.", audio_filter=audio_filter
    )


def rotate_video(path: str | Path, output: str | Path, angle: Any = 90) -> dict:
    """Rotate a clip clockwise by 90, 180, 270 degrees, or any other angle."""
    source = ensure_file(path)
    out = _prepare_output(output)
    try:
        value = float(angle)
    except (TypeError, ValueError):
        return error_result("Angle must be a number.")
    normalised = value % 360
    if math.isclose(normalised, 90, abs_tol=0.01):
        video_filter = "transpose=1"
    elif math.isclose(normalised, 180, abs_tol=0.01):
        video_filter = "transpose=2,transpose=2"
    elif math.isclose(normalised, 270, abs_tol=0.01):
        video_filter = "transpose=2"
    else:
        video_filter = f"rotate={value:g}*PI/180"
    result = _apply_filter(
        source, out, video_filter, f"Rotated {value:g} degrees, saved {out.name}."
    )
    if result["success"]:
        result["details"] = {"angle": value}
    return result


def _apply_filter(
    source: Path,
    output: Path,
    video_filter: str,
    message: str,
    audio_filter: str | None = None,
) -> dict:
    """Run one operation with a video filter and an optional audio filter."""
    args = ["-i", str(source), "-vf", video_filter]
    if audio_filter:
        args += ["-af", audio_filter]
    args += ff.encode_args(output)
    args.append(str(output))
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Operation failed: {ff.error_message(stderr)}")
    return success_result(output, message)


def _atempo_chain(factor: float) -> str:
    """Build an atempo filter chain that supports factors outside 0.5-2.0."""
    parts: list[str] = []
    remaining = factor
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def _prepare_output(output: str | Path) -> Path:
    """Resolve the output path and create its parent directory."""
    target = Path(str(output)).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
