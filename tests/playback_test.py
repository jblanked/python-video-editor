"""Preview engine test: audio track, frame pipes, stills, and the player.

Runs headless. Run with: venv/bin/python tests/playback_test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import preview  # noqa: E402

SAMPLE_A = Path("/tmp/ve_test/sample_a.mp4")
SAMPLE_B = Path("/tmp/ve_test/sample_b.mp4")
WORK_DIR = Path("/tmp/ve_test/playback")
FRAME_BYTES = preview.PREVIEW_WIDTH * preview.PREVIEW_HEIGHT * 3


def main() -> int:
    """Exercise audio extraction, frame decoding, stills, and playback timing."""
    if not preview.available():
        print("ffmpeg not available, skipping preview test")
        return 0
    for path in (SAMPLE_A, SAMPLE_B):
        if not path.exists():
            print("sample media missing, create files in /tmp/ve_test first")
            return 2
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    segments = [
        {"path": str(SAMPLE_A), "name": "a", "start": 1.0, "end": 3.0},
        {"path": str(SAMPLE_B), "name": "b", "start": 0.0, "end": 1.0},
    ]
    track = preview.audio_track(segments, WORK_DIR / "range.wav")
    assert track is not None and track.exists(), "audio track should be built"
    print(f"audio track: {track.name} ({track.stat().st_size / 1024:.0f} KB)")

    silent = preview.audio_track(
        [{"path": str(SAMPLE_B), "name": "b", "start": 0.0, "end": 1.0}],
        WORK_DIR / "silent.wav",
    )
    assert silent is None, "a clip without audio should produce no track"
    print("silent clip detected")

    process = preview.frame_pipe(str(SAMPLE_A), 1.0, 3.0)
    assert process is not None and process.stdout is not None, "frame pipe should start"
    count = 0
    while True:
        data = process.stdout.read(FRAME_BYTES)
        if len(data) < FRAME_BYTES:
            break
        count += 1
    process.wait(timeout=10)
    expected = int(2.0 * preview.PREVIEW_FPS)
    assert abs(count - expected) <= 3, f"expected about {expected} frames, got {count}"
    print(f"frame pipe: {count} frames at {preview.PREVIEW_WIDTH}x{preview.PREVIEW_HEIGHT}")

    image = preview.still_frame(str(SAMPLE_B), 1.0)
    assert image is not None, "still frame should decode"
    assert image.size == (preview.PREVIEW_WIDTH, preview.PREVIEW_HEIGHT), image.size
    print(f"still frame: {image.size[0]}x{image.size[1]}")

    player = preview.PreviewPlayer()
    player.load(segments)
    player.start(0.0, muted=False, audio_path=track)
    frames = 0
    deadline = time.time() + 4.0
    while time.time() < deadline:
        result = player.poll()
        if result is not None and result[1] is not None:
            frames += 1
        time.sleep(0.01)
    position = player.position()
    player.stop()
    assert frames > 10, f"expected decoded frames, got {frames}"
    assert 0.5 < position <= 4.0, f"clock should advance in real time, got {position}"
    print(f"audio-synced playback: {frames} frames, position {position:.2f}s")

    player.load(
        [
            {"path": str(SAMPLE_A), "name": "a", "start": 0.0, "end": 1.0},
            {"path": str(SAMPLE_B), "name": "b", "start": 0.0, "end": 1.0},
        ]
    )
    player.start(0.0, muted=True)
    deadline = time.time() + 8.0
    while time.time() < deadline and not player.finished:
        player.poll()
        time.sleep(0.01)
    assert player.finished, "muted playback should finish both segments"
    stopped = player.stop()
    assert not player.playing, "player should stop cleanly"
    print(f"muted playback finished, stopped at {stopped:.2f}s")

    print("preview engine test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

