"""Horizontal clip strip: FCP-style draggable, trimmable timeline blocks."""

from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

from tools import ffmpeg_utils as ff

BLOCK_HEIGHT = 96
BLOCK_GAP = 4
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


class ClipStrip(ctk.CTkFrame):
    """Horizontal strip of clip blocks with drag reordering and edge trimming."""

    def __init__(
        self,
        master: Any,
        project: Any,
        on_select: Callable[[int], None],
        on_change: Callable[[str, int | None], None],
        on_play: Callable[[int], None] | None = None,
        on_context: Callable[[int, Any], None] | None = None,
    ) -> None:
        """Create the strip, its scrollable block area, and the playhead line."""
        super().__init__(master, fg_color="transparent")
        self.project = project
        self._on_select = on_select
        self._on_change = on_change
        self._on_play = on_play
        self._on_context = on_context
        self._pixels_per_second = float(DEFAULT_ZOOM)
        self._blocks: list[ctk.CTkFrame] = []
        self._durations: dict[str, float] = {}
        self._selected: int | None = None
        self._playhead = 0.0
        self._empty_label: ctk.CTkLabel | None = None
        self._drag_block: ctk.CTkFrame | None = None
        self._drag_index: int | None = None
        self._drag_edge = ""
        self._drag_start_x = 0
        self._drag_start_range = (0.0, 0.0)
        self._drag_range = (0.0, 0.0)
        self._drag_moved = False
        self._strip = ctk.CTkScrollableFrame(
            self, orientation="horizontal", height=BLOCK_HEIGHT + 26
        )
        self._strip.pack(fill="both", expand=True)
        self._playhead_line = ctk.CTkFrame(
            self._strip, width=2, height=BLOCK_HEIGHT, fg_color=PLAYHEAD_FG
        )
        self.refresh()

    def refresh(self) -> None:
        """Rebuild every clip block from the project timeline."""
        self._abort_drag()
        for block in self._blocks:
            block.destroy()
        self._blocks = []
        if self._empty_label is not None:
            self._empty_label.destroy()
            self._empty_label = None
        timeline = self.project.timeline
        if not timeline:
            self._empty_label = ctk.CTkLabel(
                self._strip,
                text="Timeline is empty - add clips from the Clips view or with Add Clip.",
                anchor="w",
            )
            self._empty_label.pack(side="left", padx=12, pady=12)
        for index, segment in enumerate(timeline):
            self._blocks.append(self._build_block(index, segment))
        self._playhead_line.lift()
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
        self._playhead_line.place(x=int(self._x_for_time(self._playhead)), y=8)

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

    def _abort_drag(self) -> None:
        """Drop any drag state, for example when the strip is rebuilt."""
        self._drag_block = None
        self._drag_index = None
        self._drag_edge = ""
        self._drag_moved = False

    def _apply_selection(self) -> None:
        """Draw the selection highlight around the selected block."""
        for index, block in enumerate(self._blocks):
            if not block.winfo_exists():
                continue
            if index == self._selected:
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
        """Select the pressed clip and prepare a reorder drag."""
        self._on_select(index)
        self._drag_block = self._blocks[index] if 0 <= index < len(self._blocks) else None
        self._drag_index = index
        self._drag_edge = ""
        self._drag_moved = False
        self._drag_start_x = int(getattr(event, "x_root", 0))

    def _begin_edge_drag(self, event: Any, index: int, edge: str) -> None:
        """Select the pressed clip and prepare an edge trim drag."""
        self._on_select(index)
        block = self._blocks[index] if 0 <= index < len(self._blocks) else None
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
        """Shift the dragged block between its neighbours while moving."""
        block = self._drag_block
        if block is None or not block.winfo_exists():
            self._abort_drag()
            return
        pointer_x = int(getattr(event, "x_root", 0))
        if not self._drag_moved:
            if abs(pointer_x - self._drag_start_x) < DRAG_THRESHOLD:
                return
            self._drag_moved = True
            block.configure(fg_color=DRAG_FG, border_width=2, border_color=ACCENT)
        self._auto_scroll(pointer_x)
        self._reorder_to(pointer_x)

    def _build_block(self, index: int, segment: dict) -> ctk.CTkFrame:
        """Create one clip block sized by its duration, with trim handles."""
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or 0.0)
        length = max(0.0, end - start)
        block = ctk.CTkFrame(
            self._strip,
            width=self._width_for(length),
            height=BLOCK_HEIGHT,
            corner_radius=6,
        )
        block.pack(side="left", padx=(0, BLOCK_GAP), pady=8)
        block.pack_propagate(False)
        block.segment_index = index
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
        name.pack(fill="x", padx=6, pady=(10, 0))
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

    def _end_body_drag(self, _event: Any = None) -> None:
        """Commit a reordered clip to the project timeline."""
        block = self._drag_block
        moved = self._drag_moved
        index = self._drag_index
        self._drag_block = None
        self._drag_moved = False
        if block is None or not block.winfo_exists():
            return
        default_fg = getattr(block, "default_fg", None)
        if isinstance(default_fg, (str, tuple)):
            block.configure(fg_color=default_fg)
        block.configure(border_width=0)
        if not moved or index is None:
            self._apply_selection()
            self._drag_index = None
            return
        new_index = self._strip.pack_slaves().index(block)
        self._drag_index = None
        if new_index == index:
            self._apply_selection()
            return
        try:
            segment = self.project.move_segment(index, new_index)
        except (ValueError, OSError) as exc:
            self._on_change(str(exc), None)
            return
        name = segment.get("name", "clip")
        self._on_change(f"Moved {name} to position {new_index + 1}.", new_index)

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

    def _reorder_to(self, pointer_x: int) -> None:
        """Repack the dragged block so clips shift while dragging."""
        block = self._drag_block
        target = None
        for child in self._strip.pack_slaves():
            if child is block:
                continue
            middle = child.winfo_rootx() + child.winfo_width() // 2
            if pointer_x < middle:
                target = child
                break
        slaves = self._strip.pack_slaves()
        position = slaves.index(block)
        following = slaves[position + 1] if position + 1 < len(slaves) else None
        if target is following:
            return
        if target is None:
            anchor = slaves[-1]
            if anchor is not block:
                block.pack(after=anchor, side="left", padx=(0, BLOCK_GAP), pady=8)
        else:
            block.pack(before=target, side="left", padx=(0, BLOCK_GAP), pady=8)

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
        """Return the x offset inside the strip for a timeline position."""
        x = 0.0
        cumulative = 0.0
        timeline = self.project.timeline
        for index, segment in enumerate(timeline):
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or 0.0)
            length = max(0.0, end - start)
            width = self._width_for(length)
            if position <= cumulative + length or index == len(timeline) - 1:
                if length <= 0:
                    return x
                ratio = min(max((position - cumulative) / length, 0.0), 1.0)
                return x + ratio * width
            x += width + BLOCK_GAP
            cumulative += length
        return x


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
