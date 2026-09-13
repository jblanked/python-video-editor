"""Application shell: window, sidebar navigation, and shared app state."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import customtkinter as ctk

from tools import context, ffmpeg_utils, playback
from tools.project import Project

ROOT_DIR = Path(__file__).resolve().parents[1]
PREFS_FILE = ROOT_DIR / "settings" / "preferences.json"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "output"

NAV_ITEMS = (
    ("clips", "Clips"),
    ("timeline", "Timeline"),
    ("assistant", "AI Assistant"),
    ("settings", "Settings"),
)


class VideoEditorApp(ctk.CTk):
    """Main window hosting the clips, timeline, assistant, and settings views."""

    def __init__(self) -> None:
        """Build the window, create the views, and restore saved preferences."""
        super().__init__()
        self.preferences = _load_preferences()
        ctk.set_appearance_mode(self.preferences.get("appearance", "Dark"))
        ctk.set_default_color_theme("blue")
        self.title("Video Editor")
        self.geometry("1400x880")
        self.minsize(1120, 720)
        self.project = Project()
        context.set_project(self.project)
        context.set_output_dir(self.preferences.get("output_dir") or str(DEFAULT_OUTPUT_DIR))
        context.set_provider_id(self.preferences.get("provider"))
        context.set_transcribe_provider(self.preferences.get("transcribe_provider"))
        context.set_transcribe_model(self.preferences.get("transcribe_model"))
        ffmpeg_utils.set_ffmpeg_path(self.preferences.get("ffmpeg_path", ""))
        self._views: dict[str, ctk.CTkFrame] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self.status_label: ctk.CTkLabel | None = None
        self._build_ui()
        self.show_view("clips")
        self.after(300, self._check_ffmpeg)

    def destroy(self) -> None:
        """Stop preview playback and close the window."""
        timeline = self._views.get("timeline")
        if timeline is not None:
            shutdown = getattr(timeline, "shutdown", None)
            if callable(shutdown):
                shutdown()
        playback.stop_all()
        super().destroy()

    def open_path(self, path: str) -> None:
        """Open a file or folder with the operating system default application."""
        target = Path(str(path)).expanduser()
        if not target.exists():
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            elif os.name == "nt":
                os.startfile(str(target))  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError:
            return

    def refresh_views(self) -> None:
        """Ask every view to reload its content from the project state."""
        for view in self._views.values():
            refresh = getattr(view, "refresh", None)
            if callable(refresh):
                refresh()

    def show_view(self, name: str) -> None:
        """Show one view and highlight its navigation button."""
        for key, view in self._views.items():
            if key == name:
                view.grid()
            else:
                view.grid_remove()
        for key, button in self._nav_buttons.items():
            active = key == name
            button.configure(fg_color=("gray75", "gray25") if active else "transparent")

    def update_preferences(self, **changes: object) -> None:
        """Merge preference changes, persist them, and apply side effects."""
        self.preferences.update(changes)
        _save_preferences(self.preferences)
        if "appearance" in changes:
            ctk.set_appearance_mode(str(changes["appearance"]))
        if "output_dir" in changes and changes["output_dir"]:
            context.set_output_dir(str(changes["output_dir"]))
        if "provider" in changes:
            context.set_provider_id(str(changes["provider"] or ""))
        if "transcribe_provider" in changes:
            context.set_transcribe_provider(str(changes["transcribe_provider"] or ""))
        if "transcribe_model" in changes:
            context.set_transcribe_model(str(changes["transcribe_model"] or ""))
        if "ffmpeg_path" in changes:
            ffmpeg_utils.set_ffmpeg_path(str(changes["ffmpeg_path"] or ""))
        if "provider" in changes or "model" in changes:
            assistant = self._views.get("assistant")
            refresh = getattr(assistant, "refresh", None)
            if callable(refresh):
                refresh()

    def _build_ui(self) -> None:
        """Create the sidebar and instantiate each view."""
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        sidebar = ctk.CTkFrame(self, width=210, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.rowconfigure(len(NAV_ITEMS) + 1, weight=1)
        title = ctk.CTkLabel(sidebar, text="Video Editor", font=ctk.CTkFont(size=20, weight="bold"))
        title.grid(row=0, column=0, padx=18, pady=(22, 16), sticky="w")
        for index, (key, label) in enumerate(NAV_ITEMS, start=1):
            button = ctk.CTkButton(
                sidebar,
                text=label,
                anchor="w",
                corner_radius=8,
                fg_color="transparent",
                command=lambda view_key=key: self.show_view(view_key),
            )
            button.grid(row=index, column=0, padx=12, pady=3, sticky="ew")
            self._nav_buttons[key] = button
        self.status_label = ctk.CTkLabel(sidebar, text="Checking ffmpeg...", anchor="w", wraplength=180)
        self.status_label.grid(row=len(NAV_ITEMS) + 2, column=0, padx=18, pady=14, sticky="sw")

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        from views.assistant import AssistantView
        from views.clips import ClipsView
        from views.settings import SettingsView
        from views.timeline import TimelineView

        self._views = {
            "clips": ClipsView(container, self),
            "timeline": TimelineView(container, self),
            "assistant": AssistantView(container, self),
            "settings": SettingsView(container, self),
        }
        for view in self._views.values():
            view.grid(row=0, column=0, sticky="nsew")

    def _check_ffmpeg(self) -> None:
        """Show the detected ffmpeg version in the sidebar."""
        if self.status_label is None:
            return
        available, version = ffmpeg_utils.binary_status()
        if available:
            label = version.split(" Copyright")[0].replace("ffmpeg version ", "ffmpeg ")
            self.status_label.configure(text=label)
        else:
            self.status_label.configure(text=f"ffmpeg not found: {version[:60]}")


def _load_preferences() -> dict:
    """Read saved preferences from settings/preferences.json."""
    try:
        return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_preferences(preferences: dict) -> None:
    """Persist preferences to settings/preferences.json."""
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(json.dumps(preferences, indent=2), encoding="utf-8")
