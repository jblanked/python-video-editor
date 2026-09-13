"""Agent-facing timeline tools: inspect and edit the open project timeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools import context, transcribe_ops
from tools import ffmpeg_utils as ff
from tools.project import Project
from tools.results import error_result, success_result

MIN_SPEECH_SECONDS = 0.2


def timeline_add_clip(
    path: str | Path, start: Any = None, end: Any = None, position: Any = None
) -> dict:
    """Add a clip to the editor timeline at a position, with optional in and out points."""
    project = _require_project()
    if project is None:
        return error_result("No project is open.")
    try:
        segment = project.add_to_timeline(path, start=start, end=end, position=position)
    except (ValueError, OSError) as exc:
        return error_result(str(exc))
    return success_result(
        None,
        f"Added {segment['name']} to the timeline at index {project.timeline.index(segment)}.",
        index=project.timeline.index(segment),
        segment=segment,
    )


def timeline_clear() -> dict:
    """Remove all clips from the editor timeline."""
    project = _require_project()
    if project is None:
        return error_result("No project is open.")
    count = len(project.timeline)
    project.clear_timeline()
    return success_result(None, f"Cleared {count} timeline clips.")


def timeline_get_state() -> dict:
    """Return the media pool and timeline layout of the open project."""
    project = _require_project()
    if project is None:
        return error_result("No project is open.")
    return success_result(None, "Timeline state loaded.", state=project.timeline_state())


def timeline_move_clip(index: Any, new_index: Any) -> dict:
    """Move a timeline clip from one index to another."""
    project = _require_project()
    if project is None:
        return error_result("No project is open.")
    try:
        segment = project.move_segment(index, new_index)
    except (ValueError, OSError) as exc:
        return error_result(str(exc))
    return success_result(
        None,
        f"Moved {segment['name']} to index {project.timeline.index(segment)}.",
        segment=segment,
    )


def timeline_remove_clip(index: Any) -> dict:
    """Remove the timeline clip at the given index."""
    project = _require_project()
    if project is None:
        return error_result("No project is open.")
    try:
        segment = project.remove_segment(index)
    except (ValueError, OSError) as exc:
        return error_result(str(exc))
    return success_result(None, f"Removed {segment['name']} from the timeline.")


def timeline_remove_silence(margin: Any = 0.05) -> dict:
    """Cut silent stretches out of the timeline, rebuilding it from speech only."""
    project = _require_project()
    if project is None:
        return error_result("No project is open.")
    if not project.timeline:
        return error_result("The timeline is empty.")
    try:
        cut = max(0.0, float(ff.parse_time(margin) or 0.0))
    except ValueError as exc:
        return error_result(str(exc))
    durations = {
        str(clip.get("path")): float(clip.get("duration") or 0.0) for clip in project.media
    }
    gaps: dict[str, list[dict]] = {}
    rebuilt: list[dict] = []
    removed = 0.0
    for segment in project.timeline:
        path = str(segment.get("path"))
        if path not in gaps:
            gaps[path] = transcribe_ops.detect_silence(path, durations.get(path, 0.0))
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or 0.0)
        windows = _speech_windows(start, end, gaps[path], cut)
        for window_start, window_end in windows:
            chunk = dict(segment)
            chunk["start"] = round(window_start, 3)
            chunk["end"] = round(window_end, 3)
            rebuilt.append(chunk)
        removed += max(0.0, (end - start) - sum(window_end - window_start for window_start, window_end in windows))
    if removed < 0.01:
        return success_result(None, "No silence found to remove.", removed_seconds=0.0)
    before = len(project.timeline)
    project.timeline[:] = rebuilt
    return success_result(
        None,
        f"Removed {removed:.1f}s of silence; {before} clips became {len(rebuilt)}.",
        removed_seconds=round(removed, 2),
        clips=len(rebuilt),
        segments=rebuilt,
    )


def timeline_render(
    output: str | Path, width: Any = None, height: Any = None, fps: Any = None
) -> dict:
    """Render the timeline into a single video file at the given path."""
    project = _require_project()
    if project is None:
        return error_result("No project is open.")
    return project.render(output, width=width, height=height, fps=fps)


def timeline_trim_clip(index: Any, start: Any = None, end: Any = None) -> dict:
    """Set new in and out points for the timeline clip at the given index."""
    project = _require_project()
    if project is None:
        return error_result("No project is open.")
    try:
        segment = project.set_segment_range(index, start=start, end=end)
    except (ValueError, OSError) as exc:
        return error_result(str(exc))
    return success_result(
        None,
        f"Trimmed {segment['name']} to {segment['start']:.2f}s - {segment['end']:.2f}s.",
        segment=segment,
    )


def _require_project() -> Project | None:
    """Return the active project instance, or None when unavailable."""
    return context.get_project()


def _speech_windows(
    start: float, end: float, silence: list[dict], margin: float
) -> list[tuple[float, float]]:
    """Return the non-silent windows inside one segment range."""
    windows = [(start, end)]
    for gap in silence:
        gap_start = float(gap.get("start") or 0.0) - margin
        gap_end = float(gap.get("end") or 0.0) + margin
        trimmed: list[tuple[float, float]] = []
        for window_start, window_end in windows:
            if gap_end <= window_start or gap_start >= window_end:
                trimmed.append((window_start, window_end))
                continue
            if gap_start > window_start:
                trimmed.append((window_start, gap_start))
            if gap_end < window_end:
                trimmed.append((gap_end, window_end))
        windows = trimmed
    return [(s, e) for s, e in windows if e - s >= MIN_SPEECH_SECONDS]
