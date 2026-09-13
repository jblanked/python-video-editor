"""Module-level state shared by tools: active project and output directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_state: dict[str, Any] = {
    "project": None,
    "output_dir": None,
    "provider": None,
    "transcribe_provider": None,
    "transcribe_model": None,
}


def get_output_dir() -> Path:
    """Return the configured output directory, creating it when missing."""
    current = _state.get("output_dir")
    directory = Path(current) if current else Path.home() / "video-editor-output"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def get_project() -> Any:
    """Return the active project instance, or None when running headless."""
    return _state.get("project")


def get_provider_id() -> str | None:
    """Return the AI provider chosen in the Settings view, if any."""
    return _state.get("provider")


def get_transcribe_model() -> str:
    """Return the transcription model chosen in the Settings view."""
    return str(_state.get("transcribe_model") or "")


def get_transcribe_provider() -> str | None:
    """Return the transcription provider chosen in the Settings view, if any."""
    return _state.get("transcribe_provider")


def set_output_dir(path: str | Path) -> None:
    """Set the default output directory used for generated files."""
    _state["output_dir"] = str(path)


def set_project(project: Any) -> None:
    """Set the project instance shared with the GUI views."""
    _state["project"] = project


def set_provider_id(provider_id: str | None) -> None:
    """Set the AI provider used by tools that call the LLM directly."""
    _state["provider"] = str(provider_id) if provider_id else None


def set_transcribe_model(model: str | None) -> None:
    """Set the transcription model; empty means the default whisper-1."""
    _state["transcribe_model"] = str(model) if model else None


def set_transcribe_provider(provider_id: str | None) -> None:
    """Set the provider used for transcription; empty means the AI provider."""
    _state["transcribe_provider"] = str(provider_id) if provider_id else None
