"""Standard result dictionaries and input validation shared by operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def ensure_file(path: str | Path) -> Path:
    """Return an existing file as a resolved Path, raising ValueError otherwise."""
    if path is None or str(path).strip() == "":
        raise ValueError("A file path is required.")
    candidate = Path(str(path)).expanduser()
    if not candidate.is_absolute():
        candidate = candidate.resolve()
    if not candidate.exists():
        raise ValueError(f"File not found: {candidate}")
    if candidate.is_dir():
        raise ValueError(f"Expected a file but found a folder: {candidate}")
    return candidate


def error_result(message: str, **details: Any) -> dict:
    """Build a failed operation result with an optional details payload."""
    result: dict[str, Any] = {"success": False, "output": None, "message": message}
    if details:
        result["details"] = details
    return result


def success_result(output: str | Path | None, message: str, **details: Any) -> dict:
    """Build a successful operation result with the produced path and details."""
    result: dict[str, Any] = {
        "success": True,
        "output": str(output) if output is not None else None,
        "message": message,
    }
    if details:
        result["details"] = details
    return result
