"""Executes registered tools: argument coercion, output defaults, and errors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools import context
from tools.registry import Op, Param, find_op, first_source, output_extension
from tools.results import error_result


def coerce_args(op: Op, args: dict | None = None) -> dict:
    """Validate raw tool arguments and convert them into function keyword arguments."""
    supplied = args or {}
    kwargs: dict[str, Any] = {}
    for param in op.params:
        raw = supplied.get(param.name)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if param.required:
                raise ValueError(f"Missing required parameter: {param.name}")
            continue
        kwargs[param.name] = _convert(param, raw)
    return kwargs


def default_output_path(op: Op, args: dict | None = None) -> Path:
    """Build the default output path for an operation inside the output folder."""
    source = first_source(args or {})
    stem = Path(source).stem if source else "timeline"
    name = f"{stem}_{op.output_tag or op.name}"
    if op.output_kind == "dir":
        return context.get_output_dir() / name
    return context.get_output_dir() / f"{name}{output_extension(op, args or {})}"


def execute_tool(name: str, args: dict | None = None) -> dict:
    """Run a registered tool with validated arguments and return its result dict."""
    op = find_op(name)
    if op is None:
        return error_result(f"Unknown tool: {name}")
    try:
        kwargs = coerce_args(op, args)
    except ValueError as exc:
        return error_result(str(exc))
    if op.output_kind in {"file", "dir"}:
        target = "output" if op.output_kind == "file" else "output_dir"
        if not kwargs.get(target):
            try:
                kwargs[target] = str(default_output_path(op, kwargs))
            except (ValueError, OSError) as exc:
                return error_result(str(exc))
    try:
        result = op.function(**kwargs)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return error_result(f"{op.name} failed: {exc}")
    if not isinstance(result, dict):
        return error_result(f"{op.name} returned an unexpected result.")
    return result


def execute_tool_json(name: str, args: dict | None = None) -> str:
    """Run a tool and serialise the result as JSON for agent messages."""
    return json.dumps(execute_tool(name, args), default=str, ensure_ascii=False)


def _convert(param: Param, raw: Any) -> Any:
    """Convert one raw argument value based on the parameter type."""
    if param.type == "integer":
        try:
            return int(float(raw))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{param.name} must be a whole number.") from exc
    if param.type == "number":
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{param.name} must be a number.") from exc
    if param.type == "boolean":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if param.type == "choice":
        text = str(raw).strip()
        for choice in param.choices:
            if text.lower() == choice.lower():
                return choice
        raise ValueError(f"{param.name} must be one of: {', '.join(param.choices)}.")
    if param.type == "paths":
        if isinstance(raw, (list, tuple)):
            items = [str(item).strip() for item in raw if str(item).strip()]
        else:
            text = str(raw).replace(",", "\n")
            items = [line.strip() for line in text.splitlines() if line.strip()]
        if len(items) < 2:
            raise ValueError("Provide at least two clips to join.")
        return items
    return str(raw).strip()
