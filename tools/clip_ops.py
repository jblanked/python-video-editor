"""Clip-level operations: trim, cut, split, and join."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from tools import ffmpeg_utils as ff
from tools.results import ensure_file, error_result, success_result


def cut_video(
    path: str | Path,
    output: str | Path,
    start: Any = None,
    end: Any = None,
    reencode: bool = True,
) -> dict:
    """Remove the segment between start and end, keeping the rest of the clip."""
    source = ensure_file(path)
    out = _prepare_output(output)
    info = ff.probe(source)
    total = ff.media_duration(info)
    start_seconds = ff.parse_time(start) or 0.0
    end_seconds = ff.parse_time(end)
    if end_seconds is None:
        return error_result("An end time is required to cut a segment.")
    if total > 0:
        start_seconds = min(max(0.0, start_seconds), total)
        end_seconds = min(end_seconds, total)
    if end_seconds <= start_seconds:
        return error_result("End time must be greater than start time.")
    if start_seconds <= 0.001:
        result = trim_video(source, out, start=end_seconds, reencode=reencode)
    elif total > 0 and end_seconds >= total - 0.001:
        result = trim_video(source, out, end=start_seconds, reencode=reencode)
    else:
        result = _cut_middle(source, out, start_seconds, end_seconds, reencode)
    if result["success"]:
        result["message"] = f"Cut {start_seconds:.2f}s - {end_seconds:.2f}s, saved {out.name}."
        result["details"] = {"cut_start": start_seconds, "cut_end": end_seconds}
    return result


def join_videos(paths: Any, output: str | Path, mode: str = "auto") -> dict:
    """Concatenate clips in order; mode is auto, copy, or reencode."""
    files = [ensure_file(item) for item in _as_path_list(paths)]
    if len(files) < 2:
        return error_result("Joining needs at least two clips.")
    if mode not in {"auto", "copy", "reencode"}:
        return error_result("Join mode must be auto, copy, or reencode.")
    out = _prepare_output(output)
    copy_error = ""
    if mode in {"auto", "copy"}:
        ok, stderr = _concat_copy(files, out)
        if ok:
            return success_result(
                out, f"Joined {len(files)} clips into {out.name}.", clips=len(files), mode="copy"
            )
        copy_error = ff.error_message(stderr)
        if mode == "copy":
            return error_result(f"Join failed: {copy_error}")
    result = _concat_filter(files, out)
    if not result["success"] and copy_error:
        result["message"] = f"{result['message']} (stream copy also failed: {copy_error})"
    return result


def split_video(
    path: str | Path,
    output_dir: str | Path,
    count: int | None = None,
    seconds: Any = None,
    prefix: str | None = None,
) -> dict:
    """Split a clip into equal parts by count, or into fixed-length parts by seconds."""
    source = ensure_file(path)
    total = ff.media_duration(ff.probe(source))
    if total <= 0:
        return error_result("Cannot split a clip with unknown duration.")
    part_seconds = ff.parse_time(seconds)
    bounds: list[tuple[float, float]] = []
    if count is not None:
        try:
            count_value = int(count)
        except (TypeError, ValueError):
            return error_result("Count must be a whole number.")
        if count_value < 2:
            return error_result("Count must be at least 2.")
        length = total / count_value
        bounds = [
            (index * length, total if index == count_value - 1 else (index + 1) * length)
            for index in range(count_value)
        ]
    elif part_seconds:
        if part_seconds <= 0:
            return error_result("Seconds must be greater than zero.")
        position = 0.0
        while position < total - 0.01:
            bounds.append((position, min(position + part_seconds, total)))
            position += part_seconds
    else:
        return error_result("Provide either count or seconds to split the clip.")
    out_dir = Path(output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    base = str(prefix or source.stem)
    created: list[str] = []
    for index, (start_seconds, end_seconds) in enumerate(bounds, start=1):
        target = out_dir / f"{base}_part{index:02d}{source.suffix}"
        result = trim_video(source, target, start=start_seconds, end=end_seconds)
        if not result["success"]:
            return result
        created.append(str(target))
    return success_result(
        out_dir, f"Split into {len(created)} parts in {out_dir.name}.", files=created
    )


def trim_video(
    path: str | Path,
    output: str | Path,
    start: Any = None,
    end: Any = None,
    duration: Any = None,
    reencode: bool = True,
) -> dict:
    """Keep only the segment between start and end (or start plus duration)."""
    source = ensure_file(path)
    out = _prepare_output(output)
    info = ff.probe(source)
    total = ff.media_duration(info)
    start_seconds = ff.parse_time(start) or 0.0
    end_seconds = ff.parse_time(end)
    duration_seconds = ff.parse_time(duration)
    if end_seconds is None and duration_seconds is not None:
        end_seconds = start_seconds + duration_seconds
    if end_seconds is None and total > 0:
        end_seconds = total
    if end_seconds is not None and end_seconds <= start_seconds:
        return error_result("End time must be greater than start time.")
    args = ["-ss", f"{start_seconds:.3f}", "-i", str(source)]
    length = None if end_seconds is None else end_seconds - start_seconds
    if length is not None:
        args += ["-t", f"{length:.3f}"]
    if reencode:
        args += ff.encode_args(out)
    else:
        args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    args.append(str(out))
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Trim failed: {ff.error_message(stderr)}")
    return success_result(
        out,
        f"Trimmed clip saved to {out.name}.",
        start=start_seconds,
        end=end_seconds,
        duration=length,
    )


def _as_path_list(value: Any) -> list[str]:
    """Normalise a list or delimited string of paths into a list."""
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value or "").replace(",", "\n")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _concat_copy(files: list[Path], output: Path) -> tuple[bool, str]:
    """Concatenate compatible files with the concat demuxer and stream copy."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as handle:
        for item in files:
            escaped = str(item).replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")
        list_path = Path(handle.name)
    try:
        return ff.run_ffmpeg(
            ["-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output)]
        )
    finally:
        list_path.unlink(missing_ok=True)


def _concat_filter(files: list[Path], output: Path) -> dict:
    """Concatenate files through the concat filter with format normalisation."""
    probes = [ff.probe(item) for item in files]
    first = ff.video_stream(probes[0])
    width = ff.even(first.get("width", 1280))
    height = ff.even(first.get("height", 720))
    fps_text = str(first.get("r_frame_rate") or "30/1")
    parts: list[str] = []
    labels: list[str] = []
    for index, info in enumerate(probes):
        parts.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps_text}[v{index}]"
        )
        if ff.has_audio(info):
            parts.append(
                f"[{index}:a]aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
            )
        else:
            silent = ff.media_duration(info) or 1.0
            parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000:d={silent:.3f}[a{index}]"
            )
        labels.append(f"[v{index}][a{index}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(files)}:v=1:a=1[v][a]"
    args: list[str] = []
    for item in files:
        args += ["-i", str(item)]
    args += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]"]
    args += ff.encode_args(output)
    args.append(str(output))
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Join failed: {ff.error_message(stderr)}")
    return success_result(
        output,
        f"Joined {len(files)} clips into {output.name}.",
        clips=len(files),
        mode="reencode",
        size=f"{width}x{height}",
    )


def _cut_middle(source: Path, output: Path, start: float, end: float, reencode: bool) -> dict:
    """Remove a middle segment by trimming head and tail then joining."""
    with tempfile.TemporaryDirectory(prefix="video_editor_") as temp_dir:
        head = Path(temp_dir) / f"head{source.suffix}"
        tail = Path(temp_dir) / f"tail{source.suffix}"
        head_result = trim_video(source, head, end=start, reencode=reencode)
        if not head_result["success"]:
            return head_result
        tail_result = trim_video(source, tail, start=end, reencode=reencode)
        if not tail_result["success"]:
            return tail_result
        return join_videos([str(head), str(tail)], output, mode="reencode" if reencode else "copy")


def _prepare_output(output: str | Path) -> Path:
    """Resolve the output path and create its parent directory."""
    target = Path(str(output)).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
