"""Project model: media pool, timeline segments, and timeline rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools import ffmpeg_utils as ff
from tools.results import ensure_file, error_result, success_result


class Project:
    """Holds the media pool and timeline segments, with JSON persistence."""

    def __init__(self) -> None:
        """Create an empty project with no media and an empty timeline."""
        self.media: list[dict] = []
        self.timeline: list[dict] = []

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
    ) -> dict:
        """Add a clip segment to the timeline at an optional position."""
        clip = self.add_media(path)
        start_seconds, end_seconds = _clamp_range(clip, start, end)
        segment = {
            "path": clip["path"],
            "name": clip["name"],
            "start": start_seconds,
            "end": end_seconds,
        }
        if position is None:
            self.timeline.append(segment)
        else:
            index = max(0, min(int(position), len(self.timeline)))
            self.timeline.insert(index, segment)
        return segment

    def clear_timeline(self) -> None:
        """Remove every segment from the timeline."""
        self.timeline = []

    def load_file(self, path: str | Path) -> None:
        """Replace the current media pool and timeline from a project file."""
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        self.media = []
        self.timeline = []
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
                }
            )

    def move_segment(self, index: int, new_index: int) -> dict:
        """Move a timeline segment to a new position."""
        segment = self.timeline.pop(_segment_index(self.timeline, index))
        target = max(0, min(int(new_index), len(self.timeline)))
        self.timeline.insert(target, segment)
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
        return self.timeline.pop(_segment_index(self.timeline, index))

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
        segment["start"] = start_seconds
        segment["end"] = end_seconds
        return segment

    def timeline_state(self) -> dict:
        """Return media and timeline details for display or agent context."""
        segments = []
        total = 0.0
        for index, segment in enumerate(self.timeline):
            length = max(0.0, float(segment.get("end") or 0) - float(segment.get("start") or 0))
            total += length
            segments.append(
                {
                    "index": index,
                    "name": segment.get("name") or Path(str(segment.get("path"))).name,
                    "path": segment.get("path"),
                    "start": float(segment.get("start") or 0.0),
                    "end": float(segment.get("end") or 0.0),
                    "duration": length,
                }
            )
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
            "timeline": [
                {
                    "path": segment.get("path"),
                    "start": float(segment.get("start") or 0.0),
                    "end": float(segment.get("end") or 0.0),
                }
                for segment in self.timeline
            ],
        }


def render_timeline(
    segments: list[dict],
    output: str | Path,
    width: Any = None,
    height: Any = None,
    fps: Any = None,
) -> dict:
    """Render timeline segments into a single file through the concat filter."""
    if not segments:
        return error_result("The timeline is empty, add clips before rendering.")
    sources = [ensure_file(segment.get("path")) for segment in segments]
    probes = [ff.probe(item) for item in sources]
    first = ff.video_stream(probes[0])
    target_width = ff.even(width) if width not in (None, "") else ff.even(first.get("width", 1280))
    target_height = ff.even(height) if height not in (None, "") else ff.even(first.get("height", 720))
    fps_text = str(fps) if fps not in (None, "") else str(first.get("r_frame_rate") or "30/1")
    out = Path(str(output)).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    labels: list[str] = []
    for index, (segment, info) in enumerate(zip(segments, probes)):
        total = ff.media_duration(info)
        start = float(segment.get("start") or 0.0)
        end = segment.get("end")
        end = float(end) if end is not None else total
        if total > 0:
            start = min(max(0.0, start), total)
            end = min(end, total)
        if end <= start:
            return error_result(f"Segment {index + 1} has an invalid time range.")
        parts.append(
            f"[{index}:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps_text}"
            f"[v{index}]"
        )
        length = end - start
        if ff.has_audio(info):
            parts.append(
                f"[{index}:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
            )
        else:
            parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000:d={length:.3f}[a{index}]"
            )
        labels.append(f"[v{index}][a{index}]")
    graph = (
        ";".join(parts)
        + ";"
        + "".join(labels)
        + f"concat=n={len(segments)}:v=1:a=1[v][a]"
    )
    args: list[str] = []
    for item in sources:
        args += ["-i", str(item)]
    args += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]"]
    args += ff.encode_args(out)
    args.append(str(out))
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Render failed: {ff.error_message(stderr)}")
    return success_result(
        out,
        f"Rendered {len(segments)} clips to {out.name}.",
        segments=len(segments),
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
