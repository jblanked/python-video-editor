"""LLM access for the AI assistant: provider config, chat completions, agent loop."""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any, Callable

import requests

from tools import ffmpeg_utils as ff
from tools.dispatch import execute_tool

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_PATH = os.path.dirname(BASE_DIR)
DEFAULT_PROVIDER_ID = "openai"
MAX_TOOL_ITERATIONS = 50
REQUEST_TIMEOUT = 180

SYSTEM_PROMPT = """
You are the AI assistant inside a desktop video editor. You edit local video files and the
open project timeline by calling the provided tools. Use the timeline tools when the user
refers to clips in the editor timeline (adding, trimming, reordering, clearing, rendering);
use the file tools for one-off edits that produce new files. Call get_video_info when unsure
about a clip and timeline_get_state before editing the timeline. Use transcribe_timeline to
read the timeline script, order clips by what was said, and find silence ranges or script duplicates to trim
away; transcribe_clip does the same for a single file. Call timeline_remove_silence to cut
every silent gap out of the timeline in one step. Time values accept seconds,
MM:SS, or HH:MM:SS. After an edit, report the output file path. Keep replies short and
practical, and use tools instead of guessing."
"""


def get_llm_config() -> list[dict]:
    """Load the provider list from settings/llm.json, copying the default file when missing."""
    path = os.path.join(ROOT_PATH, "settings", "llm.json")
    if not os.path.exists(path):
        default_path = os.path.join(ROOT_PATH, "settings", "llm_base.json")
        if not os.path.exists(default_path):
            raise FileNotFoundError(f"Default LLM config not found at {default_path}")
        shutil.copy(default_path, path)
    with open(path, "r", encoding="utf-8") as handle:
        return json.loads(handle.read())

def get_provider(provider_id: str | None = None) -> dict:
    """Return the provider entry for a vendor id, defaulting to the configured default."""
    providers = get_llm_config()
    target = provider_id or DEFAULT_PROVIDER_ID
    for provider in providers:
        if provider.get("vendor") == target:
            return provider
    available = [item.get("vendor") for item in providers]
    raise ValueError(f"Unknown provider {target!r}. Available: {available}")


def get_provider_models(provider_id: str | None = None) -> list[str]:
    """Return the model names listed for a provider."""
    return list(get_provider(provider_id).get("models") or [])


def list_providers() -> list[dict]:
    """Return every configured provider entry."""
    return get_llm_config()


def post_chat_completion(
    payload: dict[str, Any], provider_id: str | None = None, timeout: int = REQUEST_TIMEOUT
) -> dict[str, Any]:
    """Post a chat completion, retrying with reasoning off when tools require it."""
    provider = get_provider(provider_id)
    api_key = provider.get("apiKey")
    if not api_key:
        raise ValueError(
            f"API key for {provider.get('vendor')} is not set. Add it in the Settings view."
        )
    options = provider.get("options")
    request_payload = {**payload, **options} if isinstance(options, dict) else dict(payload)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    endpoint = provider.get("endpoint")
    response = requests.post(endpoint, headers=headers, json=request_payload, timeout=timeout)
    if _needs_reasoning_none(response):
        retry_payload = {**request_payload, "reasoning_effort": "none"}
        response = requests.post(endpoint, headers=headers, json=retry_payload, timeout=timeout)
    if response.status_code >= 400:
        raise ValueError(f"API error ({response.status_code}): {_error_message(response.text)}")
    return response.json()


def run_agent(
    user_input: str,
    provider_id: str | None = None,
    model: str | None = None,
    history: list[dict] | None = None,
    context: str = "",
    on_event: Callable[[dict], None] | None = None,
    cancel_event: Any = None,
) -> str:
    """Run the assistant loop: prompt the LLM, execute tool calls, return the final reply."""
    from tools.registry import get_openai_tools

    provider = get_provider(provider_id)
    chosen_model = model or (provider.get("models") or [""])[0]
    if not chosen_model:
        return "No model is configured for this provider."
    system_content = SYSTEM_PROMPT
    if context:
        system_content += f"\n\nCurrent editor state:\n{context}"
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
    for item in history or []:
        messages.append({"role": item.get("role", "user"), "content": item.get("content", "")})
    messages.append({"role": "user", "content": user_input})
    tools = get_openai_tools()
    emit = on_event or _noop
    ff.set_cancel_event(cancel_event)
    try:
        for _ in range(MAX_TOOL_ITERATIONS):
            if cancel_event is not None and cancel_event.is_set():
                return "Stopped."
            payload: dict[str, Any] = {
                "model": chosen_model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
            }
            emit({"type": "thinking"})
            try:
                data = post_chat_completion(payload, provider.get("vendor"))
            except (requests.RequestException, ValueError) as exc:
                return f"An error occurred during processing: {exc}"
            message = (data.get("choices") or [{}])[0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return message.get("content") or ""
            assistant_message: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
            if message.get("content") is not None:
                assistant_message["content"] = message["content"]
            messages.append(assistant_message)
            for tool_call in tool_calls:
                name = (tool_call.get("function") or {}).get("name", "")
                raw_args = (tool_call.get("function") or {}).get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}
                emit({"type": "tool_start", "name": name, "args": args})
                result = execute_tool(name, args)
                emit({"type": "tool_end", "name": name, "result": result})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "content": json.dumps(result, default=str, ensure_ascii=False),
                    }
                )
        return "Stopped: the tool loop reached its iteration limit."
    finally:
        ff.clear_cancel_event()


def save_api_key(provider_id: str, api_key: str) -> None:
    """Store an API key for a provider in settings/llm.json."""
    providers = get_llm_config()
    for provider in providers:
        if provider.get("vendor") == provider_id:
            provider["apiKey"] = api_key
    path = os.path.join(ROOT_PATH, "settings", "llm.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(providers, handle, indent=4)


def _error_message(body: str, limit: int = 300) -> str:
    """Extract a readable message from an API error body."""
    text = str(body or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:limit]
    return re.sub(r"\s+", " ", text)[:limit]


def _needs_reasoning_none(response: Any) -> bool:
    """Detect chat completions rejecting function tools with reasoning enabled."""
    if getattr(response, "status_code", 0) != 400:
        return False
    text = str(getattr(response, "text", "") or "").lower()
    return "reasoning_effort" in text and "tool" in text


def _noop(event: dict) -> None:
    """Ignore agent events when no callback is supplied."""
    return None
