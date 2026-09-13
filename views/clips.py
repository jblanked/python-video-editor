"""Clips view: media pool with thumbnails and per-clip operations."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from tkinter import filedialog
from typing import Any

import customtkinter as ctk
from PIL import Image, ImageOps

from tools import export_ops
from tools import transcribe_ops
from tools.dispatch import execute_tool
from tools.registry import Op, get_groups, get_ops
from views.op_form import OperationForm
from views.text_dialog import TextDialog

THUMB_SIZE = (112, 63)
THUMB_POLL_MS = 150
VIDEO_TYPES = [
    ("Video files", "*.mp4 *.mov *.m4v *.mkv *.avi *.webm *.gif *.wmv *.flv"),
    ("All files", "*.*"),
]
HIDDEN_GROUPS = ("Timeline",)


class ClipsView(ctk.CTkFrame):
    """Media pool with a dynamic operations panel for the selected clip."""

    def __init__(self, master: Any, app: Any) -> None:
        """Create the clips view and load the current media pool."""
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.selected_path: str | None = None
        self._current_op: Op | None = None
        self._thumbnails: dict[str, ctk.CTkImage | None] = {}
        self._thumb_labels: dict[str, ctk.CTkLabel] = {}
        self._bitmaps: dict[str, Image.Image | None] = {}
        self._thumb_busy = False
        self._cards: dict[str, ctk.CTkFrame] = {}
        self._op_by_label: dict[str, Op] = {}
        self._last_output: str | None = None
        self._build_ui()
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the media pool cards from the project media list."""
        media = list(self.app.project.media)
        if self.selected_path and self.selected_path not in {clip["path"] for clip in media}:
            self.selected_path = None
        for child in self.cards.winfo_children():
            child.destroy()
        self._cards = {}
        self._thumb_labels = {}
        if not media:
            empty = ctk.CTkLabel(self.cards, text="No clips loaded. Use Add Clips.", anchor="w")
            empty.pack(fill="x", padx=6, pady=8)
        for clip in media:
            self._build_card(clip)
        self._update_selection_ui()
        self._start_thumbnails()

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

    def _add_to_timeline(self) -> None:
        """Add the selected clip to the project timeline."""
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
        thumb.bind("<Button-1>", lambda _event, item=path: self._select(item))
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
            widget.bind("<Button-1>", lambda _event, item=path: self._select(item))
        card.bind("<Double-Button-1>", lambda _event, item=path: self.app.open_path(item))
        self._cards[path] = card

    def _build_ui(self) -> None:
        """Create the library panel and the operations panel."""
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(0, weight=1)

        library = ctk.CTkFrame(self)
        library.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        library.columnconfigure(0, weight=1)
        library.rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(library)
        toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        for text, command, width in (
            ("Add Clips", self._add_clips, 100),
            ("Remove", self._remove_selected, 100),
            ("Clear", self._clear_pool, 100),
            ("Add to Timeline", self._add_to_timeline, 110),
            ("Add All To Timeline", self._add_all_to_timeline, 150),
            ("Preview", self._preview_selected, 100),
        ):
            ctk.CTkButton(toolbar, text=text, width=width, command=command).pack(side="left", padx=3)
        self.cards = ctk.CTkScrollableFrame(library, fg_color="transparent")
        self.cards.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 8))

        panel = ctk.CTkFrame(self)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)
        ctk.CTkLabel(panel, text="Clip Operations", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 2)
        )
        selectors = ctk.CTkFrame(panel, fg_color="transparent")
        selectors.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        self._groups = [group for group in get_groups() if group not in HIDDEN_GROUPS]
        self.group_menu = ctk.CTkOptionMenu(selectors, values=self._groups, command=self._on_group_change, width=110)
        self.group_menu.pack(side="left")
        self.op_menu = ctk.CTkOptionMenu(selectors, values=[""], command=self._on_op_change, width=180)
        self.op_menu.pack(side="left", padx=8)
        self.source_label = ctk.CTkLabel(panel, text="Source clip: none", anchor="w")
        self.source_label.grid(row=2, column=0, sticky="ew", padx=10, pady=(2, 4))
        self.form = OperationForm(panel, source_path="")
        self.form.grid(row=4, column=0, sticky="nsew", padx=8, pady=4)

        run_row = ctk.CTkFrame(panel, fg_color="transparent")
        run_row.grid(row=5, column=0, sticky="ew", padx=10, pady=6)
        self.run_button = ctk.CTkButton(run_row, text="Run Operation", command=self._run_op, width=130)
        self.run_button.pack(side="left")
        self.open_button = ctk.CTkButton(
            run_row,
            text="Open Output",
            width=110,
            state="disabled",
            command=self._open_output,
        )
        self.open_button.pack(side="left", padx=8)
        self.progress = ctk.CTkProgressBar(panel, mode="indeterminate")
        self.progress.grid(row=6, column=0, sticky="ew", padx=10, pady=(0, 4))
        self.log = ctk.CTkTextbox(panel, height=140, wrap="word", state="disabled")
        self.log.grid(row=7, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self._on_group_change(self._groups[1] if len(self._groups) > 1 else self._groups[0])

    def _clear_pool(self) -> None:
        """Remove every clip from the media pool."""
        self.app.project.media.clear()
        self.selected_path = None
        self._log("Media pool cleared.")
        self.app.refresh_views()

    def _finish(self, name: str, result: dict) -> None:
        """Report an operation result and refresh the views."""
        self.progress.stop()
        self.run_button.configure(state="normal")
        prefix = "OK" if result.get("success") else "FAILED"
        self._log(f"[{prefix}] {name}: {result.get('message')}")
        output = result.get("output")
        if output:
            self._last_output = str(output)
            self.open_button.configure(state="normal")
            self._log(f"Output: {output}")
        if result.get("success") and name.startswith("transcribe"):
            self._show_transcript(result)
        self.app.refresh_views()

    def _show_transcript(self, result: dict) -> None:
        """Open the script window for a finished transcription."""
        text = transcribe_ops.transcript_text(result.get("details") or {})
        output = result.get("output")
        if output:
            text = f"Saved to {output}\n\n{text}"
        TextDialog(self, "Transcript", text)

    def _log(self, message: str) -> None:
        """Append a line to the operations log."""
        self.log.configure(state="normal")
        self.log.insert("end", f"{message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_group_change(self, group: str) -> None:
        """Populate the operation menu for the selected group."""
        ops = get_ops(group=group)
        self._op_by_label = {op.label: op for op in ops if op}
        labels = list(self._op_by_label) or ["None"]
        self.op_menu.configure(values=labels)
        self.op_menu.set(labels[0])
        self._on_op_change(labels[0])

    def _on_op_change(self, label: str) -> None:
        """Show the parameter form for the selected operation."""
        op = self._op_by_label.get(label)
        if op is None:
            return
        self._current_op = op
        self.form.build(op, source_path=self.selected_path or "")

    def _open_output(self) -> None:
        """Open the last produced file with the system default app."""
        if self._last_output:
            self.app.open_path(self._last_output)

    def _preview_selected(self) -> None:
        """Open the selected clip in the system player."""
        if self.selected_path:
            self.app.open_path(self.selected_path)

    def _remove_selected(self) -> None:
        """Remove the selected clip from the media pool."""
        path = self._require_clip()
        if path is None:
            return
        self.app.project.remove_media(path)
        self.selected_path = None
        self._log(f"Removed {Path(path).name} from the pool.")
        self.app.refresh_views()

    def _require_clip(self) -> str | None:
        """Return the selected clip path, logging a hint when none is selected."""
        if not self.selected_path:
            self._log("Select a clip in the pool first.")
            return None
        return self.selected_path

    def _run_op(self) -> None:
        """Execute the current operation in a worker thread."""
        op = self._current_op
        if op is None:
            return
        args = self.form.collect()
        needs_source = any(param.name == "path" and param.type == "path" for param in op.params)
        if needs_source and not args.get("path"):
            self._log("Select a clip in the pool first.")
            return
        self.run_button.configure(state="disabled")
        self.progress.start()
        self._log(f"Running {op.label}...")
        threading.Thread(target=self._worker, args=(op.name, args), daemon=True).start()

    def _select(self, path: str) -> None:
        """Select a clip and highlight its card."""
        self.selected_path = path
        self._update_selection_ui()
        self.form.update_source(path)

    def _update_selection_ui(self) -> None:
        """Refresh card highlighting and the source clip label."""
        for path, card in self._cards.items():
            if path == self.selected_path:
                card.configure(border_width=2, border_color=("#3B8ED0", "#1F6AA5"))
            else:
                card.configure(border_width=0)
        if self.selected_path:
            self.source_label.configure(text=f"Source clip: {Path(self.selected_path).name}")
        else:
            self.source_label.configure(text="Source clip: none")

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

    def _worker(self, name: str, args: dict) -> None:
        """Run one operation off the UI thread."""
        result = execute_tool(name, args)
        self.after(0, lambda: self._finish(name, result))


def _duration_text(duration: float) -> str:
    """Format a duration in seconds as MM:SS."""
    total = int(max(0.0, float(duration or 0)))
    return f"{total // 60:02d}:{total % 60:02d}"
