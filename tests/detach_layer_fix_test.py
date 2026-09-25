"""Focused test: detached audio must align with the source video on a high layer.

The regression: detaching a video on a high layer (e.g. layer 2) pushed the audio
onto a video lane and to the end of that lane. The fix places audio on a negative
audio lane aligned to the video, creating a new lane underneath when needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.project import Project  # noqa: E402


def _make_project():
    project = Project()
    # Inject a media entry with audio so detach_audio accepts it.
    project.media.append(
        {
            "path": "/fake/sample_a.mp4",
            "name": "sample_a.mp4",
            "duration": 10.0,
            "width": 1280,
            "height": 720,
            "fps": 30,
            "has_audio": True,
            "size_bytes": 1,
        }
    )
    return project


def add_segment(project, layer, start, end):
    project.timeline.append(
        {
            "path": "/fake/sample_a.mp4",
            "name": "sample_a.mp4",
            "start": start,
            "end": end,
            "layer": layer,
            "kind": "video",
            "mute": False,
            "lead": 0.0,
        }
    )
    project._resync()


def test_detach_high_layer_aligned():
    """Video on layer 2 must get audio aligned to it (layer -1), not end of a video lane."""
    project = _make_project()
    # Video on layer 0 (0-5), video on layer 2 (0-5) -> top layer is 2.
    add_segment(project, 0, 0.0, 5.0)
    add_segment(project, 2, 0.0, 5.0)

    video_index = 1  # the layer-2 video
    segment, audio = project.detach_audio(video_index)
    assert str(segment.get("kind")) != "audio"
    assert audio["kind"] == "audio"
    assert segment.get("mute") is True

    # Audio must be on a negative (audio) lane, aligned with the video's abs_start.
    assert audio["layer"] < 0, f"audio on video lane {audio['layer']}"
    assert abs(audio["abs_start"] - segment["abs_start"]) < 0.01, (
        audio, segment
    )
    print("PASS high-layer aligned:", audio["layer"], audio["abs_start"])


def test_detach_two_clips_same_lane():
    """Two videos on layer 0 -> both audio on the same -1 lane, aligned to each."""
    project = _make_project()
    add_segment(project, 0, 0.0, 4.0)
    add_segment(project, 0, 4.0, 8.0)  # back-to-back on same lane
    project.detach_audio(0)
    # After detaching, an audio segment is inserted at index 1, so the second
    # video is now at index 2.
    project.detach_audio(2)
    lanes = {int(s.get("layer") or 0) for s in project.timeline}
    assert -1 in lanes, lanes
    for i, s in enumerate(project.timeline):
        if s.get("kind") != "audio":
            continue
        partner = project.timeline[i - 1]
        assert partner.get("mute") is True, partner
        assert abs(float(s["abs_start"]) - float(partner["abs_start"])) < 0.01, (s, partner)
    print("PASS two clips same lane:", {int(s.get("layer")) for s in project.timeline})


def test_detach_creates_new_lane_when_occupied():
    """When the top audio lane is occupied at the clip's position, create a new one below."""
    project = _make_project()
    # Video A on layer 0, 0-10. Its audio on -1, 0-10.
    add_segment(project, 0, 0.0, 10.0)
    project.detach_audio(0)
    # Now another video on layer 1, 0-10. -1 is occupied at 0, so it must go to -2.
    add_segment(project, 1, 0.0, 10.0)
    # Detached audio was inserted at index 1, so the new video is at index 2.
    segment, audio = project.detach_audio(2)
    assert audio["layer"] < 0
    assert audio["layer"] == -2, f"expected -2, got {audio['layer']}"
    assert abs(audio["abs_start"] - segment["abs_start"]) < 0.01
    print("PASS created new lane:", audio["layer"], audio["abs_start"])


def test_existing_test_contract():
    """Mirror smoke_test.test_detach_audio contract for layer-0 videos."""
    project = _make_project()
    # layer 0: A(0-1) B(0-1) A(0-1)  (each on its own back-to-back slot)
    add_segment(project, 0, 0.0, 1.0)
    add_segment(project, 0, 1.0, 2.0)
    add_segment(project, 0, 2.0, 3.0)
    project.detach_audio(0)
    project.detach_audio(3)
    lanes = {int(s.get("layer") or 0) for s in project.timeline}
    assert lanes == {-1, 0}, lanes
    unmuted = [
        i for i, s in enumerate(project.timeline)
        if s.get("kind") != "audio" and not s.get("mute")
    ]
    assert len(unmuted) == 1, unmuted
    print("PASS existing contract:", lanes, "unmuted indices", unmuted)


if __name__ == "__main__":
    test_detach_high_layer_aligned()
    test_detach_two_clips_same_lane()
    test_detach_creates_new_lane_when_occupied()
    test_existing_test_contract()
    print("All detach-layer tests passed.")
