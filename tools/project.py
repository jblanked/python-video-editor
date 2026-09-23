"""Project model: media pool, timeline segments, and timeline rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools import composite
from tools import ffmpeg_utils as ff
from tools.results import ensure_file, error_result, success_result

UNDO_LIMIT = 40


class Project:
    """Holds the media pool and timeline segments, with JSON persistence."""

    def __init__(self) -> None:
        """Create an empty project with no media and an empty timeline."""
        self.media: list[dict] = []
        self.timeline: list[dict] = []
        self.layers = 1
        self.clipboard: dict | None = None
        self._undo: list[dict] = []
        self._redo: list[dict] = []

    def push_undo(self) -> None:
        """Remember the current state so the next edit can be undone."""
        self._undo.append(self.to_dict())
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    def undo(self) -> bool:
        """Go back one edit; returns True when something changed."""
        if not self._undo:
            return False
        history, future = self._undo, self._redo
        target = history.pop()
        current = self.to_dict()
        pending = self.clipboard
        self.load_dict(target)
        self.clipboard = pending
        self._undo, self._redo = history, future + [current]
        return True

    def redo(self) -> bool:
        """Reapply an undone edit; returns True when something changed."""
        if not self._redo:
            return False
        history, future = self._undo, self._redo
        target = future.pop()
        current = self.to_dict()
        pending = self.clipboard
        self.load_dict(target)
        self.clipboard = pending
        self._undo, self._redo = history + [current], future
        return True

    def add_media(self, path: str | Path) -> dict:
        """Add a file to the media pool and return its clip record."""
        source = ensure_file(path)
        for clip in self.media:
            if clip["path"] == str(source):
                return clip
        info = ff.probe(source)
        video = ff.video_stream(info)
        clip = {
            "path": str(source),
            "name": source.name,
            "duration": ff.media_duration(info),
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "fps": ff.stream_fps(video),
            "has_audio": ff.has_audio(info),
            "size_bytes": source.stat().st_size,
        }
        self.media.append(clip)
        return clip

    def add_to_timeline(
        self,
        path: str | Path,
        start: Any = None,
        end: Any = None,
        position: int | None = None,
        layer: int = 0,
    ) -> dict:
        """Add a clip to a layer, appended after that layer's last clip."""
        self.push_undo()
        clip = self.add_media(path)
        start_seconds, end_seconds = _clamp_range(clip, start, end)
        segment = {
            "path": clip["path"],
            "name": clip["name"],
            "start": start_seconds,
            "end": end_seconds,
            "layer": int(layer),
        }
        if position is None:
            self.timeline.append(segment)
        else:
            index = max(0, min(int(position), len(self.timeline)))
            self.timeline.insert(index, segment)
        self._resync()
        return segment

    def clear_timeline(self) -> None:
        """Remove every segment from the timeline."""
        self.push_undo()
        self.timeline = []

    def load_dict(self, data: dict) -> None:
        """Replace the current media pool and timeline from a project dict."""
        self.media = []
        self.timeline = []
        self._undo = []
        self._redo = []
        self.clipboard = None
        for item in data.get("media", []):
            try:
                self.add_media(item if isinstance(item, str) else item.get("path", ""))
            except (ValueError, OSError):
                continue
        for segment in data.get("timeline", []):
            self.timeline.append(
                {
                    "path": str(segment.get("path", "")),
                    "name": Path(str(segment.get("path", ""))).name,
                    "start": float(segment.get("start") or 0.0),
                    "end": float(segment.get("end") or 0.0),
                    "layer": int(segment.get("layer") or 0),
                    "kind": str(segment.get("kind") or "video"),
                    "mute": bool(segment.get("mute")),
                    "lead": max(0.0, float(segment.get("lead") or 0.0)),
                }
            )
        self.layers = max(1, int(data.get("layers") or 1))
        self._resync()

    def load_file(self, path: str | Path) -> None:
        """Replace the current media pool and timeline from a project file."""
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        self.load_dict(data)

    def move_segment(self, index: int, new_index: int) -> dict:
        """Move a timeline segment to a new position."""
        self.push_undo()
        segment = self.timeline.pop(_segment_index(self.timeline, index))
        target = max(0, min(int(new_index), len(self.timeline)))
        self.timeline.insert(target, segment)
        self._resync()
        return segment

    def remove_media(self, path: str | Path) -> bool:
        """Remove a file from the media pool; returns True when it was present."""
        target = str(Path(str(path)).expanduser())
        for index, clip in enumerate(self.media):
            if clip["path"] == target:
                del self.media[index]
                return True
        return False

    def remove_segment(self, index: int) -> dict:
        """Remove a timeline segment by index and return it."""
        self.push_undo()
        segment = self.timeline.pop(_segment_index(self.timeline, index))
        self._resync()
        return segment

    def set_segment_layer(self, index: int, layer: int) -> dict:
        """Move a timeline clip onto another layer, appended on top."""
        position = _segment_index(self.timeline, index)
        self.push_undo()
        segment = self.timeline.pop(position)
        target = int(layer)
        segment["layer"] = target
        insert_at = len(self.timeline)
        for offset, item in enumerate(self.timeline):
            if int(item.get("layer") or 0) == target:
                insert_at = offset + 1
        self.timeline.insert(insert_at, segment)
        self._resync()
        return segment

    def detach_audio(self, index: int) -> tuple[dict, dict]:
        """Split a clip's audio onto its own lane under the clip.

        The clip keeps playing without sound and the new audio segment holds the
        same range, so it can be trimmed and moved on its own afterwards.
        """
        position = _segment_index(self.timeline, index)
        segment = self.timeline[position]
        if str(segment.get("kind") or "video") == "audio":
            raise ValueError("This clip is already an audio track.")
        if segment.get("mute"):
            raise ValueError("The audio of this clip is already separated.")
        path = str(segment.get("path"))
        audible = False
        for clip in self.media:
            if str(clip.get("path")) == path:
                audible = bool(clip.get("has_audio"))
                break
        else:
            try:
                audible = ff.has_audio(ff.probe(path))
            except (ValueError, OSError):
                audible = False
        if not audible:
            raise ValueError("This clip has no audio track.")
        self.push_undo()
        lane = int(segment.get("layer") or 0) - 1
        cursor = 0.0
        for item in self.timeline[: position + 1]:
            if int(item.get("layer") or 0) == lane:
                cursor = _abs_end(item)
        segment["mute"] = True
        audio = {
            "path": segment.get("path"),
            "name": segment.get("name"),
            "start": float(segment.get("start") or 0.0),
            "end": float(segment.get("end") or 0.0),
            "layer": lane,
            "kind": "audio",
            "lead": max(0.0, _abs_start(segment) - cursor),
        }
        self.timeline.insert(position + 1, audio)
        self._resync()
        return segment, audio

    def set_segment_lead(self, index: int, lead: Any) -> dict:
        """Shift a clip along its lane by holding empty space before it."""
        position = _segment_index(self.timeline, index)
        try:
            shift = max(0.0, float(lead))
        except (TypeError, ValueError) as exc:
            raise ValueError("The new position must be a number.") from exc
        self.push_undo()
        self.timeline[position]["lead"] = shift
        self._resync()
        return self.timeline[position]

    def copy_segment(self, index: int) -> dict:
        """Store a copy of a timeline clip on the clip clipboard."""
        segment = self.timeline[_segment_index(self.timeline, index)]
        self.clipboard = {
            "path": segment.get("path"),
            "name": segment.get("name"),
            "start": float(segment.get("start") or 0.0),
            "end": float(segment.get("end") or 0.0),
            "layer": int(segment.get("layer") or 0),
            "kind": str(segment.get("kind") or "video"),
            "mute": bool(segment.get("mute")),
            "lead": max(0.0, float(segment.get("lead") or 0.0)),
        }
        return segment

    def paste_segment(self, layer: int | None = None) -> dict:
        """Add the clipboard clip to the end of a layer."""
        if not self.clipboard:
            raise ValueError("Copy a clip first.")
        data = dict(self.clipboard)
        self.push_undo()
        self.add_media(str(data.get("path")))
        segment = {
            "path": data.get("path"),
            "name": data.get("name"),
            "start": float(data.get("start") or 0.0),
            "end": float(data.get("end") or 0.0),
            "layer": int(data.get("layer") if layer is None else layer),
            "kind": str(data.get("kind") or "video"),
        }
        if data.get("mute"):
            segment["mute"] = True
        if segment["kind"] == "audio":
            segment["lead"] = max(0.0, float(data.get("lead") or 0.0))
        self.timeline.append(segment)
        self._resync()
        return segment

    def get_layers(self) -> dict[int, list[dict]]:
        """Return timeline segments grouped by layer, in time order."""
        layers: dict[int, list[dict]] = {}
        for segment in self.timeline:
            layers.setdefault(int(segment.get("layer") or 0), []).append(segment)
        return layers

    def layer_span(self) -> tuple[int, int]:
        """Return the lowest and highest lane index, including empty slots."""
        keys = {int(segment.get("layer") or 0) for segment in self.timeline}
        return min(keys | {0}), max(keys | {max(0, self.layers - 1)})

    def bottom_layer(self) -> int:
        """Return the lowest lane index; audio lanes live below zero."""
        return self.layer_span()[0]

    def top_layer(self) -> int:
        """Return the highest lane index."""
        return self.layer_span()[1]

    def layer_count(self) -> int:
        """Return the number of lanes in the timeline, at least one."""
        low, high = self.layer_span()
        return high - low + 1

    def add_layer(self) -> int:
        """Add an empty layer on top and return its index."""
        self.push_undo()
        index = self.top_layer() + 1
        self.layers = index + 1
        return index

    def remove_layer(self, layer: int) -> bool:
        """Remove an empty top or bottom lane; returns True when one was removed."""
        index = int(layer)
        low, high = self.layer_span()
        if low == high or index not in (low, high):
            return False
        if any(int(segment.get("layer") or 0) == index for segment in self.timeline):
            return False
        self.push_undo()
        if index == high:
            self.layers = max(1, index)
        return True

    def timeline_duration(self) -> float:
        """Return the total timeline length in seconds."""
        return max((_abs_end(segment) for segment in self.timeline), default=0.0)

    def segment_at_time(self, position: float) -> dict | None:
        """Return the topmost segment covering a timeline position."""
        layers = self.get_layers()
        for layer in sorted(layers, reverse=True):
            for segment in layers[layer]:
                if _abs_start(segment) <= position < _abs_end(segment):
                    return segment
        return None

    def _resync(self) -> None:
        """Lay every layer out back-to-back and store absolute starts."""
        cursors: dict[int, float] = {}
        for segment in self.timeline:
            layer = int(segment.get("layer") or 0)
            start = cursors.get(layer, 0.0) + max(0.0, float(segment.get("lead") or 0.0))
            segment["layer"] = layer
            segment["abs_start"] = start
            cursors[layer] = start + _seg_length(segment)

    def render(
        self,
        output: str | Path,
        width: Any = None,
        height: Any = None,
        fps: Any = None,
    ) -> dict:
        """Render the timeline into a single video file."""
        return render_timeline(self.timeline, output, width=width, height=height, fps=fps)

    def save_file(self, path: str | Path) -> Path:
        """Write the project as JSON and return the saved path."""
        target = Path(str(path)).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target

    def set_segment_range(self, index: int, start: Any = None, end: Any = None) -> dict:
        """Update the in and out points of a timeline segment."""
        position = _segment_index(self.timeline, index)
        segment = self.timeline[position]
        start_seconds, end_seconds = _clamp_range(segment, start, end, keep_missing=True)
        self.push_undo()
        segment["start"] = start_seconds
        segment["end"] = end_seconds
        self._resync()
        return segment

    def timeline_state(self) -> dict:
        """Return media and timeline details for display or agent context."""
        segments = []
        for index, segment in enumerate(self.timeline):
            length = _seg_length(segment)
            segments.append(
                {
                    "index": index,
                    "name": segment.get("name") or Path(str(segment.get("path"))).name,
                    "path": segment.get("path"),
                    "start": float(segment.get("start") or 0.0),
                    "end": float(segment.get("end") or 0.0),
                    "duration": length,
                    "layer": int(segment.get("layer") or 0),
                    "kind": str(segment.get("kind") or "video"),
                    "mute": bool(segment.get("mute")),
                    "abs_start": _abs_start(segment),
                    "abs_end": _abs_end(segment),
                }
            )
        total = self.timeline_duration()
        return {
            "media": [
                {
                    "name": clip["name"],
                    "path": clip["path"],
                    "duration": clip["duration"],
                    "size": f"{clip['width']}x{clip['height']}",
                }
                for clip in self.media
            ],
            "timeline": segments,
            "timeline_duration": total,
            "output_dir": str(Path(str(self.media[0]["path"])).parent) if self.media else "",
        }

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation of the project."""
        return {
            "media": [clip["path"] for clip in self.media],
            "layers": max(1, int(self.layers)),
            "timeline": [
                {
                    "path": segment.get("path"),
                    "start": float(segment.get("start") or 0.0),
                    "end": float(segment.get("end") or 0.0),
                    "layer": int(segment.get("layer") or 0),
                    "kind": str(segment.get("kind") or "video"),
                    "mute": bool(segment.get("mute")),
                    "lead": max(0.0, float(segment.get("lead") or 0.0)),
                    "abs_start": _abs_start(segment),
                }
                for segment in self.timeline
            ],
        }


def layer_label(layer: int) -> str:
    """Return the lane name: video layers count up, audio lanes count down."""
    value = int(layer)
    return f"Layer {value}" if value >= 0 else f"Audio {abs(value)}"


def _seg_length(segment: dict) -> float:
    """Return the source-relative length of a segment."""
    return max(
        0.0, float(segment.get("end") or 0.0) - float(segment.get("start") or 0.0)
    )


def _abs_start(segment: dict) -> float:
    """Return a segment's absolute timeline start."""
    return max(0.0, float(segment.get("abs_start") or 0.0))


def _abs_end(segment: dict) -> float:
    """Return a segment's absolute timeline end."""
    return _abs_start(segment) + _seg_length(segment)


def render_timeline(
    segments: list[dict],
    output: str | Path,
    width: Any = None,
    height: Any = None,
    fps: Any = None,
) -> dict:
    """Render timeline layers into one file, overlaying bottom to top."""
    if not segments:
        return error_result("The timeline is empty, add clips before rendering.")
    try:
        ordered, total_time, sources = composite.layout(segments)
    except (ValueError, OSError) as exc:
        return error_result(str(exc))
    if not ordered or total_time <= 0:
        return error_result("The timeline is empty, add clips before rendering.")
    first = ff.video_stream(ordered[0]["info"])
    target_width = ff.even(width) if width not in (None, "") else ff.even(first.get("width", 1280))
    target_height = ff.even(height) if height not in (None, "") else ff.even(first.get("height", 720))
    fps_text = str(fps) if fps not in (None, "") else str(first.get("r_frame_rate") or "30/1")
    out = Path(str(output)).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    parts, base = composite.video_graph(
        ordered, total_time, target_width, target_height, fps_text
    )
    audio_parts, audio_label = composite.audio_graph(ordered, 48000, 2)
    parts += audio_parts
    if audio_label is None:
        parts.append(
            f"anullsrc=channel_layout=stereo:sample_rate=48000:d={total_time:.3f}[aout]"
        )
        audio_label = "aout"

    graph = ";".join(parts)
    args: list[str] = []
    for source in sources:
        args += ["-i", source]
    args += ["-filter_complex", graph, "-map", f"[{base}]", "-map", f"[{audio_label}]"]
    args += ff.encode_args(out)
    args.append(str(out))
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Render failed: {ff.error_message(stderr)}")
    layers = len({item["layer"] for item in ordered})
    return success_result(
        out,
        f"Rendered {len(ordered)} clips on {layers} layer(s) to {out.name}.",
        segments=len(ordered),
        layers=layers,
        size=f"{target_width}x{target_height}",
    )


def _clamp_range(
    clip: dict, start: Any, end: Any, keep_missing: bool = False
) -> tuple[float, float]:
    """Clamp a segment range to the clip duration and validate ordering."""
    in_point = clip.get("start") if keep_missing else None
    out_point = clip.get("end") if keep_missing else None
    start_seconds = ff.parse_time(start) if start not in (None, "") else (in_point or 0.0)
    end_seconds = ff.parse_time(end) if end not in (None, "") else out_point
    duration = float(clip.get("duration") or 0.0)
    if end_seconds is None:
        end_seconds = duration if duration else start_seconds + 1.0
    if duration > 0:
        start_seconds = min(max(0.0, float(start_seconds)), duration)
        end_seconds = min(float(end_seconds), duration)
    if end_seconds <= start_seconds:
        raise ValueError("End time must be greater than start time.")
    return float(start_seconds), float(end_seconds)


def _segment_index(segments: list[dict], index: int) -> int:
    """Validate a timeline index and return it as an integer."""
    try:
        position = int(index)
    except (TypeError, ValueError) as exc:
        raise ValueError("Index must be a whole number.") from exc
    if position < 0 or position >= len(segments):
        raise ValueError(f"Index {position} is outside the timeline (0-{len(segments) - 1}).")
    return position
