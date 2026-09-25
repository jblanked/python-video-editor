"""Model test for cutting a timeline segment at a position.

Run with: venv/bin/python tests/split_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.project import Project  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def make_project_with_clips() -> Project:
    """Build a project with two clips back-to-back on layer 0."""
    project = Project()
    project.timeline = [
        {"path": "a.mp4", "name": "clip_a", "start": 0.0, "end": 4.0, "layer": 0},
        {"path": "b.mp4", "name": "clip_b", "start": 0.0, "end": 2.0, "layer": 0},
    ]
    project._resync()
    return project


def test_split_middle() -> None:
    """Split a 4s clip at the 2s mark into two 2s clips."""
    project = make_project_with_clips()
    first, second = project.split_segment_at(0, 2.0)
    assert first["start"] == 0.0 and first["end"] == 2.0, "first half wrong"
    assert second["start"] == 2.0 and second["end"] == 4.0, "second half wrong"
    assert first["layer"] == 0 and second["layer"] == 0, "layer changed"
    assert first["name"] == "clip_a" and second["name"] == "clip_a", "name lost"
    assert len(project.timeline) == 3, "clip count changed"
    # absolute layout: a[0:2], a[2:4], b[4:6]
    assert abs(project.timeline[0]["abs_start"] - 0.0) < 0.001
    assert abs(project.timeline[1]["abs_start"] - 2.0) < 0.001
    assert abs(project.timeline[2]["abs_start"] - 4.0) < 0.001


def test_split_segment_at_end() -> None:
    """Splitting at a clip's edge is rejected (no zero-length half)."""
    project = make_project_with_clips()
    try:
        project.split_segment_at(0, 0.0)
        raise AssertionError("expected ValueError for cut at start")
    except ValueError:
        pass


def test_split_segment_at_clip_end() -> None:
    """Splitting at a clip's end is rejected (no zero-length half)."""
    project = make_project_with_clips()
    try:
        project.split_segment_at(0, 4.0)
        raise AssertionError("expected ValueError for cut at end")
    except ValueError:
        pass


def test_split_second_clip() -> None:
    """Split the second clip at its own 1s mark (absolute 5s)."""
    project = make_project_with_clips()
    first, second = project.split_segment_at(1, 5.0)
    assert first["name"] == "clip_b"
    assert first["start"] == 0.0 and first["end"] == 1.0
    assert second["start"] == 1.0 and second["end"] == 2.0
    assert len(project.timeline) == 3


def test_split_invalid_index() -> None:
    """Out-of-range index raises ValueError."""
    project = make_project_with_clips()
    try:
        project.split_segment_at(99, 1.0)
        raise AssertionError("expected ValueError for bad index")
    except ValueError:
        pass


def test_split_undo() -> None:
    """A split can be undone back to the original two-clip timeline."""
    project = make_project_with_clips()
    project.split_segment_at(0, 2.0)
    assert len(project.timeline) == 3
    assert project.undo()
    assert len(project.timeline) == 2
    assert project.timeline[0]["end"] == 4.0


def main() -> None:
    """Run the split tests and report results."""
    for func in (
        test_split_middle,
        test_split_segment_at_end,
        test_split_segment_at_clip_end,
        test_split_second_clip,
        test_split_invalid_index,
        test_split_undo,
    ):
        try:
            func()
            PASSED.append(func.__name__)
            print(f"[ok ] {func.__name__}")
        except Exception as exc:  # noqa: B001
            FAILED.append(func.__name__)
            print(f"[ERR] {func.__name__}: {exc}")
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
