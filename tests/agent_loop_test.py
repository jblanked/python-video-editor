"""Agent loop test: verify tool calls are dispatched and results returned."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import agent  # noqa: E402

SAMPLE = "/tmp/ve_test/sample_a.mp4"


def main() -> int:
    """Run a mocked conversation through the agent loop."""
    if not Path(SAMPLE).exists():
        print("sample media missing, run tests/smoke_test.py first")
        return 2
    payloads: list[dict] = []
    events: list[dict] = []
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "get_video_info",
                                    "arguments": json.dumps({"path": SAMPLE}),
                                },
                            },
                            {
                                "id": "call_2",
                                "type": "function",
                                "function": {
                                    "name": "trim_video",
                                    "arguments": json.dumps(
                                        {"path": SAMPLE, "start": "0", "end": "1"}
                                    ),
                                },
                            },
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"role": "assistant", "content": "Trimmed the clip."}}]},
    ]

    def fake_post(payload: dict, provider_id: str | None = None, timeout: int = 0) -> dict:
        payloads.append(payload)
        return responses.pop(0)

    real_post = agent.post_chat_completion
    agent.post_chat_completion = fake_post
    agent.get_provider = lambda provider_id=None: {"vendor": "mock", "models": ["mock-model"]}

    reply = agent.run_agent(
        "inspect and trim the clip",
        provider_id="mock",
        model="mock-model",
        on_event=events.append,
        context='{"timeline": []}',
    )
    assert reply == "Trimmed the clip.", reply
    assert len(payloads) == 2, f"expected two LLM calls, got {len(payloads)}"
    assert "tools" in payloads[0] and "tool_choice" in payloads[0], "every call must send tools"
    assert "tools" in payloads[1], "later passes keep tools so follow-up calls still work"
    assert payloads[0]["tools"] == payloads[1]["tools"]
    tool_messages = [item for item in payloads[1]["messages"] if item.get("role") == "tool"]
    assert len(tool_messages) == 2, "both tool results must be returned to the model"
    assert json.loads(tool_messages[0]["content"])["success"] is True
    assert Path(json.loads(tool_messages[1]["content"])["output"]).exists()
    kinds = [event["type"] for event in events]
    assert kinds.count("tool_start") == 2 and kinds.count("tool_end") == 2, kinds

    # Error bodies are unwrapped, and tools with reasoning retry with reasoning off.
    assert agent._error_message('{"error": {"message": "boom"}}') == "boom"
    assert agent._error_message("plain   text") == "plain text"
    reasoning = '{"error": {"message": "Function tools with reasoning_effort are not supported"}}'
    assert agent._needs_reasoning_none(SimpleNamespace(status_code=400, text=reasoning)) is True
    assert agent._needs_reasoning_none(SimpleNamespace(status_code=400, text="other")) is False
    assert agent._needs_reasoning_none(SimpleNamespace(status_code=200, text=reasoning)) is False

    calls: list[dict] = []

    class FakeResponse:
        def __init__(self, status_code: int, text: str) -> None:
            self.status_code = status_code
            self.text = text

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_request(url, headers=None, json=None, timeout=None) -> FakeResponse:
        body = dict(json or {})
        calls.append(body)
        if "reasoning_effort" not in body:
            return FakeResponse(400, reasoning)
        return FakeResponse(200, "{}")

    real_requests = agent.requests
    real_provider = agent.get_provider
    agent.requests = SimpleNamespace(post=fake_request)
    agent.get_provider = lambda provider_id=None: {
        "vendor": "mock",
        "models": ["m"],
        "apiKey": "k",
        "endpoint": "https://example.test/v1/chat/completions",
    }
    try:
        data = real_post({"model": "m", "messages": [], "tools": []})
        assert data["choices"][0]["message"]["content"] == "ok"
        assert len(calls) == 2, calls
        assert "reasoning_effort" not in calls[0], calls[0]
        assert calls[1].get("reasoning_effort") == "none", calls[1]

        calls.clear()
        agent.get_provider = lambda provider_id=None: {
            "vendor": "mock",
            "models": ["m"],
            "apiKey": "k",
            "endpoint": "https://example.test/v1/chat/completions",
            "options": {"reasoning_effort": "none", "temperature": 0.2},
        }
        real_post({"model": "m", "messages": []})
        assert len(calls) == 1, calls
        assert calls[0]["reasoning_effort"] == "none" and calls[0]["temperature"] == 0.2
        assert len(calls[0]["messages"]) == 0
    finally:
        agent.requests = real_requests
        agent.get_provider = real_provider

    print("agent loop test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
