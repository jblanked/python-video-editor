"""End-to-end smoke test for every editor operation and agent tool.

Run with: venv/bin/python tests/smoke_test.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import context  # noqa: E402
from tools.dispatch import execute_tool  # noqa: E402
from tools.project import Project  # noqa: E402
from tools.registry import OPS, get_openai_tools  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, result: dict, expect_output: bool = True) -> None:
    """Record a pass or fail for one operation result."""
    ok = bool(result.get("success"))
    output = result.get("output")
    if ok and expect_output and output and not Path(str(output)).exists():
        ok = False
        result["message"] = f"output missing: {output}"
    if ok and expect_output and output and Path(str(output)).is_file():
        ffmpeg, _ = context_probe(str(output))
        if not ffmpeg:
            ok = False
            result["message"] = f"output not readable: {output}"
    label = f"{name}: {result.get('message')}"
    if ok:
        PASSED.append(name)
        print(f"[ok ] {label}")
    else:
        FAILED.append(name)
        print(f"[ERR] {label}")


def context_probe(path: str) -> tuple[bool, str]:
    """Return (readable, message) by probing a produced file with ffprobe."""
    try:
        from tools import ffmpeg_utils as ff

        info = ff.probe(path)
        return bool(info.get("streams")), "ok"
    except (ValueError, OSError) as exc:
        return False, str(exc)


def make_media(directory: Path) -> None:
    """Create sample clips, a watermark image, and a subtitle file."""
    run(
        [
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(directory / "sample_a.mp4"),
        ]
    )
    run(
        [
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24:duration=6",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(directory / "sample_b.mp4"),
        ]
    )
    run(
        [
            "-f", "lavfi", "-i", "color=c=red@0.8:s=160x80",
            "-frames:v", "1", str(directory / "watermark.png"),
        ]
    )
    (directory / "captions.srt").write_text(
        "1\n00:00:00,500 --> 00:00:04,500\nSmoke test subtitle\n", encoding="utf-8"
    )
    run(
        [
            "-t", "4", "-i", str(directory / "sample_a.mp4"),
            "-vn", "-c:a", "libmp3lame", str(directory / "voice.mp3"),
        ]
    )


def run(args: list[str]) -> None:
    """Run ffmpeg quietly with the given arguments."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
    )


def test_clip_ops(media: Path, out: Path) -> None:
    """Exercise trim, cut, split, and join."""
    sample = media / "sample_a.mp4"
    check("trim_video", execute_tool("trim_video", {"path": str(sample), "start": "1", "end": "4"}))
    check("trim via duration", execute_tool(
        "trim_video", {"path": str(sample), "start": "2", "duration": "3"}
    ))
    check("cut_video", execute_tool("cut_video", {"path": str(sample), "start": "3", "end": "4"}))
    check("cut_video head", execute_tool("cut_video", {"path": str(sample), "start": "0", "end": "2"}))
    check("split_video", execute_tool("split_video", {"path": str(sample), "count": 3}))
    check("join_videos", execute_tool(
        "join_videos", {"paths": [str(media / "sample_a.mp4"), str(media / "sample_b.mp4")]}
    ))
    check("join_videos copy", execute_tool(
        "join_videos",
        {
            "paths": [
                str(out / "sample_a_trim.mp4"),
                str(out / "sample_a_trim.mp4"),
            ],
            "mode": "copy",
        },
    ))


def test_export_ops(media: Path, out: Path) -> None:
    """Exercise conversion, GIF, thumbnail, and frame extraction."""
    sample = media / "sample_a.mp4"
    short = out / "sample_a_trim.mp4"
    check("convert_video webm", execute_tool(
        "convert_video", {"path": str(short), "format": "webm", "quality": "low"}
    ))
    check("convert_video hevc", execute_tool(
        "convert_video", {"path": str(short), "format": "hevc", "quality": "low"}
    ))
    check("convert_video mkv", execute_tool(
        "convert_video", {"path": str(sample), "format": "mkv", "quality": "medium"}
    ))
    check("create_gif", execute_tool(
        "create_gif", {"path": str(sample), "start": "1", "duration": "2", "width": 320}
    ))
    check("create_thumbnail", execute_tool("create_thumbnail", {"path": str(sample), "time": "2"}))
    check("extract_frames", execute_tool(
        "extract_frames", {"path": str(sample), "mode": "interval", "value": 2.0}
    ), expect_output=False)
    check("extract_frames count", execute_tool(
        "extract_frames", {"path": str(sample), "mode": "count", "value": 4}
    ), expect_output=False)


def test_info_ops(media: Path) -> None:
    """Exercise media inspection."""
    check("get_video_info", execute_tool("get_video_info", {"path": str(media / "sample_a.mp4")}), expect_output=False)


def test_audio_ops(media: Path, out: Path) -> None:
    """Exercise volume, mute, extract, and add audio."""
    sample = media / "sample_a.mp4"
    check("adjust_volume", execute_tool("adjust_volume", {"path": str(sample), "volume": 0.5}))
    check("mute_video", execute_tool("mute_video", {"path": str(sample)}))
    check("extract_audio", execute_tool("extract_audio", {"path": str(sample), "format": "wav"}))
    check("add_audio replace", execute_tool(
        "add_audio", {"path": str(sample), "audio": str(media / "voice.mp3")}
    ))
    check("add_audio mix", execute_tool(
        "add_audio", {"path": str(sample), "audio": str(media / "voice.mp3"), "mix": True}
    ))


def test_overlay_ops(media: Path) -> None:
    """Exercise text, subtitles, and watermark overlays."""
    sample = media / "sample_a.mp4"
    check("add_text", execute_tool(
        "add_text",
        {"path": str(sample), "text": "Hello, world: 100% test", "position": "top-center", "box": True},
    ))
    check("add_subtitles", execute_tool(
        "add_subtitles", {"path": str(sample), "subtitle_file": str(media / "captions.srt")}
    ))
    check("add_watermark", execute_tool(
        "add_watermark",
        {"path": str(sample), "image": str(media / "watermark.png"), "width": 120},
    ))


def test_timeline_ops(media: Path, out: Path) -> None:
    """Exercise timeline editing and rendering through the project."""
    project = Project()
    context.set_project(project)
    check("timeline_add_clip", execute_tool(
        "timeline_add_clip", {"path": str(media / "sample_a.mp4"), "start": "0", "end": "2"}
    ), expect_output=False)
    check("timeline_add_clip 2", execute_tool(
        "timeline_add_clip", {"path": str(media / "sample_b.mp4"), "start": "1", "end": "3"}
    ), expect_output=False)
    check("timeline_add_clip position", execute_tool(
        "timeline_add_clip",
        {"path": str(media / "sample_a.mp4"), "position": 0, "end": "1"},
    ), expect_output=False)
    check("timeline_get_state", execute_tool("timeline_get_state", {}) , expect_output=False)
    check("timeline_trim_clip", execute_tool(
        "timeline_trim_clip", {"index": 0, "start": "0.5", "end": "1.5"}
    ), expect_output=False)
    check("timeline_move_clip", execute_tool(
        "timeline_move_clip", {"index": 0, "new_index": 2}
    ), expect_output=False)
    render_path = out / "timeline_render.mp4"
    check("timeline_render", execute_tool("timeline_render", {"output": str(render_path)}))
    check("timeline_remove_clip", execute_tool("timeline_remove_clip", {"index": 0}), expect_output=False)
    check("timeline_clear", execute_tool("timeline_clear", {}), expect_output=False)
    assert len(project.timeline) == 0, "timeline should be empty after clear"


def test_transform_ops(media: Path, out: Path) -> None:
    """Exercise resize, crop, rotate, flip, speed, reverse, fade, and blur."""
    sample = media / "sample_a.mp4"
    short = out / "sample_a_trim.mp4"
    check("resize_video width", execute_tool("resize_video", {"path": str(sample), "width": 320}))
    check("resize_video fit", execute_tool(
        "resize_video", {"path": str(sample), "width": 480, "height": 480}
    ))
    check("resize_video stretch", execute_tool(
        "resize_video", {"path": str(sample), "width": 640, "height": 480, "mode": "stretch"}
    ))
    check("crop_video", execute_tool(
        "crop_video", {"path": str(sample), "width": 320, "height": 240}
    ))
    check("rotate_video 90", execute_tool("rotate_video", {"path": str(sample), "angle": 90}))
    check("rotate_video 45", execute_tool("rotate_video", {"path": str(short), "angle": 45}))
    check("flip_video", execute_tool("flip_video", {"path": str(sample), "direction": "both"}))
    check("change_speed", execute_tool("change_speed", {"path": str(sample), "factor": 2.0}))
    check("change_speed slow", execute_tool("change_speed", {"path": str(short), "factor": 0.75}))
    check("reverse_video", execute_tool("reverse_video", {"path": str(short)}))
    check("fade_video", execute_tool("fade_video", {"path": str(sample), "fade_in": 1.0, "fade_out": 2.0}))
    check("blur_video", execute_tool("blur_video", {"path": str(sample), "strength": 8}))


def test_dispatch_errors(media: Path) -> None:
    """Exercise dispatch validation and defaults."""
    result = execute_tool("no_such_tool", {})
    assert not result["success"], "unknown tool should fail"
    result = execute_tool("trim_video", {})
    assert not result["success"], "missing path should fail"
    result = execute_tool("flip_video", {"path": str(media / "sample_a.mp4"), "direction": "diagonal"})
    assert not result["success"], "invalid choice should fail"
    result = execute_tool("join_videos", {"paths": [str(media / "sample_a.mp4")]})
    assert not result["success"], "single path join should fail"
    default_result = execute_tool("trim_video", {"path": str(media / "sample_a.mp4"), "start": "1", "end": "2"})
    assert default_result["success"], "default output should work"
    assert Path(default_result["output"]).parent == context.get_output_dir()
    print("[ok ] dispatch validation and defaults")


def main() -> int:
    """Run every operation against generated media and report a summary."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg and ffprobe must be on PATH.")
        return 2
    work_dir = Path(tempfile.mkdtemp(prefix="video_editor_smoke_"))
    media = work_dir / "media"
    out = work_dir / "output"
    media.mkdir(parents=True)
    out.mkdir(parents=True)
    context.set_output_dir(out)
    print(f"work dir: {work_dir}")
    print(f"operations registered: {len(OPS)}, tools exposed: {len(get_openai_tools())}")
    try:
        make_media(media)
        test_info_ops(media)
        test_clip_ops(media, out)
        test_transform_ops(media, out)
        test_audio_ops(media, out)
        test_overlay_ops(media)
        test_export_ops(media, out)
        test_timeline_ops(media, out)
        test_dispatch_errors(media)
    finally:
        context.set_project(None)
    print(f"\npassed: {len(PASSED)}, failed: {len(FAILED)}")
    if FAILED:
        print("failed operations:")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    if os.environ.get("KEEP_SMOKE_FILES") != "1":
        shutil.rmtree(work_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
