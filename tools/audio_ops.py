"""Audio operations: volume, mute, extract audio, and add or mix audio."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools import ffmpeg_utils as ff
from tools.results import ensure_file, error_result, success_result

AUDIO_CODECS: dict[str, tuple[list[str], str]] = {
    "mp3": (["-c:a", "libmp3lame", "-q:a", "2"], ".mp3"),
    "wav": (["-c:a", "pcm_s16le"], ".wav"),
    "m4a": (["-c:a", "aac", "-b:a", "192k"], ".m4a"),
    "aac": (["-c:a", "aac", "-b:a", "192k"], ".m4a"),
    "flac": (["-c:a", "flac"], ".flac"),
    "ogg": (["-c:a", "libvorbis", "-q:a", "5"], ".ogg"),
}

_probes: dict[str, dict | None] = {}


def add_audio(
    path: str | Path,
    audio: str | Path,
    output: str | Path,
    mix: bool = False,
    volume: Any = 1.0,
) -> dict:
    """Replace the clip audio track, or mix a new track with the existing one."""
    source = ensure_file(path)
    audio_source = ensure_file(audio)
    out = _prepare_output(output)
    try:
        level = float(volume)
    except (TypeError, ValueError):
        return error_result("Volume must be a number.")
    info = ff.probe(source)
    duration = ff.media_duration(info)
    if mix and ff.has_audio(info):
        graph = (
            f"[0:a]aresample=48000[a0];[1:a]volume={level:g},aresample=48000[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
        args = ["-i", str(source), "-i", str(audio_source)]
        args += ["-filter_complex", graph, "-map", "0:v", "-map", "[a]"]
        args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    else:
        args = ["-i", str(source), "-i", str(audio_source)]
        args += ["-map", "0:v:0", "-map", "1:a:0"]
        args += ["-af", f"volume={level:g}"]
        args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    if duration > 0:
        args += ["-t", f"{duration:.3f}"]
    args.append(str(out))
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Add audio failed: {ff.error_message(stderr)}")
    action = "Mixed" if mix and ff.has_audio(info) else "Replaced"
    return success_result(out, f"{action} audio track, saved {out.name}.")


def adjust_volume(path: str | Path, output: str | Path, volume: Any = 1.0) -> dict:
    """Change audio level; 1.0 is unchanged, 2.0 doubles, 0.5 halves."""
    source = ensure_file(path)
    out = _prepare_output(output)
    try:
        level = float(volume)
    except (TypeError, ValueError):
        return error_result("Volume must be a number.")
    if level < 0:
        return error_result("Volume must be zero or greater.")
    if not ff.has_audio(ff.probe(source)):
        return error_result("This clip has no audio track.")
    args = ["-i", str(source), "-af", f"volume={level:g}", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    args.append(str(out))
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Volume change failed: {ff.error_message(stderr)}")
    return success_result(out, f"Volume set to {level:g}, saved {out.name}.")


def extract_audio(path: str | Path, output: str | Path, format: str = "mp3") -> dict:
    """Extract the audio track from a clip into mp3, wav, m4a, flac, or ogg."""
    source = ensure_file(path)
    choice = str(format or "mp3").strip().lower()
    if choice not in AUDIO_CODECS:
        return error_result(f"Format must be one of: {', '.join(sorted(AUDIO_CODECS))}.")
    if not ff.has_audio(ff.probe(source)):
        return error_result("This clip has no audio track.")
    codec_args, extension = AUDIO_CODECS[choice]
    out = _prepare_output(output)
    if out.suffix.lower() != extension:
        out = out.with_suffix(extension)
    args = ["-i", str(source), "-vn", "-map", "0:a:0", *codec_args, str(out)]
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Extract audio failed: {ff.error_message(stderr)}")
    return success_result(out, f"Audio extracted to {out.name}.", format=choice)


def mute_video(path: str | Path, output: str | Path) -> dict:
    """Remove the audio track from a clip without re-encoding the video."""
    source = ensure_file(path)
    out = _prepare_output(output)
    args = ["-i", str(source), "-map", "0:v:0", "-c:v", "copy", "-an", str(out)]
    ok, stderr = ff.run_ffmpeg(args)
    if not ok:
        return error_result(f"Mute failed: {ff.error_message(stderr)}")
    return success_result(out, f"Audio removed, saved {out.name}.")


def render_audio_range(
    segments: list[dict],
    output: str | Path,
    codec_args: list[str] | None = None,
    sample_rate: int = 44100,
    channels: int = 2,
) -> Path | None:
    """Render trimmed timeline segments into one audio file; None when silent."""
    parts: list[str] = []
    labels: list[str] = []
    args: list[str] = []
    has_audio = False
    layout = "stereo" if channels >= 2 else "mono"
    for index, segment in enumerate(segments):
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or 0.0)
        length = end - start
        if length <= 0.05:
            continue
        info = _probe(segment.get("path"))
        args += ["-i", str(segment.get("path"))]
        if info and ff.has_audio(info):
            has_audio = True
            parts.append(
                f"[{index}:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
                f"aresample={sample_rate},"
                f"aformat=sample_fmts=s16:channel_layouts={layout}[a{index}]"
            )
        else:
            parts.append(
                f"anullsrc=channel_layout={layout}:sample_rate={sample_rate}:"
                f"d={length:.3f}[a{index}]"
            )
        labels.append(f"[a{index}]")
    if not parts or not has_audio:
        return None
    graph = (
        ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[a]"
    )
    target = _prepare_output(output)
    args += ["-filter_complex", graph, "-map", "[a]"]
    args += codec_args or ["-c:a", "pcm_s16le"]
    args.append(str(target))
    ok, _ = ff.run_ffmpeg(args)
    return target if ok and target.exists() else None


def _prepare_output(output: str | Path) -> Path:
    """Resolve the output path and create its parent directory."""
    target = Path(str(output)).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _probe(path: Any) -> dict | None:
    """Probe a media file once and cache the result."""
    key = str(path)
    if key in _probes:
        return _probes[key]
    try:
        info = ff.probe(key)
    except (ValueError, OSError):
        info = None
    _probes[key] = info
    return info
