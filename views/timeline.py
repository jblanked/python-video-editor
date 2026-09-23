"""Timeline view: arrange, preview, and render the project sequence."""

from __future__ import annotations

import json
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Any

import customtkinter as ctk
from PIL import Image, ImageOps

from tools import context, export_ops, preview, transcribe_ops
from tools.project import layer_label
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
THUMB_SIZE = (112, 63)
THUMB_POLL_MS = 150
DRAG_THRESHOLD = 6
DRAG_ACCENT = ("#3B8ED0", "#1F6AA5")


class JsonEditor(ctk.CTkToplevel):
    """Editable window for the project JSON; applies changes to the project."""

    def __init__(self, master: Any, app: Any, project: Any) -> None:
        super().__init__(master)
        self.app = app
        self.project = project
        self.title("Project JSON")
        self.geometry("900x620")
        self.minsize(640, 420)
        self.transient(master)
        self._textbox = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._textbox.pack(fill="both", expand=True, padx=10, pady=10)
        self._text = ctk.CTkTextbox(self._textbox, wrap="none")
        self._text.pack(fill="both", expand=True)
        self._text.insert("1.0", json.dumps(project.to_dict(), indent=2))
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(
            buttons,
            text="Apply",
            width=110,
            command=self._apply,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            buttons, text="Close", width=110, command=self._close
        ).pack(side="left", padx=4)
        self._status = ctk.CTkLabel(self, text="", anchor="w", wraplength=860)
        self._status.pack(fill="x", padx=14, pady=(0, 10))
        self.after(60, self.lift)

    def _apply(self) -> None:
        """Parse the edited JSON and update the project."""
        try:
            data = json.loads(self._text.get("1.0", "end"))
        except json.JSONDecodeError as exc:
            self._status.configure(text=f"Invalid JSON: {exc}", text_color="red")
            return
        if not isinstance(data, dict):
            self._status.configure(text="Top level must be a JSON object.", text_color="red")
            return
        try:
            self.project.load_dict(data)
        except (ValueError, OSError) as exc:
            self._status.configure(text=f"Cannot load project: {exc}", text_color="red")
            return
        self.app.refresh_views()
        self._status.configure(text="Project updated.", text_color="green")

    def _close(self) -> None:
        """Close the editor without applying changes."""
        self.destroy()


class TimelineView(ctk.CTkFrame):
    """Horizontal sequence editor with clip trimming, preview playback, and rendering."""

    def __init__(self, master: Any, app: Any) -> None:
        """Create the timeline view and load the current project."""
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.selected_index: int | None = None
        self.selected_path: str | None = None
        self._last_output: str | None = None
        self._thumbnails: dict[str, ctk.CTkImage | None] = {}
        self._thumb_labels: dict[str, ctk.CTkLabel] = {}
        self._bitmaps: dict[str, Image.Image | None] = {}
        self._thumb_busy = False
        self._cards: dict[str, ctk.CTkFrame] = {}
        self.cards: ctk.CTkScrollableFrame | None = None
        self._drag_path: str | None = None
        self._drag_start: tuple[int, int] = (0, 0)
        self._dragging = False
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
        for sequence in ("<Command-z>", "<Control-z>"):
            self.app.bind_all(sequence, lambda _event: self._shortcut(self._undo))
        for sequence in ("<Command-Shift-Z>", "<Command-Shift-z>", "<Control-Shift-Z>"):
            self.app.bind_all(sequence, lambda _event: self._shortcut(self._redo))
        for sequence in ("<Command-c>", "<Control-c>"):
            self.app.bind_all(sequence, lambda _event: self._shortcut(self._copy))
        for sequence in ("<Command-v>", "<Control-v>"):
            self.app.bind_all(sequence, lambda _event: self._shortcut(self._paste))

    def refresh(self) -> None:
        """Rebuild the pool cards, clip strip, and transport from the project."""
        timeline = self.app.project.timeline
        self._refresh_pool()
        if self.selected_index is not None and self.selected_index >= len(timeline):
            self.selected_index = None
        total = self._total_duration()
        self.strip.refresh()
        self.strip.select(self.selected_index)
        total_label_text = (
            f"{len(timeline)} clip(s) on {self.app.project.layer_count()} layer(s)  |  "
            f"total {_duration_text(total)}  |  drag a clip up or down to change its layer"
        )
        self.total_label.configure(text=total_label_text)
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
        menu.add_command(label="Detach Audio", command=lambda: self._detach_audio(index))
        menu.add_command(label="Add New Layer", command=self._add_layer)
        layer_menu = tk.Menu(menu, tearoff=0)
        top = self.app.project.top_layer()
        for layer in range(self.app.project.bottom_layer(), top + 2):
            label = layer_label(layer) + (" (new)" if layer > top else "")
            layer_menu.add_command(
                label=label, command=lambda target=layer, item=index: self._send_to_layer(item, target)
            )
        menu.add_cascade(label="Move to layer", menu=layer_menu)
        menu.add_command(label="Preview from this clip", command=lambda: self._play(start_index=index))
        menu.add_command(label="Move Left", command=lambda: self._move_clip(index, -1))
        menu.add_command(label="Move Right", command=lambda: self._move_clip(index, 1))
        menu.add_separator()
        menu.add_command(label="Remove from timeline", command=lambda: self._remove_clip(index))
        return menu

    def _build_ui(self) -> None:
        """Create the pool panel and the timeline editor area."""
        self.columnconfigure(0, weight=2)
        self.columnconfigure(1, weight=3)
        self.rowconfigure(0, weight=1)
        self._build_pool()

        main = ctk.CTkFrame(self)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(7, weight=1)
        toolbar = ctk.CTkFrame(main)
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar_rows = (
            ctk.CTkFrame(toolbar, fg_color="transparent"),
            ctk.CTkFrame(toolbar, fg_color="transparent"),
        )
        for toolbar_row in toolbar_rows:
            toolbar_row.pack(fill="x")
        buttons = (
            ("New", self._new_project, 78),
            ("Open", self._open_project, 78),
            ("Save", self._save_project, 78),
            ("JSON", self._json_editor, 78),
            ("Add Clip", self._add_clip, 78),
            ("Remove", self._remove, 78),
            ("Undo", self._undo, 78),
            ("Redo", self._redo, 78),
            ("Copy", self._copy, 78),
            ("Paste", self._paste, 78),
            ("Add Layer", self._add_layer, 78),
            ("Layer Up", lambda: self._change_layer(1), 78),
            ("Layer Down", lambda: self._change_layer(-1), 78),
            ("Detach Audio", self._detach_audio, 96),
            ("Move Left", lambda: self._move(-1), 78),
            ("Move Right", lambda: self._move(1), 78),
            ("Transcribe", self._transcribe, 78),
            ("Clear", self._clear, 78),
        )
        for index, (text, command, width) in enumerate(buttons):
            parent = toolbar_rows[0] if index < 9 else toolbar_rows[1]
            ctk.CTkButton(parent, text=text, width=width, command=command).pack(
                side="left", padx=2, pady=4
            )
        self.total_label = ctk.CTkLabel(main, text="", anchor="w")
        self.total_label.grid(row=1, column=0, sticky="ew", padx=6, pady=(4, 2))

        player_frame = ctk.CTkFrame(main)
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

        strip_area = ctk.CTkFrame(main, fg_color="transparent")
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

        edit = ctk.CTkFrame(main)
        edit.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        ctk.CTkLabel(edit, text="In", width=22).pack(side="left", padx=(8, 2), pady=8)
        self.in_var = tk.StringVar()
        ctk.CTkEntry(edit, textvariable=self.in_var, width=90).pack(side="left")
        ctk.CTkLabel(edit, text="Out", width=30).pack(side="left", padx=(10, 2))
        self.out_var = tk.StringVar()
        ctk.CTkEntry(edit, textvariable=self.out_var, width=90).pack(side="left")
        ctk.CTkButton(edit, text="Apply", width=80, command=self._apply_range).pack(side="left", padx=10)
        ctk.CTkLabel(edit, text="Times accept seconds, MM:SS, or HH:MM:SS").pack(side="left", padx=6)

        render = ctk.CTkFrame(main)
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
        self.progress = ctk.CTkProgressBar(main, mode="indeterminate")
        self.progress.grid(row=6, column=0, sticky="ew", pady=(6, 2))
        self.log = ctk.CTkTextbox(main, height=80, wrap="word", state="disabled")
        self.log.grid(row=7, column=0, sticky="nsew", padx=4, pady=(2, 4))

    def _build_pool(self) -> None:
        """Create the media pool panel with its toolbar and cards."""
        pool = ctk.CTkFrame(self)
        pool.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        pool.columnconfigure(0, weight=1)
        pool.rowconfigure(2, weight=1)
        toolbar = ctk.CTkFrame(pool)
        toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        for text, command, width in (
            ("Add Clips", self._add_clips, 100),
            ("Remove from Pool", self._remove_selected, 110),
            ("Clear Pool", self._clear_pool, 90),
        ):
            ctk.CTkButton(toolbar, text=text, width=width, command=command).pack(side="left", padx=3)
        toolbar2 = ctk.CTkFrame(pool)
        toolbar2.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 4))
        for text, command, width in (
            ("Add to Timeline", self._add_to_timeline, 110),
            ("Add All To Timeline", self._add_all_to_timeline, 140),
            ("Preview", self._preview_selected, 80),
        ):
            ctk.CTkButton(toolbar2, text=text, width=width, command=command).pack(side="left", padx=3)
        self.cards = ctk.CTkScrollableFrame(pool, fg_color="transparent")
        self.cards.grid(row=2, column=0, sticky="nsew", padx=4, pady=(4, 2))
        hint = ctk.CTkLabel(
            pool,
            text="Drag a card onto the timeline strip to insert it.",
            anchor="w",
            text_color=("gray40", "gray60"),
        )
        hint.grid(row=3, column=0, sticky="w", padx=10, pady=(4, 2))

    def _refresh_pool(self) -> None:
        """Rebuild the media pool cards from the project media list."""
        media = list(self.app.project.media)
        if self.selected_path and self.selected_path not in {clip["path"] for clip in media}:
            self.selected_path = None
        for child in self.cards.winfo_children():
            child.destroy()
        self._cards = {}
        self._thumb_labels = {}
        if not media:
            empty = ctk.CTkLabel(self.cards, text="No clips in the pool. Use Add Clips.", anchor="w")
            empty.pack(fill="x", padx=6, pady=8)
        for clip in media:
            self._build_card(clip)
        self._update_selection_ui()
        self._start_thumbnails()

    def _build_card(self, clip: dict) -> None:
        """Create one media card with thumbnail, name, and metadata."""
        path = clip["path"]
        card = ctk.CTkFrame(self.cards, corner_radius=8)
        card.pack(fill="x", padx=4, pady=3)
        image = self._thumbnails.get(path)
        thumb = ctk.CTkLabel(
            card,
            text="" if image is not None else "...",
            image=image,
            width=THUMB_SIZE[0],
            height=THUMB_SIZE[1],
        )
        thumb.grid(row=0, column=0, rowspan=2, padx=(8, 6), pady=8)
        thumb.bind("<Button-1>", lambda event, item=path: self._begin_drag(event, item))
        thumb.bind("<B1-Motion>", self._drag_motion)
        thumb.bind("<ButtonRelease-1>", lambda event, item=path: self._end_drag(event, item))
        self._thumb_labels[path] = thumb
        details = ctk.CTkFrame(card, fg_color="transparent")
        details.grid(row=0, column=1, rowspan=2, sticky="ew", padx=(0, 8), pady=6)
        card.columnconfigure(1, weight=1)
        name = ctk.CTkLabel(details, text=clip["name"], anchor="w", font=ctk.CTkFont(size=13, weight="bold"))
        name.pack(fill="x")
        meta = (
            f"{clip['width']}x{clip['height']}  |  {_duration_text(clip['duration'])}  |  "
            f"{clip['size_bytes'] / 1e6:.1f} MB"
        )
        meta_label = ctk.CTkLabel(details, text=meta, anchor="w", text_color=("gray30", "gray70"))
        meta_label.pack(fill="x")
        for widget in (card, details, name, meta_label):
            widget.bind("<Button-1>", lambda event, item=path: self._begin_drag(event, item))
            widget.bind("<B1-Motion>", self._drag_motion)
            widget.bind("<ButtonRelease-1>", lambda event, item=path: self._end_drag(event, item))
        card.bind("<Double-Button-1>", lambda _event, item=path: self.app.open_path(item))
        self._cards[path] = card

    def _shortcut(self, action: Any) -> str | None:
        """Run a timeline shortcut unless a text field has focus."""
        try:
            focused = self.app.focus_get()
        except tk.TclError:
            focused = None
        if isinstance(focused, (tk.Entry, tk.Text)):
            return None
        action()
        return "break"

    def _undo(self) -> None:
        """Undo the last timeline edit."""
        if not self.app.project.undo():
            self._log("Nothing to undo.")
            return
        self.selected_index = None
        self._log("Undo.")
        self.app.refresh_views()

    def _redo(self) -> None:
        """Redo the last edit that was undone."""
        if not self.app.project.redo():
            self._log("Nothing to redo.")
            return
        self.selected_index = None
        self._log("Redo.")
        self.app.refresh_views()

    def _copy(self) -> None:
        """Copy the selected clip onto the clip clipboard."""
        if self.selected_index is None:
            self._log("Select a timeline clip first.")
            return
        try:
            segment = self.app.project.copy_segment(self.selected_index)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot copy clip: {exc}")
            return
        self._log(f"Copied {segment.get('name', 'clip')}.")

    def _paste(self) -> None:
        """Paste the copied clip at the end of its lane."""
        try:
            segment = self.app.project.paste_segment()
        except (ValueError, OSError) as exc:
            self._log(f"Cannot paste clip: {exc}")
            return
        self.selected_index = self.app.project.timeline.index(segment)
        self._log(
            f"Pasted {segment.get('name', 'clip')} on "
            f"{layer_label(int(segment.get('layer') or 0))}."
        )
        self.app.refresh_views()

    def _add_clips(self) -> None:
        """Ask for video files and add them to the media pool."""
        paths = filedialog.askopenfilenames(title="Add clips", filetypes=VIDEO_TYPES)
        if not paths:
            return
        for path in paths:
            try:
                self.app.project.add_media(path)
            except (ValueError, OSError) as exc:
                self._log(f"Skipped {Path(path).name}: {exc}")
        self._log(f"Added {len(paths)} clip(s) to the pool.")
        self.app.refresh_views()

    def _remove_selected(self) -> None:
        """Remove the selected clip from the media pool."""
        path = self._require_clip()
        if path is None:
            return
        self.app.project.remove_media(path)
        self.selected_path = None
        self._log(f"Removed {Path(path).name} from the pool.")
        self.app.refresh_views()

    def _clear_pool(self) -> None:
        """Remove every clip from the media pool."""
        self.app.project.media.clear()
        self.selected_path = None
        self._log("Media pool cleared.")
        self.app.refresh_views()

    def _add_to_timeline(self) -> None:
        """Add the selected pool clip to the end of the timeline."""
        path = self._require_clip()
        if path is None:
            return
        try:
            segment = self.app.project.add_to_timeline(path)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot add to timeline: {exc}")
            return
        self._log(f"Added {segment['name']} to the timeline.")
        self.app.refresh_views()

    def _add_all_to_timeline(self) -> None:
        """Add every clip in the media pool to the timeline in order."""
        media = list(self.app.project.media)
        if not media:
            self._log("Add clips to the pool first.")
            return
        added = 0
        for clip in media:
            try:
                self.app.project.add_to_timeline(clip["path"])
                added += 1
            except (ValueError, OSError) as exc:
                self._log(f"Skipped {clip.get('name', 'clip')}: {exc}")
        self._log(f"Added {added} clip(s) to the timeline.")
        self.app.refresh_views()

    def _preview_selected(self) -> None:
        """Open the selected pool clip in the system player."""
        if self.selected_path:
            self.app.open_path(self.selected_path)

    def _require_clip(self) -> str | None:
        """Return the selected pool clip path, logging a hint when none is selected."""
        if not self.selected_path:
            self._log("Select a clip in the pool first.")
            return None
        return self.selected_path

    def _select_pool(self, path: str) -> None:
        """Select a pool card and highlight it."""
        self.selected_path = path
        self._update_selection_ui()

    def _begin_drag(self, event: Any, path: str) -> None:
        """Start a drag of a pool card toward the timeline strip."""
        self._drag_path = path
        self._drag_start = (int(getattr(event, "x_root", 0)), int(getattr(event, "y_root", 0)))
        self._dragging = False
        card = self._cards.get(path)
        if card is not None:
            card.configure(border_width=2, border_color=DRAG_ACCENT)
        self._log(f"Drag {Path(path).name} onto the strip to insert it.")

    def _drag_motion(self, event: Any) -> None:
        """Track the pointer and highlight the insertion index under it."""
        path = self._drag_path
        if path is None:
            return
        pointer_x, pointer_y = int(getattr(event, "x_root", 0)), int(getattr(event, "y_root", 0))
        if not self._dragging:
            if abs(pointer_x - self._drag_start[0]) + abs(pointer_y - self._drag_start[1]) < DRAG_THRESHOLD:
                return
            self._dragging = True
        if self.strip.over_strip(pointer_x, pointer_y):
            self.strip.highlight_drop(self.strip.drop_target(pointer_x, pointer_y))
        else:
            self.strip.highlight_drop(None)

    def _end_drag(self, event: Any, path: str) -> None:
        """Drop the card onto the strip at the highlighted index."""
        moved = self._dragging
        self._drag_path = None
        self._dragging = False
        card = self._cards.get(path)
        if card is not None:
            card.configure(border_width=0)
        if not moved:
            self.strip.highlight_drop(None)
            self._select_pool(path)
            return
        layer, index = self.strip.drop_target(
            int(getattr(event, "x_root", 0)), int(getattr(event, "y_root", 0))
        )
        try:
            segment = self.app.project.add_to_timeline(path, position=index, layer=layer)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot drop on strip: {exc}")
            return
        self._log(f"Dropped {segment['name']} on layer {layer}.")
        self.app.refresh_views()

    def _update_selection_ui(self) -> None:
        """Refresh card highlighting for the selected pool clip."""
        for path, card in self._cards.items():
            if path == self.selected_path:
                card.configure(border_width=2, border_color=DRAG_ACCENT)
            else:
                card.configure(border_width=0)

    def _apply_thumbnail(self, path: str, bitmap: Image.Image | None) -> None:
        """Show a generated thumbnail on the matching card."""
        if bitmap is None:
            return
        label = self._thumb_labels.get(path)
        if label is None or not label.winfo_exists():
            return
        image = ctk.CTkImage(light_image=bitmap, dark_image=bitmap, size=THUMB_SIZE)
        self._thumbnails[path] = image
        label.configure(image=image, text="")

    def _load_thumbnail(self, path: str) -> Image.Image | None:
        """Render a thumbnail bitmap for a clip; safe to call off the UI thread."""
        if path in self._bitmaps:
            return self._bitmaps[path]
        bitmap: Image.Image | None = None
        thumb_dir = Path(tempfile.gettempdir()) / "video_editor_thumbs"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        target = thumb_dir / f"{Path(path).stem}_{abs(hash(path)) % 10**8}.jpg"
        try:
            if not target.exists():
                export_ops.create_thumbnail(path, target, time="00:00:01", width=224)
            with Image.open(target) as loaded:
                fitted = ImageOps.contain(loaded.convert("RGB"), THUMB_SIZE)
                canvas = Image.new("RGB", THUMB_SIZE, "#141414")
                canvas.paste(
                    fitted,
                    (
                        (THUMB_SIZE[0] - fitted.width) // 2,
                        (THUMB_SIZE[1] - fitted.height) // 2,
                    ),
                )
                bitmap = canvas
        except (OSError, ValueError, RuntimeError):
            bitmap = None
        self._bitmaps[path] = bitmap
        return bitmap

    def _preload_thumbnails(self, paths: list[str]) -> None:
        """Generate thumbnail bitmaps off the UI thread."""
        for path in paths:
            try:
                self._load_thumbnail(path)
            except (OSError, ValueError):
                self._bitmaps[path] = None

    def _poll_thumbnails(self) -> None:
        """Apply finished thumbnails and stop polling once everything is done."""
        pending: list[str] = []
        for path in list(self._cards):
            if path in self._thumbnails:
                continue
            if path in self._bitmaps:
                self._apply_thumbnail(path, self._bitmaps[path])
            else:
                pending.append(path)
        if pending:
            self.after(THUMB_POLL_MS, self._poll_thumbnails)
        else:
            self._thumb_busy = False

    def _start_thumbnails(self) -> None:
        """Queue thumbnail generation for cards that do not have one yet."""
        if self._thumb_busy:
            return
        pending = [path for path in self._cards if path not in self._thumbnails]
        if not pending:
            return
        self._thumb_busy = True
        threading.Thread(target=self._preload_thumbnails, args=(pending,), daemon=True).start()
        self.after(THUMB_POLL_MS, self._poll_thumbnails)

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

    def _add_layer(self) -> None:
        """Add an empty layer above the current ones."""
        self._log(f"Added {layer_label(self.app.project.add_layer())}.")
        self.app.refresh_views()

    def _detach_audio(self, index: int | None = None) -> None:
        """Move a clip's audio onto its own lane below the clip."""
        target = self.selected_index if index is None else index
        if target is None:
            self._log("Select a timeline clip first.")
            return
        try:
            segment, audio = self.app.project.detach_audio(target)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot separate audio: {exc}")
            return
        self.selected_index = self.app.project.timeline.index(segment)
        self._log(
            f"Separated the audio of {segment.get('name', 'clip')} onto "
            f"{layer_label(audio['layer'])}."
        )
        self.app.refresh_views()

    def _change_layer(self, offset: int) -> None:
        """Move the selected clip up or down one layer."""
        if self.selected_index is None:
            self._log("Select a timeline clip first.")
            return
        timeline_items = self.app.project.timeline
        if not 0 <= self.selected_index < len(timeline_items):
            return
        segment = timeline_items[self.selected_index]
        target = int(segment.get("layer") or 0) + offset
        if target < self.app.project.bottom_layer():
            self._log("There is no lane below this one.")
            return
        self._send_to_layer(self.selected_index, target)

    def _send_to_layer(self, index: int, layer: int) -> None:
        """Move a clip to a layer, adding it when the layer is new."""
        if layer > self.app.project.top_layer():
            self.app.project.add_layer()
        try:
            segment = self.app.project.set_segment_layer(index, layer)
        except (ValueError, OSError) as exc:
            self._log(f"Cannot move clip: {exc}")
            return
        self.selected_index = self.app.project.timeline.index(segment)
        self._log(f"Moved {segment.get('name', 'clip')} to {layer_label(layer)}.")
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

    def _json_editor(self) -> None:
        """Open the project JSON in an editable window."""
        JsonEditor(self, self.app, self.app.project)

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
            timeline_items = self.app.project.timeline
            if 0 <= start_index < len(timeline_items):
                self._playhead = float(timeline_items[start_index].get("abs_start") or 0.0)
        if self._playhead >= self._total_duration() - 0.05:
            self._playhead = 0.0
        self._play_start = self._playhead
        self._update_playhead_ui(self._playhead)
        if self.mute_var.get():
            self._start_player(None)
            return
        self._preparing = True
        self._audio_ready = False
        self._prepared_audio = None
        self.play_button.configure(state="disabled", text="Prepare")
        self._log("Preparing preview audio...")
        threading.Thread(target=self._prepare_audio, daemon=True).start()
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
        self._start_player(audio_path)

    def _prepare_audio(self) -> None:
        """Render the preview audio track off the UI thread."""
        try:
            self._prepared_audio = preview.audio_track(
                list(self.app.project.timeline), position=self._playhead
            )
        except (OSError, ValueError):
            self._prepared_audio = None
        self._audio_ready = True

    def _start_player(self, audio_path: Path | None) -> None:
        """Start the embedded preview player at the current playhead."""
        timeline_items = list(self.app.project.timeline)
        if not timeline_items:
            return
        self._player.load(timeline_items)
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

    def _request_still(self, position: float) -> None:
        """Request a composited preview frame for a timeline position."""
        timeline_items = list(self.app.project.timeline)
        if not timeline_items:
            return
        self._still_token += 1
        if not self._still_pending:
            self._still_pending = True
            self.after(STILL_POLL_MS, self._poll_still)
        threading.Thread(
            target=self._still_worker,
            args=(self._still_token, timeline_items, position),
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
        """Return each timeline segment with its layered start and end time."""
        return [
            (
                float(segment.get("abs_start") or 0.0),
                float(segment.get("abs_start") or 0.0)
                + max(0.0, float(segment.get("end") or 0.0) - float(segment.get("start") or 0.0)),
                segment,
            )
            for segment in self.app.project.timeline
        ]

    def _still_worker(self, token: int, segments: list[dict], position: float) -> None:
        """Grab a composited still frame off the UI thread."""
        image = preview.still_composite(segments, position)
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
        return self.app.project.timeline_duration()

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
            self._seek_to(float(segment.get("abs_start") or 0.0))


def _duration_text(duration: float) -> str:
    """Format a duration in seconds as MM:SS."""
    total = int(max(0.0, float(duration or 0)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _time_text(seconds: float) -> str:
    """Format seconds as MM:SS.d for the transport display."""
    total = max(0.0, float(seconds or 0))
    return f"{int(total // 60):02d}:{total % 60:04.1f}"
