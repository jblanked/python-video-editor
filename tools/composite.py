"""Layered filter-graph builder shared by the renderer and the preview."""

from __future__ import annotations

from tools import ffmpeg_utils as ff
from tools.results import ensure_file


def layout(
    segments: list[dict], offset: float = 0.0
) -> tuple[list[dict], float, list[str]]:
    """Lay clips out per layer and return (items, duration, sources).

    Every layer runs back-to-back from zero. Clips are sorted bottom-to-top so
    later overlays win. An offset windows the timeline from a playhead, trimming
    the leading part of any clip that spans the offset.
    """
    items: list[dict] = []
    sources: list[str] = []
    for segment in segments:
        source = ensure_file(segment.get("path"))
        info = ff.probe(source)
        total = ff.media_duration(info)
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or 0.0)
        if total > 0:
            start = min(max(0.0, start), total)
            end = min(end, total)
        if end <= start:
            raise ValueError("A timeline clip has an invalid time range.")
        items.append(
            {
                "index": len(sources),
                "layer": int(segment.get("layer") or 0),
                "kind": str(segment.get("kind") or "video"),
                "mute": bool(segment.get("mute")),
                "lead": max(0.0, float(segment.get("lead") or 0.0)),
                "info": info,
                "start": start,
                "end": end,
            }
        )
        sources.append(str(source))
    cursors: dict[int, float] = {}
    for item in items:
        begin = cursors.get(item["layer"], 0.0) + item["lead"]
        item["abs_start"] = begin
        item["abs_end"] = begin + (item["end"] - item["start"])
        cursors[item["layer"]] = item["abs_end"]
    total_time = max(cursors.values(), default=0.0)
    if offset > 0:
        windowed: list[dict] = []
        for item in items:
            if item["abs_end"] <= offset + 1e-6:
                continue
            overlap = max(0.0, offset - item["abs_start"])
            item["start"] += overlap
            item["abs_start"] = max(0.0, item["abs_start"] - offset)
            windowed.append(item)
        items = windowed
        total_time = max(0.0, total_time - offset)
    ordered = sorted(items, key=lambda entry: (entry["layer"], entry["abs_start"]))
    return ordered, total_time, sources


def video_graph(
    ordered: list[dict],
    total_time: float,
    width: int,
    height: int,
    fps_text: str,
) -> tuple[list[str], str]:
    """Return overlay filter parts and the final video label."""
    parts = [
        f"color=c=black:s={width}x{height}:r={fps_text}:d={total_time:.3f}[canvas]"
    ]
    base = "canvas"
    for pos, item in enumerate(ordered):
        if item["kind"] == "audio":
            continue
        parts.append(
            f"[{item['index']}:v]trim=start={item['start']:.3f}:end={item['end']:.3f},"
            f"setpts=PTS-STARTPTS+{item['abs_start']:.6f}/TB,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps_text}[v{pos}]"
        )
        parts.append(f"[{base}][v{pos}]overlay=0:0:eof_action=pass[comp{pos}]")
        base = f"comp{pos}"
    return parts, base


def audio_graph(
    ordered: list[dict], sample_rate: int = 48000, channels: int = 2
) -> tuple[list[str], str | None]:
    """Return audio filter parts and the mixed label, or None when silent.

    Clips without audio are padded with silence so the mix keeps the timeline
    length, which matters for transcription offsets and preview sync.
    """
    if not any(ff.has_audio(item["info"]) and not item["mute"] for item in ordered):
        return [], None
    layout_name = "stereo" if channels >= 2 else "mono"
    parts: list[str] = []
    labels: list[str] = []
    for pos, item in enumerate(ordered):
        length = item["end"] - item["start"]
        if ff.has_audio(item["info"]) and not item["mute"]:
            delay = int(round(item["abs_start"] * 1000))
            parts.append(
                f"[{item['index']}:a]atrim=start={item['start']:.3f}:end={item['end']:.3f},"
                f"asetpts=PTS-STARTPTS,aresample={sample_rate},"
                f"aformat=sample_fmts=fltp:channel_layouts={layout_name},"
                f"adelay={delay}:all=1[a{pos}]"
            )
        else:
            span = item["abs_start"] + length
            parts.append(
                f"anullsrc=channel_layout={layout_name}:sample_rate={sample_rate}:"
                f"d={span:.3f}[a{pos}]"
            )
        labels.append(f"a{pos}")
    if len(labels) == 1:
        return parts, labels[0]
    joined = "".join(f"[{label}]" for label in labels)
    parts.append(
        f"{joined}amix=inputs={len(labels)}:duration=longest:normalize=0[aout]"
    )
    return parts, "aout"
