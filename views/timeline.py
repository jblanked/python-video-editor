"""Timeline view: arrange, preview, and render the project sequence."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Any

import customtkinter as ctk
from PIL import Image

from tools import context, preview, transcribe_ops
from tools.registry import Op, get_ops
from views.clip_strip import ZOOM_LEVELS, ClipStrip
from views.op_form import OperationDialog
from views.text_dialog import TextDialog

PROJECT_TYPES = [
    ("Video editor projects", "*.veproj *.json"),
    ("All files", "*.*"),
]
VIDEO_TYPES = [
    ("Video files", "*.mp4 *.mov *.m4v *.mkv *.avi *.webm *.gif *.wmv *.flv"),
    ("All files", "*.*"),
]
TICK_MS = 25
PREPARE_POLL_MS = 80
STILL_POLL_MS = 60


class TimelineView(ctk.CTkFrame):
    """Horizontal sequence editor with clip trimming, preview playback, and rendering."""

    def __init__(self, master: Any, app: Any) -> None:
        """Create the timeline view and load the current project."""
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.selected_index: int | None = None
        self._last_output: str | None = None
        self._player = preview.PreviewPlayer()
        self._playing = False
        self._preparing = False
        self._prepared_audio: Path | None = None
        self._audio_ready = False
        self._playhead = 0.0
        self._play_start = 0.0
        self._frames_shown = 0
        self._picture: ctk.CTkImage | None = None
        self._still_token = 0
        self._still_applied = 0
        self._still_result: tuple[int, Image.Image | None] | None = None
        self._still_pending = False
        self._updating_playhead = False
        self._transcribing = False
        self._build_ui()
        self.refresh()
        self.app.bind_all("<space>", self._on_space)

    def refresh(self) -> None:
        """Rebuild the clip strip and transport from the project timeline."""
        timeline = self.app.project.timeline
        if self.selected_index is not None and self.selected_index >= len(timeline):
            self.selected_index = None
        total = self._total_duration()
        self.strip.refresh()
        self.strip.select(self.selected_index)
        self.total_label.configure(
            text=(
                f"{len(timeline)} clip(s)  |  total {_duration_text(total)}  |  "
                "drag clips to reorder, drag an edge to trim, double-click to preview"
            )
        )
        if self._playing or self._preparing:
            self._halt()
        self.playhead_slider.configure(to=max(total, 0.001))
        self._playhead = min(self._playhead, total)
        self._update_playhead_ui(self._playhead)
        if not timeline and self._picture is None:
            self.video_label.configure(text="No preview yet - press Play")

    def shutdown(self) -> None:
        """Stop preview playback when the application closes."""
        self._still_token += 1
        self._still_pending = False
        self._player.stop()
        self._playing = False
        self._preparing = False

    def _add_clip(self) -> None:
        """Add a clip from disk to the end of the timeline."""
        path = filedialog.askopenfilename(title="Add clip to timeline", filetypes=VIDEO_TYPES)
        if not path:
            return
        try:
            segment = self.app.project.add_to_timeline(path)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot add clip: {exc}")
            return
        self._log(f"Added {segment['name']} to the timeline.")
        self.app.refresh_views()

    def _apply_range(self) -> None:
        """Apply the in and out fields to the selected segment."""
        if self.selected_index is None:
            self._log("Select a timeline clip first.")
            return
        try:
            segment = self.app.project.set_segment_range(
                self.selected_index,
                start=self.in_var.get(),
                end=self.out_var.get(),
            )
        except (ValueError, OSError) as exc:
            self._log(f"Cannot apply range: {exc}")
            return
        self._log(f"{segment['name']} set to {segment['start']:.2f}s - {segment['end']:.2f}s.")
        self.app.refresh_views()

    def _build_context_menu(self, index: int) -> tk.Menu:
        """Build the right-click menu for a clip block."""
        menu = tk.Menu(self, tearoff=0)
        timeline = self.app.project.timeline
        if not (0 <= index < len(timeline)):
            return menu
        name = timeline[index].get("name", "clip")
        menu.add_command(label=f"Clip {index + 1}: {name}", state="disabled")
        menu.add_separator()
        for group in ("Clip", "Transform", "Audio", "Overlay", "Export", "Info"):
            ops = [op for op in get_ops(group=group) if op.scope in {"clip", "info"}]
            if not ops:
                continue
            submenu = tk.Menu(menu, tearoff=0)
            for op in ops:
                submenu.add_command(
                    label=op.label,
                    command=lambda item=op, position=index: self._open_operation(item, position),
                )
            menu.add_cascade(label=group, menu=submenu)
        menu.add_separator()
        menu.add_command(label="Preview from this clip", command=lambda: self._play(start_index=index))
        menu.add_command(label="Move Left", command=lambda: self._move_clip(index, -1))
        menu.add_command(label="Move Right", command=lambda: self._move_clip(index, 1))
        menu.add_separator()
        menu.add_command(label="Remove from timeline", command=lambda: self._remove_clip(index))
        return menu

    def _build_ui(self) -> None:
        """Create the toolbar, preview player, clip strip, and render controls."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(7, weight=1)
        toolbar = ctk.CTkFrame(self)
        toolbar.grid(row=0, column=0, sticky="ew")
        for text, command in (
            ("New", self._new_project),
            ("Open", self._open_project),
            ("Save", self._save_project),
            ("Add Clip", self._add_clip),
            ("Remove", self._remove),
            ("Move Left", lambda: self._move(-1)),
            ("Move Right", lambda: self._move(1)),
            ("Transcribe", self._transcribe),
            ("Clear", self._clear),
        ):
            ctk.CTkButton(toolbar, text=text, width=86, command=command).pack(side="left", padx=3, pady=6)
        self.total_label = ctk.CTkLabel(self, text="", anchor="w")
        self.total_label.grid(row=1, column=0, sticky="ew", padx=6, pady=(4, 2))

        player_frame = ctk.CTkFrame(self)
        player_frame.grid(row=2, column=0, sticky="ew", pady=(2, 0))
        self.video_label = ctk.CTkLabel(
            player_frame,
            text="No preview yet - press Play",
            width=preview.PREVIEW_WIDTH,
            height=preview.PREVIEW_HEIGHT,
            fg_color="#000000",
            corner_radius=6,
        )
        self.video_label.pack(padx=8, pady=(8, 4))
        transport = ctk.CTkFrame(player_frame, fg_color="transparent")
        transport.pack(fill="x", padx=8, pady=(0, 8))
        self.play_button = ctk.CTkButton(transport, text="Play", width=76, command=self._toggle_play)
        self.play_button.pack(side="left")
        self.stop_button = ctk.CTkButton(
            transport, text="Stop", width=70, state="disabled", command=self._stop_play
        )
        self.stop_button.pack(side="left", padx=6)
        self.time_label = ctk.CTkLabel(transport, text="00:00.0 / 00:00.0", width=132)
        self.time_label.pack(side="left", padx=(4, 10))
        self.playhead_slider = ctk.CTkSlider(transport, from_=0, to=1, command=self._on_slider)
        self.playhead_slider.set(0)
        self.playhead_slider.pack(side="left", fill="x", expand=True)
        self.mute_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(transport, text="Mute", variable=self.mute_var, width=72).pack(
            side="left", padx=(10, 0)
        )

        strip_area = ctk.CTkFrame(self, fg_color="transparent")
        strip_area.grid(row=3, column=0, sticky="ew", padx=4, pady=(6, 0))
        ctk.CTkLabel(strip_area, text="Zoom", text_color=("gray40", "gray60")).pack(
            side="left", padx=(0, 4)
        )
        self.zoom_menu = ctk.CTkOptionMenu(
            strip_area,
            values=[str(level) for level in ZOOM_LEVELS],
            width=72,
            command=self._on_zoom_change,
        )
        self.zoom_menu.set(str(ZOOM_LEVELS[2]))
        self.zoom_menu.pack(side="left", padx=(0, 6))
        self.strip = ClipStrip(
            strip_area,
            self.app.project,
            on_select=self._on_strip_select,
            on_change=self._on_strip_change,
            on_play=self._on_strip_play,
            on_context=self._on_strip_context,
        )
        self.strip.pack(side="left", fill="x", expand=True)

        edit = ctk.CTkFrame(self)
        edit.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        ctk.CTkLabel(edit, text="In", width=22).pack(side="left", padx=(8, 2), pady=8)
        self.in_var = tk.StringVar()
        ctk.CTkEntry(edit, textvariable=self.in_var, width=90).pack(side="left")
        ctk.CTkLabel(edit, text="Out", width=30).pack(side="left", padx=(10, 2))
        self.out_var = tk.StringVar()
        ctk.CTkEntry(edit, textvariable=self.out_var, width=90).pack(side="left")
        ctk.CTkButton(edit, text="Apply", width=80, command=self._apply_range).pack(side="left", padx=10)
        ctk.CTkLabel(edit, text="Times accept seconds, MM:SS, or HH:MM:SS").pack(side="left", padx=6)

        render = ctk.CTkFrame(self)
        render.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        ctk.CTkLabel(render, text="Output").pack(side="left", padx=(8, 4), pady=8)
        self.output_var = tk.StringVar(value=str(context.get_output_dir() / "timeline.mp4"))
        ctk.CTkEntry(render, textvariable=self.output_var).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(render, text="...", width=30, command=self._browse_output).pack(side="left", padx=4)
        self.render_button = ctk.CTkButton(render, text="Render Timeline", width=130, command=self._render)
        self.render_button.pack(side="left", padx=4)
        self.open_button = ctk.CTkButton(
            render, text="Open Result", width=100, state="disabled", command=self._open_output
        )
        self.open_button.pack(side="left", padx=(0, 8))
        self.progress = ctk.CTkProgressBar(self, mode="indeterminate")
        self.progress.grid(row=6, column=0, sticky="ew", pady=(6, 2))
        self.log = ctk.CTkTextbox(self, height=80, wrap="word", state="disabled")
        self.log.grid(row=7, column=0, sticky="nsew", padx=4, pady=(2, 4))

    def _browse_output(self) -> None:
        """Pick an output file for the rendered timeline."""
        chosen = filedialog.asksaveasfilename(
            title="Render timeline",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")],
        )
        if chosen:
            self.output_var.set(chosen)

    def _clear(self) -> None:
        """Remove every segment from the timeline."""
        self.app.project.clear_timeline()
        self.selected_index = None
        self._log("Timeline cleared.")
        self.app.refresh_views()

    def _finish_render(self, result: dict) -> None:
        """Report the render result and update the view."""
        self.progress.stop()
        self.render_button.configure(state="normal")
        prefix = "OK" if result.get("success") else "FAILED"
        self._log(f"[{prefix}] {result.get('message')}")
        output = result.get("output")
        if output and result.get("success"):
            self._last_output = str(output)
            self.open_button.configure(state="normal")
            self._log(f"Output: {output}")

    def _log(self, message: str) -> None:
        """Append a line to the render log."""
        self.log.configure(state="normal")
        self.log.insert("end", f"{message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _move(self, offset: int) -> None:
        """Move the selected segment up or down the timeline."""
        if self.selected_index is None:
            self._log("Select a timeline clip first.")
            return
        target = self.selected_index + offset
        try:
            self.app.project.move_segment(self.selected_index, target)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot move clip: {exc}")
            return
        self.selected_index = max(0, target)
        self.app.refresh_views()

    def _move_clip(self, index: int, offset: int) -> None:
        """Move a clip from the context menu."""
        try:
            self.app.project.move_segment(index, index + offset)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot move clip: {exc}")
            return
        self.selected_index = max(0, index + offset)
        self.app.refresh_views()

    def _new_project(self) -> None:
        """Start a fresh project with an empty pool and timeline."""
        self.app.project.media.clear()
        self.app.project.clear_timeline()
        self.selected_index = None
        self._log("New project started.")
        self.app.refresh_views()

    def _on_space(self, _event: Any = None) -> str | None:
        """Toggle playback with the space bar while this view is visible."""
        if not self.winfo_ismapped():
            return None
        try:
            focused = self.app.focus_get()
        except tk.TclError:
            focused = None
        if isinstance(focused, (tk.Entry, tk.Text)):
            return None
        self._toggle_play()
        return "break"

    def _open_operation(self, op: Op, index: int) -> None:
        """Open the operation dialog for a clip block."""
        timeline = self.app.project.timeline
        if not (0 <= index < len(timeline)):
            return
        source = str(timeline[index].get("path") or "")
        if not source:
            return
        OperationDialog(
            self, op, source, on_done=lambda result: self._on_operation_done(result, op)
        )

    def _open_output(self) -> None:
        """Open the rendered file with the system default app."""
        if self._last_output:
            self.app.open_path(self._last_output)

    def _open_project(self) -> None:
        """Load a project file from disk."""
        path = filedialog.askopenfilename(title="Open project", filetypes=PROJECT_TYPES)
        if not path:
            return
        try:
            self.app.project.load_file(path)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot open project: {exc}")
            return
        self.selected_index = None
        self._log(f"Opened project {Path(path).name}.")
        self.app.refresh_views()

    def _play(self, start_index: int | None = None) -> None:
        """Start the in-app preview from the playhead or from a clip index."""
        if self._playing or self._preparing:
            return
        if not self.app.project.timeline:
            self._log("Add clips to the timeline before playing.")
            return
        if not preview.available():
            self._log("ffmpeg was not found, preview playback is unavailable.")
            return
        if start_index is not None:
            spans = self._spans()
            if 0 <= start_index < len(spans):
                self._playhead = spans[start_index][0]
        if self._playhead >= self._total_duration() - 0.05:
            self._playhead = 0.0
        self._play_start = self._playhead
        self._update_playhead_ui(self._playhead)
        segments = self._range_from(self._playhead)
        if not segments:
            self._log("Nothing to play from this position.")
            return
        if self.mute_var.get():
            self._start_player(segments, None)
            return
        self._preparing = True
        self._audio_ready = False
        self._prepared_audio = None
        self.play_button.configure(state="disabled", text="Prepare")
        self._log("Preparing preview audio...")
        threading.Thread(target=self._prepare_audio, args=(segments,), daemon=True).start()
        self.after(PREPARE_POLL_MS, self._poll_prepare)

    def _poll_prepare(self) -> None:
        """Start playback once the preview audio track is ready."""
        if not self._preparing:
            return
        if not self._audio_ready:
            self.after(PREPARE_POLL_MS, self._poll_prepare)
            return
        self._preparing = False
        audio_path = self._prepared_audio
        self._prepared_audio = None
        self._start_player(self._range_from(self._playhead), audio_path)

    def _prepare_audio(self, segments: list[dict]) -> None:
        """Render the preview audio track off the UI thread."""
        try:
            self._prepared_audio = preview.audio_track(segments)
        except (OSError, ValueError):
            self._prepared_audio = None
        self._audio_ready = True

    def _start_player(self, segments: list[dict], audio_path: Path | None) -> None:
        """Start the embedded preview player at the current playhead."""
        if not segments:
            return
        self._player.load(segments)
        muted = bool(self.mute_var.get()) or audio_path is None
        self._player.start(self._playhead, muted=muted, audio_path=audio_path)
        self._playing = True
        self.play_button.configure(state="normal", text="Pause")
        self.stop_button.configure(state="normal")
        self._log(f"Preview from {_time_text(self._playhead)}.")
        self.after(TICK_MS, self._poll_playback)

    def _poll_playback(self) -> None:
        """Show due frames, advance the playhead, and finish when decoding ends."""
        if not self._playing:
            return
        result = self._player.poll()
        if result is not None:
            position, data = result
            if data is not None:
                self._show_frame(data)
            self._playhead = position
            self._update_playhead_ui(position)
        if self._player.finished:
            self._finish_playback()
            return
        self.after(TICK_MS, self._poll_playback)

    def _display_image(self, image: Image.Image) -> None:
        """Show a decoded frame in the preview area."""
        picture = ctk.CTkImage(
            light_image=image,
            dark_image=image,
            size=(preview.PREVIEW_WIDTH, preview.PREVIEW_HEIGHT),
        )
        self._picture = picture
        self.video_label.configure(image=picture, text="")

    def _finish_playback(self) -> None:
        """Stop at the end of the range and reset the transport."""
        if not self._playing:
            return
        self._playhead = self._player.position()
        self._player.stop()
        self._playing = False
        self.play_button.configure(state="normal", text="Play")
        self.stop_button.configure(state="disabled")
        self._update_playhead_ui(self._playhead)
        self._log("Preview finished.")

    def _halt(self) -> None:
        """Stop playback without moving the playhead."""
        self._player.stop()
        self._playing = False
        self._preparing = False
        self._audio_ready = False
        self.play_button.configure(state="normal", text="Play")
        self.stop_button.configure(state="disabled")

    def _locate(self, position: float) -> tuple[int, float] | None:
        """Return the segment index and offset for a timeline position."""
        spans = self._spans()
        for index, (start, end, _segment) in enumerate(spans):
            if position < end - 1e-6 or index == len(spans) - 1:
                return index, max(0.0, position - start)
        return None

    def _on_operation_done(self, result: dict, op: Op | None = None) -> None:
        """Log the result of an operation run from the context menu."""
        prefix = "OK" if result.get("success") else "FAILED"
        self._log(f"[{prefix}] {result.get('message')}")
        output = result.get("output")
        if output:
            self._log(f"Output: {output}")
        if op is not None and op.name.startswith("transcribe") and result.get("success"):
            self._show_transcript(result)
        self.app.refresh_views()

    def _on_slider(self, value: float) -> None:
        """Seek the playhead when the slider is dragged."""
        if self._updating_playhead:
            return
        self._seek_to(value)

    def _on_strip_change(self, message: str, select: int | None) -> None:
        """Apply a strip edit, then refresh the whole view."""
        if message:
            self._log(message)
        if select is not None:
            self.selected_index = select
        self.app.refresh_views()

    def _on_strip_select(self, index: int) -> None:
        """Select a clip that was clicked in the strip."""
        if 0 <= index < len(self.app.project.timeline):
            self._select(index)

    def _on_strip_context(self, index: int, event: Any) -> None:
        """Show the clip context menu at the pointer."""
        menu = self._build_context_menu(index)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_strip_play(self, index: int) -> None:
        """Preview playback from a clip that was double-clicked."""
        if 0 <= index < len(self.app.project.timeline):
            self._play(start_index=index)

    def _on_zoom_change(self, level: str) -> None:
        """Rescale the clip strip to a new zoom level."""
        self.strip.set_zoom(level)

    def _pause_play(self) -> None:
        """Pause playback at the current position."""
        if not self._playing:
            return
        self._playhead = self._player.stop()
        self._playing = False
        self.play_button.configure(state="normal", text="Play")
        self.stop_button.configure(state="disabled")
        self._update_playhead_ui(self._playhead)

    def _poll_still(self) -> None:
        """Show the newest still frame once it has been decoded."""
        if not self._still_pending:
            return
        result = self._still_result
        if result is not None:
            self._still_result = None
            token, image = result
            self._still_applied = token
            if token == self._still_token and image is not None:
                self._display_image(image)
        if self._still_applied >= self._still_token:
            self._still_pending = False
            return
        self.after(STILL_POLL_MS, self._poll_still)

    def _range_from(self, position: float) -> list[dict]:
        """Return the segments covering playback from a timeline position."""
        located = self._locate(position)
        if located is None:
            return []
        index, offset = located
        spans = self._spans()
        segments = [_segment_copy(spans[index][2], offset)]
        segments += [
            _segment_copy(segment) for _start, _end, segment in spans[index + 1 :]
        ]
        return segments

    def _request_still(self, position: float) -> None:
        """Request a preview frame for a timeline position."""
        located = self._locate(position)
        if located is None:
            return
        index, offset = located
        segment = self.app.project.timeline[index]
        source_time = float(segment.get("start") or 0.0) + offset
        self._still_token += 1
        if not self._still_pending:
            self._still_pending = True
            self.after(STILL_POLL_MS, self._poll_still)
        threading.Thread(
            target=self._still_worker,
            args=(self._still_token, str(segment.get("path")), source_time),
            daemon=True,
        ).start()

    def _seek_to(self, position: float) -> None:
        """Move the playhead to a timeline position and show that frame."""
        if self._playing:
            self._pause_play()
        if self._preparing:
            self._preparing = False
            self._audio_ready = False
            self.play_button.configure(state="normal", text="Play")
        self._playhead = max(0.0, min(float(position), self._total_duration()))
        self._update_playhead_ui(self._playhead)
        self._request_still(self._playhead)

    def _show_frame(self, data: bytes) -> None:
        """Convert raw rgb24 frame bytes and show them in the preview."""
        image = Image.frombytes("RGB", (preview.PREVIEW_WIDTH, preview.PREVIEW_HEIGHT), data)
        self._frames_shown += 1
        self._display_image(image)

    def _spans(self) -> list[tuple[float, float, dict]]:
        """Return each timeline segment with its start and end time."""
        spans = []
        position = 0.0
        for segment in self.app.project.timeline:
            length = max(
                0.0, float(segment.get("end") or 0.0) - float(segment.get("start") or 0.0)
            )
            spans.append((position, position + length, segment))
            position += length
        return spans

    def _still_worker(self, token: int, path: str, source_time: float) -> None:
        """Grab a still frame off the UI thread."""
        image = preview.still_frame(path, source_time)
        self._still_result = (token, image)

    def _stop_play(self) -> None:
        """Stop playback and return the playhead to where it started."""
        if self._preparing:
            self._preparing = False
            self._audio_ready = False
            self.play_button.configure(state="normal", text="Play")
            self._log("Preview cancelled.")
            return
        if not self._playing:
            return
        self._player.stop()
        self._playing = False
        self.play_button.configure(state="normal", text="Play")
        self.stop_button.configure(state="disabled")
        self._playhead = self._play_start
        self._update_playhead_ui(self._playhead)
        self._request_still(self._playhead)
        self._log("Preview stopped.")

    def _toggle_play(self) -> None:
        """Play from the playhead, or pause when playback is running."""
        if self._playing:
            self._pause_play()
        else:
            self._play()

    def _total_duration(self) -> float:
        """Return the total playable duration of the timeline."""
        return sum(
            max(0.0, float(item.get("end") or 0.0) - float(item.get("start") or 0.0))
            for item in self.app.project.timeline
        )

    def _transcribe(self) -> None:
        """Start transcription of the timeline in a worker thread."""
        if self._transcribing:
            self._log("Transcription already running...")
            return
        if not self.app.project.timeline:
            self._log("Add clips to the timeline first.")
            return
        self._transcribing = True
        self._log("Transcribing timeline... this can take a moment.")
        threading.Thread(target=self._transcribe_worker, daemon=True).start()

    def _transcribe_worker(self) -> None:
        """Run the transcription tool off the UI thread."""
        result = transcribe_ops.transcribe_timeline()
        self.after(0, lambda: self._finish_transcribe(result))

    def _finish_transcribe(self, result: dict) -> None:
        """Report the transcript and show the script window."""
        self._transcribing = False
        self._log(str(result.get("message") or "Transcription finished."))
        if result.get("success"):
            self._show_transcript(result)

    def _show_transcript(self, result: dict) -> None:
        """Open the script window for a finished transcription."""
        text = transcribe_ops.transcript_text(result.get("details") or {})
        output = result.get("output")
        if output:
            text = f"Saved to {output}\n\n{text}"
        TextDialog(self, "Transcript", text)

    def _update_playhead_ui(self, position: float) -> None:
        """Sync the playhead slider, strip line, and time label."""
        total = self._total_duration()
        position = max(0.0, min(position, total))
        self._updating_playhead = True
        try:
            self.playhead_slider.set(position)
        finally:
            self._updating_playhead = False
        self.strip.set_playhead(position)
        self.time_label.configure(text=f"{_time_text(position)} / {_time_text(total)}")

    def _remove(self) -> None:
        """Remove the selected segment from the timeline."""
        if self.selected_index is None:
            self._log("Select a timeline clip first.")
            return
        try:
            segment = self.app.project.remove_segment(self.selected_index)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot remove clip: {exc}")
            return
        self._log(f"Removed {segment.get('name', 'clip')} from the timeline.")
        self.selected_index = None
        self.app.refresh_views()

    def _remove_clip(self, index: int) -> None:
        """Remove a clip from the context menu."""
        try:
            segment = self.app.project.remove_segment(index)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot remove clip: {exc}")
            return
        self._log(f"Removed {segment.get('name', 'clip')} from the timeline.")
        self.selected_index = None
        self.app.refresh_views()

    def _render(self) -> None:
        """Render the timeline in a worker thread."""
        if not self.app.project.timeline:
            self._log("Add clips to the timeline before rendering.")
            return
        output = self.output_var.get().strip()
        if not output:
            output = str(context.get_output_dir() / "timeline.mp4")
            self.output_var.set(output)
        self.render_button.configure(state="disabled")
        self.progress.start()
        self._log(f"Rendering {len(self.app.project.timeline)} clips...")
        threading.Thread(target=self._render_worker, args=(output,), daemon=True).start()

    def _render_worker(self, output: str) -> None:
        """Run the render off the UI thread."""
        result = self.app.project.render(output)
        self.after(0, lambda: self._finish_render(result))

    def _save_project(self) -> None:
        """Save the project to disk as JSON."""
        path = filedialog.asksaveasfilename(
            title="Save project",
            defaultextension=".veproj",
            filetypes=PROJECT_TYPES,
        )
        if not path:
            return
        try:
            saved = self.app.project.save_file(path)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot save project: {exc}")
            return
        self._log(f"Saved project to {saved.name}.")

    def _select(self, index: int) -> None:
        """Select a timeline segment, fill the range fields, and move the playhead."""
        self.selected_index = index
        segment = self.app.project.timeline[index]
        self.in_var.set(f"{float(segment.get('start') or 0):.2f}")
        self.out_var.set(f"{float(segment.get('end') or 0):.2f}")
        self.strip.select(index)
        if not self._playing and not self._preparing:
            spans = self._spans()
            if 0 <= index < len(spans):
                self._seek_to(spans[index][0])


def _duration_text(duration: float) -> str:
    """Format a duration in seconds as MM:SS."""
    total = int(max(0.0, float(duration or 0)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _segment_copy(segment: dict, offset: float = 0.0) -> dict:
    """Copy a timeline segment, optionally moving its in point later."""
    return {
        "path": segment.get("path"),
        "name": segment.get("name"),
        "start": float(segment.get("start") or 0.0) + offset,
        "end": float(segment.get("end") or 0.0),
    }


def _time_text(seconds: float) -> str:
    """Format seconds as MM:SS.d for the transport display."""
    total = max(0.0, float(seconds or 0))
    return f"{int(total // 60):02d}:{total % 60:04.1f}"
