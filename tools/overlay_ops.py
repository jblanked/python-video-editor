"""Overlay operations: text, subtitles, and image watermarks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools import ffmpeg_utils as ff
from tools.results import ensure_file, error_result, success_result

POSITIONS = (
    "top-left",
    "top-center",
    "top-right",
    "center",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)


def add_subtitles(path: str | Path, subtitle_file: str | Path, output: str | Path) -> dict:
    """Burn a subtitle file (srt, ass, or vtt) into the video."""
    source = ensure_file(path)
    subtitles = ensure_file(subtitle_file)
    out = _prepare_output(output)
    escaped = ff.escape_filter_path(subtitles)
    args = ["-i", str(source), "-vf", f"subtitles='{escaped}'"]
    args += ff.encode_args(out)
    args.append(str(out))
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Subtitles failed: {ff.error_message(stderr)}")
    return success_result(out, f"Subtitles burned into {out.name}.")


def add_text(
    path: str | Path,
    output: str | Path,
    text: str,
    position: str = "bottom-center",
    font_size: Any = 48,
    color: str = "white",
    start: Any = None,
    end: Any = None,
    box: bool = False,
) -> dict:
    """Draw a text overlay, optionally limited to a time range or shown in a box."""
    source = ensure_file(path)
    out = _prepare_output(output)
    content = str(text or "").strip()
    if not content:
        return error_result("Text is required.")
    x_expr, y_expr = _position_pair(position, "text_w", "text_h")
    filters = [
        f"text='{_escape_text(content)}'",
        f"fontcolor={str(color or 'white').strip()}",
        f"fontsize={max(8, int(float(font_size)))}",
        f"x={x_expr}",
        f"y={y_expr}",
    ]
    font = ff.find_font()
    if font:
        filters.insert(0, f"fontfile={ff.escape_filter_path(font)}")
    start_seconds = ff.parse_time(start)
    end_seconds = ff.parse_time(end)
    if start_seconds is not None and end_seconds is not None:
        filters.append(f"enable='between(t,{start_seconds:.3f},{end_seconds:.3f})'")
    elif start_seconds is not None:
        filters.append(f"enable='gte(t,{start_seconds:.3f})'")
    elif end_seconds is not None:
        filters.append(f"enable='lte(t,{end_seconds:.3f})'")
    if box:
        filters += ["box=1", "boxcolor=black@0.5", "boxborderw=12"]
    args = ["-i", str(source), "-vf", f"drawtext={':'.join(filters)}"]
    args += ff.encode_args(out)
    args.append(str(out))
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Add text failed: {ff.error_message(stderr)}")
    return success_result(out, f"Text overlay added, saved {out.name}.", position=position)


def add_watermark(
    path: str | Path,
    image: str | Path,
    output: str | Path,
    position: str = "bottom-right",
    opacity: Any = 0.7,
    width: Any = None,
) -> dict:
    """Overlay an image watermark with the given opacity and optional width."""
    source = ensure_file(path)
    watermark = ensure_file(image)
    out = _prepare_output(output)
    try:
        alpha = float(opacity)
    except (TypeError, ValueError):
        return error_result("Opacity must be a number.")
    alpha = min(max(alpha, 0.0), 1.0)
    x_expr, y_expr = _position_pair(position, "w", "h", video_names=("W", "H"))
    chain = []
    if width not in (None, ""):
        try:
            chain.append(f"scale={ff.even(width)}:-2")
        except (TypeError, ValueError):
            return error_result("Width must be a number.")
    chain += ["format=rgba", f"colorchannelmixer=aa={alpha:g}"]
    graph = f"[1:v]{','.join(chain)}[wm];[0:v][wm]overlay={x_expr}:{y_expr}[v]"
    args = ["-i", str(source), "-i", str(watermark)]
    args += ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?"]
    args += ff.encode_args(out)
    args.append(str(out))
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Watermark failed: {ff.error_message(stderr)}")
    return success_result(out, f"Watermark added, saved {out.name}.", position=position)


def _escape_text(text: str) -> str:
    """Escape user text so it survives the drawtext filter parser."""
    cleaned = text.replace("\r", " ").replace("\n", " ")
    cleaned = cleaned.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    cleaned = cleaned.replace("%", "\\%").replace(",", "\\,")
    return cleaned


def _position_pair(
    position: str,
    subject_width: str,
    subject_height: str,
    margin: int = 20,
    video_names: tuple[str, str] = ("w", "h"),
) -> tuple[str, str]:
    """Return ffmpeg x and y expressions for a named overlay position."""
    choice = str(position or "bottom-center").strip().lower()
    if choice not in POSITIONS:
        choice = "bottom-center"
    video_width, video_height = video_names
    if choice.endswith("left"):
        x_expr = str(margin)
    elif choice.endswith("right"):
        x_expr = f"{video_width}-{subject_width}-{margin}"
    else:
        x_expr = f"({video_width}-{subject_width})/2"
    if choice.startswith("top"):
        y_expr = str(margin)
    elif choice.startswith("bottom"):
        y_expr = f"{video_height}-{subject_height}-{margin}"
    else:
        y_expr = f"({video_height}-{subject_height})/2"
    return x_expr, y_expr


def _prepare_output(output: str | Path) -> Path:
    """Resolve the output path and create its parent directory."""
    target = Path(str(output)).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
