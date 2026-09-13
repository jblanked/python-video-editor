"""Filesystem locations that work both from source and inside a frozen app."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Python Video Editor"


def bundle_dir() -> Path:
    """Return the folder that holds bundled, read-only files."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    """Return the user-writable folder for settings, projects, and output."""
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[1]
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME


def config_dir() -> Path:
    """Return the settings folder used for llm.json and preferences.json."""
    return data_dir() / "settings"
