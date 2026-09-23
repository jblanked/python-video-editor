"""GUI smoke test: build the window and exercise view interactions."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import context, paths, preview  # noqa: E402
from tools.registry import find_op  # noqa: E402
from views.app import VideoEditorApp  # noqa: E402
from views.clip_strip import AUDIO_FG  # noqa: E402
from views.op_form import OperationDialog  # noqa: E402
from views.text_dialog import TextDialog  # noqa: E402

SAMPLE = "/tmp/ve_test/sample_a.mp4"
SAMPLE_B = "/tmp/ve_test/sample_b.mp4"


def exercise(app: VideoEditorApp) -> None:
    """Drive the views programmatically to catch wiring errors."""
    root = Path(__file__).resolve().parents[1]
    assert paths.bundle_dir() == root, paths.bundle_dir()
    assert paths.data_dir() == root, paths.data_dir()
    timeline = app._views["timeline"]
    assistant = app._views["assistant"]
    settings = app._views["settings"]

    assert Path(SAMPLE).exists(), "run the operation smoke test media generation first"
    timeline._transcribe()
    assert not timeline._transcribing, "transcribe should not run on an empty timeline"
    assert "Add clips" in timeline.log.get("1.0", "end")
    app.project.add_media(SAMPLE)
    app.project.add_media(SAMPLE_B)
    app.refresh_views()
    assert len(timeline._cards) == 2, "two pool cards expected"

    timeline._select_pool(SAMPLE)
    assert timeline.selected_path == SAMPLE

    # Trim runs through the operation dialog (context menu flow).
    trim_op = find_op("trim_video")
    assert trim_op is not None
    dialog = OperationDialog(
        timeline, trim_op, SAMPLE, on_done=lambda result: timeline._on_operation_done(result, trim_op)
    )
    app.update()
    dialog.form.widgets["start"].set("1")
    dialog.form.widgets["end"].set("3")
    args = dialog.form.collect()
    assert args["start"] == "1" and args["end"] == "3", args
    assert args.get("path") == SAMPLE, args
    assert args.get("output", "").endswith("sample_a_trim.mp4"), args
    dialog.run_now()
    deadline = time.time() + 60
    while time.time() < deadline and dialog.run_button.cget("state") == "disabled":
        app.update()
        time.sleep(0.05)
    assert dialog.run_button.cget("state") == "normal", "trim operation did not finish"
    log_text = timeline.log.get("1.0", "end")
    assert "[OK]" in log_text and "Trimmed clip saved" in log_text, log_text
    assert Path(args.get("output", "")).exists(), args
    dialog.destroy()

    timeline._add_to_timeline()
    assert len(app.project.timeline) == 1
    timeline.refresh()
    assert len(timeline.strip._blocks) == 1, len(timeline.strip._blocks)
    timeline._select(0)
    timeline.in_var.set("0")
    timeline.out_var.set("2")
    timeline._apply_range()
    assert abs(app.project.timeline[0]["end"] - 2) < 0.01

    app.project.add_to_timeline(SAMPLE_B)
    timeline.refresh()
    timeline._select(1)
    timeline._move(-1)
    assert app.project.timeline[0]["path"] == SAMPLE_B
    timeline._remove()
    assert len(app.project.timeline) == 1

    app.project.add_to_timeline(SAMPLE_B)
    app.project.add_to_timeline(SAMPLE)
    app.show_view("timeline")
    timeline.refresh()
    app.update()
    while timeline.winfo_height() <= 1:
        app.update()
        time.sleep(0.02)
    strip = timeline.strip
    blocks = strip._blocks
    assert len(blocks) == 3, f"expected 3 clip blocks, got {len(blocks)}"
    app.update()
    first_block = blocks[0]
    last_block = blocks[2]
    assert first_block.winfo_width() > 1, "clip blocks should be laid out"

    # Layers: add a layer, move a clip up and back, and confirm rows rebuild.
    timeline._add_layer()
    assert app.project.layer_count() == 2, app.project.layer_count()
    timeline.refresh()
    app.update()
    assert len(strip._rows) == 2, f"expected two layer rows, got {len(strip._rows)}"
    timeline._send_to_layer(0, 1)
    on_top = [seg for seg in app.project.timeline if int(seg.get("layer") or 0) == 1]
    assert len(on_top) == 1, "one clip should sit on layer 1"
    timeline._send_to_layer(app.project.timeline.index(on_top[0]), 0)
    assert all(int(seg.get("layer") or 0) == 0 for seg in app.project.timeline)

    # Deterministic order for the menu checks: trimmed clip last.
    app.project.clear_timeline()
    app.project.add_to_timeline(SAMPLE_B)
    app.project.add_to_timeline(SAMPLE)
    app.project.add_to_timeline(SAMPLE)
    app.project.set_segment_range(2, start=0, end=2)
    timeline.refresh()
    app.update()
    order = [segment["path"] for segment in app.project.timeline]
    assert order == [SAMPLE_B, SAMPLE, SAMPLE], order
    timeline.selected_index = 2

    # Right-click menu: operation groups, clip actions, and wired commands.
    menu = timeline._build_context_menu(2)
    entries: dict[str, tuple[str, int]] = {}
    for entry in range(int(menu.index("end") or 0) + 1):
        kind = str(menu.type(entry))
        if kind == "separator":
            continue
        entries[str(menu.entrycget(entry, "label"))] = (kind, entry)
    for group in ("Clip", "Transform", "Audio", "Overlay", "Export", "Info"):
        assert group in entries, f"context menu missing {group}: {list(entries)}"
    for action in (
        "Preview from this clip",
        "Move Left",
        "Move Right",
        "Remove from timeline",
        "Detach Audio",
        "Add New Layer",
        "Move to layer",
    ):
        assert action in entries, f"context menu missing {action}"
    assert entries["Preview from this clip"][0] == "command"
    audio_menu = menu.nametowidget(str(menu.entrycget(entries["Audio"][1], "menu")))
    audio_labels = [
        str(audio_menu.entrycget(index, "label"))
        for index in range(int(audio_menu.index("end") or 0) + 1)
        if audio_menu.type(index) != "separator"
    ]
    assert "Transcribe clip" in audio_labels, audio_labels
    menu.invoke(entries["Move Left"][1])
    assert abs(float(app.project.timeline[1]["end"]) - 2.0) < 0.01, app.project.timeline
    menu_again = timeline._build_context_menu(1)
    menu_again.invoke(entries["Move Right"][1])
    assert abs(float(app.project.timeline[2]["end"]) - 2.0) < 0.01, app.project.timeline
    menu.destroy()
    menu_again.destroy()

    # Trim: drag the right edge of a block to shorten the clip.
    timeline.refresh()
    app.update()
    strip = timeline.strip
    block = strip._by_index[2]
    original_end = float(app.project.timeline[2]["end"])
    edge_x = block.winfo_rootx() + block.winfo_width() - 3
    pixels_per_second = strip._pixels_per_second
    strip._begin_edge_drag(SimpleNamespace(x_root=edge_x), 2, "right")
    strip._edge_drag_motion(SimpleNamespace(x_root=int(edge_x - pixels_per_second)))
    strip._end_edge_drag(SimpleNamespace(x_root=edge_x))
    trimmed_end = float(app.project.timeline[2]["end"])
    assert abs((original_end - trimmed_end) - 1.0) < 0.15, (original_end, trimmed_end)

    # Expanding stops at the source clip duration.
    timeline.refresh()
    app.update()
    strip = timeline.strip
    block = strip._by_index[2]
    edge_x = block.winfo_rootx() + block.winfo_width() - 3
    strip._begin_edge_drag(SimpleNamespace(x_root=edge_x), 2, "right")
    strip._edge_drag_motion(SimpleNamespace(x_root=edge_x + 4000))
    strip._end_edge_drag(SimpleNamespace(x_root=edge_x))
    expanded_end = float(app.project.timeline[2]["end"])
    source_duration = next(
        float(clip["duration"]) for clip in app.project.media if clip["path"] == SAMPLE
    )
    assert abs(expanded_end - source_duration) < 0.1, (expanded_end, source_duration)

    # Zoom changes the strip scale, and the playhead line tracks the playhead.
    strip.set_zoom(20)
    assert strip._width_for(2.0) == 40, strip._width_for(2.0)
    strip.set_zoom(40)
    assert strip._width_for(2.0) == 80, strip._width_for(2.0)
    strip.set_playhead(1.0)
    assert int(strip._playhead_line.place_info()["x"]) >= 0, "playhead line should be placed"
    timeline._seek_to(0.5)
    assert abs(timeline._playhead - 0.5) < 0.05, timeline._playhead

    # Transcript dialog opens with the formatted script.
    timeline._finish_transcribe(
        {
            "success": True,
            "message": "Transcribed 00:07: 1 speech segment",
            "details": {
                "text": "hello world",
                "segments": [],
                "clips": [
                    {"index": 0, "name": "a.mp4", "start": 0.0, "end": 4.0, "text": "hello"}
                ],
                "silence": [{"start": 4.0, "end": 5.0, "duration": 1.0}],
            },
        }
    )
    app.update()
    dialogs = [
        child for child in timeline.winfo_children() if isinstance(child, TextDialog)
    ]
    assert dialogs, "transcript dialog should open"
    assert dialogs[0].title() == "Transcript"
    dialogs[0].destroy()

    # Operation dialog collects arguments for a clip from the context menu flow.
    trim_op = find_op("trim_video")
    assert trim_op is not None
    operation_dialog = OperationDialog(timeline, trim_op, SAMPLE, on_done=lambda result: None)
    app.update()
    operation_dialog.form.widgets["start"].set("1")
    operation_dialog.form.widgets["end"].set("2")
    dialog_args = operation_dialog.form.collect()
    assert dialog_args.get("path") == SAMPLE, dialog_args
    assert dialog_args.get("start") == "1", dialog_args
    operation_dialog.destroy()

    # Trim every segment to one second so embedded playback finishes quickly.
    for index in range(len(app.project.timeline)):
        app.project.set_segment_range(index, start=0, end=1)
    timeline.refresh()
    assert abs(timeline._total_duration() - 3.0) < 0.01, timeline._total_duration()

    if preview.available():
        timeline.mute_var.set(True)
        timeline._play()
        deadline = time.time() + 20
        while time.time() < deadline and timeline._playing:
            app.update()
            time.sleep(0.02)
        assert not timeline._playing, "embedded playback should finish"
        assert timeline._frames_shown > 10, timeline._frames_shown
        assert timeline._picture is not None, "preview should show a frame"
        play_log = timeline.log.get("1.0", "end")
        assert "Preview from" in play_log and "Preview finished." in play_log, play_log
        assert timeline.play_button.cget("state") == "normal"
        assert timeline.stop_button.cget("state") == "disabled"

        # Seek the playhead and confirm the still frame arrives.
        timeline._seek_to(1.5)
        deadline = time.time() + 10
        while time.time() < deadline and timeline._still_pending:
            app.update()
            time.sleep(0.02)
        assert abs(timeline._playhead - 1.5) < 0.05, timeline._playhead
        time_text = timeline.time_label.cget("text")
        assert time_text.startswith("00:01"), time_text
        assert not timeline._still_pending, "still frame should be fetched"

        # Double-clicking a clip previews from that clip.
        timeline._on_strip_play(1)
        assert timeline._playing or timeline._preparing, "double-click should start playback"
        deadline = time.time() + 12
        while time.time() < deadline and (timeline._playing or timeline._preparing):
            app.update()
            time.sleep(0.02)
        assert not timeline._playing, "double-click playback should finish"
        assert abs(timeline._play_start - 1.0) < 0.05, timeline._play_start
    else:
        print("ffmpeg unavailable, skipping preview playback check")

    # Detach Audio moves the clip audio onto its own lane below the clip.
    app.project.clear_timeline()
    app.project.layers = 1
    app.project.add_to_timeline(SAMPLE_B)
    app.project.add_to_timeline(SAMPLE)
    app.project.set_segment_range(0, start=0, end=1)
    app.project.set_segment_range(1, start=0, end=1)
    timeline.refresh()
    app.update()
    toolbar = timeline.video_label.master.master.winfo_children()[0]
    toolbar_labels = [
        str(button.cget("text"))
        for row in toolbar.winfo_children()
        for button in row.winfo_children()
    ]
    assert "Detach Audio" in toolbar_labels, toolbar_labels
    for button in ("Undo", "Redo", "Copy", "Paste"):
        assert button in toolbar_labels, (button, toolbar_labels)
    for row in toolbar.winfo_children():
        assert row.winfo_reqwidth() <= toolbar.winfo_width(), (
            "toolbar row overflows",
            row.winfo_reqwidth(),
            toolbar.winfo_width(),
        )
        for button in row.winfo_children():
            assert button.winfo_viewable(), f"hidden toolbar button: {button.cget('text')}"
    timeline.selected_index = 1
    timeline._detach_audio()
    assert len(app.project.timeline) == 3, app.project.timeline
    muted = app.project.timeline[1]
    audio = app.project.timeline[2]
    assert muted.get("mute") is True, muted
    assert audio.get("kind") == "audio" and int(audio.get("layer") or 0) == -1, audio
    assert abs(float(audio.get("abs_start") or 0.0) - 1.0) < 0.01, audio
    assert app.project.layer_count() == 2, app.project.layer_count()
    assert "Audio 1" in timeline.log.get("1.0", "end")
    app.update()
    assert len(strip._rows) == 2, f"expected a layer row and an audio row, got {len(strip._rows)}"
    audio_block = strip._by_index[2]
    assert audio_block.cget("fg_color") == AUDIO_FG, audio_block.cget("fg_color")
    assert strip.drop_target(0, strip._strip.winfo_rooty() + 100)[0] == -1, "bottom row is the audio lane"
    app.project.remove_segment(2)
    app.project.timeline[1]["mute"] = False
    app.project.clear_timeline()
    app.project.add_to_timeline(SAMPLE_B)
    app.project.add_to_timeline(SAMPLE)
    app.project.add_to_timeline(SAMPLE)
    for index in range(3):
        app.project.set_segment_range(index, start=0, end=1)
    timeline.refresh()
    app.update()

    # Dragging a clip sideways slides it along its lane without changing length.
    block = strip._by_index[0]
    origin_x, origin_y = block.winfo_rootx() + 20, block.winfo_rooty() + 20
    strip._begin_body_drag(SimpleNamespace(x_root=origin_x, y_root=origin_y), 0)
    strip._body_drag_motion(SimpleNamespace(x_root=origin_x + 80, y_root=origin_y))
    assert strip._drag_slide and strip._hover_layer is None, "a drag within one row should slide"
    strip._end_body_drag(SimpleNamespace(x_root=origin_x + 80, y_root=origin_y))
    shifted = app.project.timeline[0]
    assert abs(float(shifted["lead"] or 0.0) - 2.0) < 0.2, shifted
    assert abs(float(shifted["end"]) - float(shifted["start"]) - 1.0) < 0.01, shifted
    timeline._undo()
    assert abs(float(app.project.timeline[0]["lead"] or 0.0)) < 0.01, app.project.timeline[0]
    timeline._redo()
    assert abs(float(app.project.timeline[0]["lead"] or 0.0) - 2.0) < 0.2, app.project.timeline[0]
    timeline.selected_index = 0
    timeline._copy()
    timeline._paste()
    assert len(app.project.timeline) == 4, app.project.timeline
    assert "Pasted" in timeline.log.get("1.0", "end")
    timeline._undo()
    assert len(app.project.timeline) == 3, app.project.timeline

    # Dragging a clip onto another row moves it to that layer, even diagonally.
    timeline._add_layer()
    timeline.refresh()
    app.update()
    dragged = app.project.timeline[0]
    block = strip._by_index[0]
    start_x, start_y = block.winfo_rootx() + 20, block.winfo_rooty() + 20
    target_y = strip._rows[1].winfo_rooty() + 20
    strip._begin_body_drag(SimpleNamespace(x_root=start_x, y_root=start_y), 0)
    strip._body_drag_motion(SimpleNamespace(x_root=start_x + 90, y_root=target_y))
    assert strip._hover_layer == 1, strip._hover_layer
    strip._end_body_drag(SimpleNamespace(x_root=start_x + 90, y_root=target_y))
    assert int(dragged.get("layer") or 0) == 1, dragged
    assert "to Layer 1" in timeline.log.get("1.0", "end")
    timeline._undo()
    assert all(int(segment.get("layer") or 0) == 0 for segment in app.project.timeline), (
        app.project.timeline
    )

    app.project.clear_timeline()
    app.project.layers = 1
    app.project.add_to_timeline(SAMPLE_B)
    app.project.add_to_timeline(SAMPLE)
    app.project.add_to_timeline(SAMPLE)
    for index in range(3):
        app.project.set_segment_range(index, start=0, end=1)
    timeline.refresh()
    app.update()

    # Add All To Timeline appends every pool clip in order.
    before = len(app.project.timeline)
    pool_paths = [clip["path"] for clip in app.project.media]
    timeline._add_all_to_timeline()
    assert len(app.project.timeline) == before + len(pool_paths), app.project.timeline
    added = [item["path"] for item in app.project.timeline[-len(pool_paths):]]
    assert added == pool_paths, added
    assert "Added" in timeline.log.get("1.0", "end")

    # A finished agent tool run refreshes the other views.
    assistant._show_event(
        {"type": "tool_end", "name": "timeline_remove_silence", "result": {"success": True, "message": "Removed 1.0s of silence."}}
    )
    assert len(timeline.strip._blocks) == len(app.project.timeline), (
        len(timeline.strip._blocks),
        len(app.project.timeline),
    )

    app.show_view("assistant")
    assert not hasattr(assistant, "provider_menu"), "AI settings live in Settings only"
    assert not hasattr(assistant, "model_menu"), "AI settings live in Settings only"
    provider_id, _ = assistant._settings_selection()
    assert provider_id, "assistant should resolve a provider from saved settings"
    assistant.refresh()
    assert "Settings" in assistant.model_status.cget("text")
    original_prefs = dict(app.preferences)
    app.update_preferences(
        provider=provider_id,
        model="sync-check",
        transcribe_provider=provider_id,
        transcribe_model="whisper-x",
    )
    assert "sync-check" in assistant.model_status.cget("text")
    assert context.get_provider_id() == provider_id, context.get_provider_id()
    assert context.get_transcribe_provider() == provider_id
    assert context.get_transcribe_model() == "whisper-x"
    app.update_preferences(
        provider=str(original_prefs.get("provider") or ""),
        model=str(original_prefs.get("model") or ""),
        transcribe_provider=str(original_prefs.get("transcribe_provider") or ""),
        transcribe_model=str(original_prefs.get("transcribe_model") or ""),
    )
    assert context.get_provider_id() == (original_prefs.get("provider") or None)
    assistant.input.insert("1.0", "trim the clip")
    assert assistant.input.get("1.0", "end").strip() == "trim the clip"
    assistant._clear_chat()

    app.show_view("settings")
    assert settings.provider_menu.get(), "settings should select a provider"
    assert settings.model_menu.get(), "settings should select a model"
    assert settings.transcribe_menu.get() == "Same as AI provider", settings.transcribe_menu.get()
    settings._detect_ffmpeg()
    assert settings.ffmpeg_var.get().endswith("ffmpeg"), settings.ffmpeg_var.get()
    settings._check_binary()
    settings._save_output()

    app.show_view("timeline")
    print("GUI interactions OK")


def main() -> int:
    """Instantiate the window, exercise it, then close."""
    app = VideoEditorApp()
    failures: list[str] = []

    def run() -> None:
        try:
            exercise(app)
        except AssertionError as exc:
            failures.append(str(exc))
            print(f"GUI FAILURE: {exc}")
        except (AttributeError, KeyError, ValueError, OSError) as exc:
            failures.append(repr(exc))
            print(f"GUI ERROR: {exc!r}")
        finally:
            app.after(300, app.destroy)

    app.after(600, run)
    app.mainloop()
    if failures:
        return 1
    print("GUI smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
