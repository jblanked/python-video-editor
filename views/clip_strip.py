"""Layered clip strip: FCP-style draggable, trimmable timeline rows."""

from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

from tools import ffmpeg_utils as ff
from tools.project import layer_label

BLOCK_HEIGHT = 64
ROW_GAP = 8
ROW_STRIDE = BLOCK_HEIGHT + ROW_GAP
LABEL_WIDTH = 72
EDGE_WIDTH = 9
MIN_BLOCK_WIDTH = 34
MIN_LENGTH = 0.2
DRAG_THRESHOLD = 5
SCROLL_EDGE = 26
SCROLL_UNITS = 1
DEFAULT_ZOOM = 40
ZOOM_LEVELS = (10, 20, 40, 80)
DRAG_FG = ("#DCE4EE", "#343B44")
ACCENT = ("#3B8ED0", "#1F6AA5")
HANDLE_FG = ("#AAB4BF", "#4A5560")
PLAYHEAD_FG = "#E05C5C"
LABEL_FG = ("#E8EAED", "#2B2D31")
AUDIO_FG = ("#2F6F6B", "#24544F")


class ClipStrip(ctk.CTkFrame):
    """Horizontal layer rows of clip blocks with layer moves and edge trimming."""

    def __init__(
        self,
        master: Any,
        project: Any,
        on_select: Callable[[int], None],
        on_change: Callable[[str, int | None], None],
        on_play: Callable[[int], None] | None = None,
        on_context: Callable[[int, Any], None] | None = None,
    ) -> None:
        """Create the layered board, its rows, and the playhead line."""
        super().__init__(master, fg_color="transparent")
        self.project = project
        self._on_select = on_select
        self._on_change = on_change
        self._on_play = on_play
        self._on_context = on_context
        self._pixels_per_second = float(DEFAULT_ZOOM)
        self._blocks: list[ctk.CTkFrame] = []
        self._by_index: dict[int, ctk.CTkFrame] = {}
        self._rows: dict[int, ctk.CTkFrame] = {}
        self._durations: dict[str, float] = {}
        self._selected: int | None = None
        self._playhead = 0.0
        self._empty_label: ctk.CTkLabel | None = None
        self._drag_block: ctk.CTkFrame | None = None
        self._drag_index: int | None = None
        self._drag_layer = 0
        self._hover_layer: int | None = None
        self._drag_edge = ""
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._drag_start_range = (0.0, 0.0)
        self._drag_range = (0.0, 0.0)
        self._drag_x = 0
        self._drag_slide = False
        self._drag_moved = False
        self._drop_target: tuple[int, int] | None = None
        self._strip = ctk.CTkScrollableFrame(
            self, orientation="horizontal", height=BLOCK_HEIGHT + ROW_GAP + 26
        )
        self._strip.pack(fill="both", expand=True)
        self._board = ctk.CTkFrame(self._strip, fg_color="transparent")
        self._board.pack(anchor="nw")
        self._board.pack_propagate(False)
        self._playhead_line = ctk.CTkFrame(
            self._board, width=2, height=BLOCK_HEIGHT, fg_color=PLAYHEAD_FG
        )
        self._drop_marker = ctk.CTkFrame(
            self._board, width=3, height=BLOCK_HEIGHT, fg_color=ACCENT
        )
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the layer rows and their clip blocks from the timeline."""
        self._abort_drag()
        for block in self._blocks:
            block.destroy()
        self._blocks = []
        self._by_index = {}
        for row in self._rows.values():
            row.destroy()
        self._rows = {}
        if self._empty_label is not None:
            self._empty_label.destroy()
            self._empty_label = None
        bottom = self.project.bottom_layer()
        highest = self.project.top_layer()
        count = highest - bottom + 1
        duration = self.project.timeline_duration()
        board_width = int(LABEL_WIDTH + max(duration, 6.0) * self._pixels_per_second + 60)
        board_height = count * ROW_STRIDE + 4
        self._board.configure(width=board_width, height=board_height)
        self._strip.configure(height=board_height + 26)
        if not self.project.timeline:
            self._empty_label = ctk.CTkLabel(
                self._board,
                text="Timeline is empty - add clips with Add Clips or drag a pool card onto a row.",
                anchor="w",
            )
            self._empty_label.place(x=LABEL_WIDTH, y=8)
        for layer in range(highest, bottom - 1, -1):
            row = ctk.CTkFrame(self._board, fg_color="transparent", width=board_width, height=BLOCK_HEIGHT)
            row.place(x=0, y=(highest - layer) * ROW_STRIDE)
            label = ctk.CTkLabel(
                row,
                text=layer_label(layer),
                anchor="w",
                fg_color=LABEL_FG,
                corner_radius=4,
                width=LABEL_WIDTH - 8,
                height=BLOCK_HEIGHT - 12,
                text_color=("gray25", "gray80"),
            )
            label.place(x=0, y=6)
            self._rows[layer] = row
            for index, segment in enumerate(self.project.timeline):
                if int(segment.get("layer") or 0) != layer:
                    continue
                block = self._build_block(index, segment, row)
                self._blocks.append(block)
                self._by_index[index] = block
        self._drop_target = None
        self._playhead_line.lift()
        self._drop_marker.lift()
        self._apply_selection()
        self.set_playhead(self._playhead)

    def select(self, index: int | None) -> None:
        """Highlight the block that matches a timeline index."""
        self._selected = index
        self._apply_selection()

    def set_playhead(self, position: float) -> None:
        """Move the playhead line to a timeline position."""
        self._playhead = max(0.0, float(position))
        if not self._playhead_line.winfo_exists():
            return
        x = int(self._x_for_time(self._playhead))
        try:
            height = max(1, int(self._board.cget("height")) - 8)
        except (ValueError, TypeError):
            height = BLOCK_HEIGHT
        self._playhead_line.configure(height=height)
        self._playhead_line.place(x=x, y=4)

    def set_zoom(self, level: Any) -> None:
        """Change the pixels-per-second scale and rebuild the blocks."""
        try:
            value = float(level)
        except (TypeError, ValueError):
            return
        if value <= 0 or value == self._pixels_per_second:
            return
        self._pixels_per_second = value
        self.refresh()

    def drop_target(self, pointer_x: int, pointer_y: int) -> tuple[int, int]:
        """Return the (layer, index) under the pointer for a pool drop."""
        bottom = self.project.bottom_layer()
        highest = self.project.top_layer()
        count = highest - bottom + 1
        origin_y = self._strip.winfo_rooty()
        row = int((pointer_y - origin_y) // ROW_STRIDE)
        row = max(0, min(row, count - 1))
        layer = highest - row
        board_x = pointer_x - self._strip.winfo_rootx() - LABEL_WIDTH
        time = max(0.0, board_x / self._pixels_per_second)
        index = len(self.project.timeline)
        for position, segment in enumerate(self.project.timeline):
            if int(segment.get("layer") or 0) != layer:
                continue
            if float(segment.get("abs_start") or 0.0) > time:
                index = position
                break
        return layer, index

    def over_strip(self, pointer_x: int, pointer_y: int) -> bool:
        """Return True when the pointer is inside the strip area."""
        root_x = self._strip.winfo_rootx()
        root_y = self._strip.winfo_rooty()
        return (
            root_x <= pointer_x < root_x + self._strip.winfo_width()
            and root_y <= pointer_y < root_y + self._strip.winfo_height()
        )

    def highlight_drop(self, target: tuple[int, int] | None) -> None:
        """Show the insertion marker for a (layer, index) drop target."""
        if target is None:
            self._drop_target = None
            self._drop_marker.place_forget()
            return
        self._drop_target = target
        layer, index = target
        time = self.project.timeline_duration()
        if 0 <= index < len(self.project.timeline):
            time = float(self.project.timeline[index].get("abs_start") or 0.0)
        row = max(0, self.project.top_layer() - layer)
        x = int(self._x_for_time(time))
        self._drop_marker.configure(height=BLOCK_HEIGHT - 8)
        self._drop_marker.place(x=x, y=row * ROW_STRIDE + 4)

    def _abort_drag(self) -> None:
        """Drop any drag state, for example when the strip is rebuilt."""
        self._drag_block = None
        self._drag_index = None
        self._drag_edge = ""
        self._drag_slide = False
        self._drag_moved = False
        self._hover_layer = None

    def _apply_selection(self) -> None:
        """Draw the selection highlight around the selected block."""
        for block in self._blocks:
            if not block.winfo_exists():
                continue
            if getattr(block, "segment_index", None) == self._selected:
                block.configure(border_width=2, border_color=ACCENT)
            else:
                block.configure(border_width=0)

    def _auto_scroll(self, pointer_x: int) -> None:
        """Scroll the strip while dragging near its left or right edge."""
        canvas = getattr(self._strip, "_parent_canvas", None)
        if canvas is None:
            return
        left = canvas.winfo_rootx()
        right = left + canvas.winfo_width()
        if pointer_x < left + SCROLL_EDGE:
            canvas.xview_scroll(-SCROLL_UNITS, "units")
        elif pointer_x > right - SCROLL_EDGE:
            canvas.xview_scroll(SCROLL_UNITS, "units")

    def _begin_body_drag(self, event: Any, index: int) -> None:
        """Select the pressed clip and prepare a layer or position drag."""
        self._on_select(index)
        self._drag_block = self._by_index.get(index)
        self._drag_index = index
        segment = self.project.timeline[index] if 0 <= index < len(self.project.timeline) else {}
        self._drag_layer = int(segment.get("layer") or 0)
        self._drag_edge = ""
        self._drag_slide = False
        self._drag_moved = False
        self._hover_layer = None
        self._drag_start_x = int(getattr(event, "x_root", 0))
        self._drag_start_y = int(getattr(event, "y_root", 0))
        block = self._drag_block
        self._drag_x = block.winfo_x() if block is not None and block.winfo_exists() else 0

    def _begin_edge_drag(self, event: Any, index: int, edge: str) -> None:
        """Select the pressed clip and prepare an edge trim drag."""
        self._on_select(index)
        block = self._by_index.get(index)
        self._drag_block = block
        self._drag_index = index
        self._drag_edge = edge
        self._drag_moved = False
        self._drag_start_x = int(getattr(event, "x_root", 0))
        if block is not None and index < len(self.project.timeline):
            segment = self.project.timeline[index]
            self._drag_start_range = (
                float(segment.get("start") or 0.0),
                float(segment.get("end") or 0.0),
            )
            self._drag_range = self._drag_start_range

    def _body_drag_motion(self, event: Any) -> None:
        """Track the pointer: over another row it targets that lane, else it slides."""
        block = self._drag_block
        if block is None or not block.winfo_exists():
            self._abort_drag()
            return
        pointer_x = int(getattr(event, "x_root", 0))
        pointer_y = int(getattr(event, "y_root", 0))
        delta_x = pointer_x - self._drag_start_x
        delta_y = pointer_y - self._drag_start_y
        if not self._drag_moved:
            if abs(delta_x) < DRAG_THRESHOLD and abs(delta_y) < DRAG_THRESHOLD:
                return
            self._drag_moved = True
            block.configure(fg_color=DRAG_FG, border_width=2, border_color=ACCENT)
        self._auto_scroll(pointer_x)
        self._highlight_row(pointer_y)
        if self._hover_layer is not None:
            block.place(x=self._drag_x, y=0)
            return
        self._drag_slide = abs(delta_x) >= DRAG_THRESHOLD
        block.place(x=max(0, self._drag_x + delta_x), y=0)

    def _highlight_row(self, pointer_y: int) -> None:
        """Highlight the layer row under the pointer during a drag."""
        bottom = self.project.bottom_layer()
        highest = self.project.top_layer()
        count = highest - bottom + 1
        origin_y = self._strip.winfo_rooty()
        row = int((pointer_y - origin_y) // ROW_STRIDE)
        layer = highest - row if 0 <= row < count else None
        if layer == self._drag_layer:
            layer = None
        self._hover_layer = layer
        self._mark_row(layer)

    def _mark_row(self, layer: int | None) -> None:
        """Highlight one layer row, clearing the others."""
        for key, row in self._rows.items():
            if not row.winfo_exists():
                continue
            row.configure(fg_color=ACCENT if key == layer else "transparent")

    def _build_block(
        self, index: int, segment: dict, row: ctk.CTkFrame
    ) -> ctk.CTkFrame:
        """Create one clip block placed at its absolute time inside a layer row."""
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or 0.0)
        length = max(0.0, end - start)
        abs_start = float(segment.get("abs_start") or 0.0)
        x = int(self._x_for_time(abs_start))
        audio_only = str(segment.get("kind") or "video") == "audio"
        block = ctk.CTkFrame(
            row,
            width=self._width_for(length),
            height=BLOCK_HEIGHT,
            corner_radius=6,
            fg_color=AUDIO_FG if audio_only else None,
        )
        block.pack_propagate(False)
        block.place(x=x, y=0)
        block.segment_index = index
        block.layer = int(segment.get("layer") or 0)
        block.default_fg = block.cget("fg_color")
        handle_left = ctk.CTkFrame(
            block,
            width=EDGE_WIDTH,
            corner_radius=0,
            fg_color=HANDLE_FG,
            cursor="sb_h_double_arrow",
        )
        handle_left.pack(side="left", fill="y")
        handle_right = ctk.CTkFrame(
            block,
            width=EDGE_WIDTH,
            corner_radius=0,
            fg_color=HANDLE_FG,
            cursor="sb_h_double_arrow",
        )
        handle_right.pack(side="right", fill="y")
        body = ctk.CTkFrame(block, fg_color="transparent")
        body.pack(side="left", fill="both", expand=True)
        name = ctk.CTkLabel(
            body,
            text=_short_name(segment.get("name")),
            anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        name.pack(fill="x", padx=6, pady=(8, 0))
        length_label = ctk.CTkLabel(body, text=_length_text(length), anchor="w")
        length_label.pack(fill="x", padx=6, pady=(2, 0))
        range_label = ctk.CTkLabel(
            body,
            text=_range_text(start, end),
            anchor="w",
            text_color=("gray40", "gray60"),
        )
        range_label.pack(fill="x", padx=6, pady=(2, 0))
        block.length_label = length_label
        block.range_label = range_label
        for widget in (block, body, name, length_label, range_label):
            widget.bind("<Button-1>", lambda event, item=index: self._begin_body_drag(event, item))
            widget.bind("<B1-Motion>", self._body_drag_motion)
            widget.bind("<ButtonRelease-1>", self._end_body_drag)
            widget.bind("<Double-Button-1>", lambda _event, item=index: self._play(item))
            for sequence in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
                widget.bind(sequence, lambda event, item=index: self._show_context(event, item))
        for widget, edge in ((handle_left, "left"), (handle_right, "right")):
            widget.bind(
                "<Button-1>",
                lambda event, item=index, side=edge: self._begin_edge_drag(event, item, side),
            )
            widget.bind("<B1-Motion>", self._edge_drag_motion)
            widget.bind("<ButtonRelease-1>", self._end_edge_drag)
        return block

    def _edge_drag_motion(self, event: Any) -> None:
        """Resize the clip block while its in or out point is dragged."""
        block = self._drag_block
        if block is None or not block.winfo_exists() or self._drag_index is None:
            self._abort_drag()
            return
        pointer_x = int(getattr(event, "x_root", 0))
        if not self._drag_moved:
            if abs(pointer_x - self._drag_start_x) < DRAG_THRESHOLD:
                return
            self._drag_moved = True
            block.configure(border_width=2, border_color=ACCENT)
        self._auto_scroll(pointer_x)
        delta = (pointer_x - self._drag_start_x) / self._pixels_per_second
        start, end = self._drag_start_range
        if self._drag_edge == "left":
            new_start = min(max(0.0, start + delta), end - MIN_LENGTH)
            new_range = (new_start, end)
        else:
            segment = self.project.timeline[self._drag_index]
            limit = self._source_duration(segment.get("path"))
            new_end = max(start + MIN_LENGTH, end + delta)
            if limit > 0:
                new_end = min(new_end, limit)
            new_range = (start, new_end)
        self._drag_range = new_range
        self._update_block(block, new_range)

    def _end_body_drag(self, event: Any = None) -> None:
        """Commit a layer change when released on another row, else a sideways slide."""
        block = self._drag_block
        moved = self._drag_moved
        index = self._drag_index
        layer = self._drag_layer
        target_layer = self._hover_layer
        sliding = self._drag_slide
        self._drag_block = None
        self._drag_moved = False
        self._drag_slide = False
        self._drag_index = None
        self._hover_layer = None
        self._mark_row(None)
        if block is not None and block.winfo_exists():
            default_fg = getattr(block, "default_fg", None)
            if isinstance(default_fg, (str, tuple)):
                block.configure(fg_color=default_fg)
            block.configure(border_width=0)
            block.place(x=self._drag_x, y=0)
        if not moved or index is None:
            self._apply_selection()
            return
        if target_layer is None or target_layer == layer:
            if not sliding:
                self._apply_selection()
                return
            delta = (int(getattr(event, "x_root", 0)) - self._drag_start_x) / self._pixels_per_second
            segment = self.project.timeline[index]
            lead = max(0.0, float(segment.get("lead") or 0.0) + delta)
            try:
                updated = self.project.set_segment_lead(index, lead)
            except (ValueError, OSError) as exc:
                self._on_change(str(exc), None)
                return
            self._on_change(
                f"Moved {updated.get('name', 'clip')} to {updated['abs_start']:.2f}s.", index
            )
            return
        try:
            segment = self.project.set_segment_layer(index, target_layer)
        except (ValueError, OSError) as exc:
            self._on_change(str(exc), None)
            return
        new_index = self.project.timeline.index(segment)
        self._on_change(
            f"Moved {segment.get('name', 'clip')} to layer {target_layer}.", new_index
        )

    def _end_edge_drag(self, _event: Any = None) -> None:
        """Commit a trimmed in or out point to the project timeline."""
        block = self._drag_block
        moved = self._drag_moved
        index = self._drag_index
        self._drag_block = None
        self._drag_moved = False
        self._drag_index = None
        if block is None or not block.winfo_exists() or index is None:
            return
        block.configure(border_width=0)
        if not moved:
            self._apply_selection()
            return
        start, end = self._drag_range
        try:
            segment = self.project.set_segment_range(index, start=start, end=end)
        except (ValueError, OSError) as exc:
            self._on_change(str(exc), None)
            return
        name = segment.get("name", "clip")
        self._on_change(
            f"Trimmed {name} to {segment['start']:.2f}s - {segment['end']:.2f}s.", index
        )

    def _play(self, index: int) -> None:
        """Ask the view to preview playback from a double-clicked clip."""
        if self._on_play is not None:
            self._on_play(index)

    def _show_context(self, event: Any, index: int) -> None:
        """Select the clip and hand a right-click to the view."""
        self._on_select(index)
        if self._on_context is not None:
            self._on_context(index, event)

    def _source_duration(self, path: Any) -> float:
        """Return the source clip duration from the media pool or a probe."""
        key = str(path)
        if key in self._durations:
            return self._durations[key]
        duration = 0.0
        for clip in self.project.media:
            if str(clip.get("path")) == key:
                duration = float(clip.get("duration") or 0.0)
                break
        if duration <= 0:
            try:
                duration = ff.media_duration(ff.probe(key))
            except (ValueError, OSError):
                duration = 0.0
        self._durations[key] = duration
        return duration

    def _update_block(self, block: ctk.CTkFrame, clip_range: tuple[float, float]) -> None:
        """Resize a block and refresh its labels for a dragged range."""
        start, end = clip_range
        length = max(0.0, end - start)
        block.configure(width=self._width_for(length))
        length_label = getattr(block, "length_label", None)
        if length_label is not None and length_label.winfo_exists():
            length_label.configure(text=_length_text(length))
        range_label = getattr(block, "range_label", None)
        if range_label is not None and range_label.winfo_exists():
            range_label.configure(text=_range_text(start, end))
        self.set_playhead(self._playhead)

    def _width_for(self, length: float) -> int:
        """Return the pixel width for a clip length."""
        return max(MIN_BLOCK_WIDTH, int(length * self._pixels_per_second))

    def _x_for_time(self, position: float) -> float:
        """Return the x offset inside the board for a timeline position."""
        return LABEL_WIDTH + max(0.0, float(position)) * self._pixels_per_second


def _length_text(length: float) -> str:
    """Format a clip length in seconds."""
    return f"{length:.2f}s"


def _range_text(start: float, end: float) -> str:
    """Format a clip in and out point."""
    return f"in {start:.2f}  out {end:.2f}"


def _short_name(name: Any) -> str:
    """Shorten a clip name so it fits inside a block."""
    text = str(name or "clip")
    return text if len(text) <= 18 else f"{text[:15]}..."
