"""Assistant view: chat with the AI agent that drives the editing tools."""

from __future__ import annotations

import json
import threading
from typing import Any

import customtkinter as ctk

from tools import agent
from tools.dispatch import execute_tool_json


class AssistantView(ctk.CTkFrame):
    """Chat panel that prompts the LLM agent and executes its tool calls."""

    def __init__(self, master: Any, app: Any) -> None:
        """Create the assistant view and show the saved model choice."""
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._history: list[dict] = []
        self._busy = False
        self._cancel = threading.Event()
        self._providers = _safe_providers()
        self._build_ui()
        self.refresh()

    def _append(self, speaker: str, text: str) -> None:
        """Append one message to the transcript."""
        self.chat.configure(state="normal")
        self.chat.insert("end", f"{speaker}\n{text}\n\n")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _build_ui(self) -> None:
        """Create the header, transcript, and composer."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        ctk.CTkLabel(
            self, text="AI Assistant", font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=4, pady=(2, 4))
        top = ctk.CTkFrame(self)
        top.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.model_status = ctk.CTkLabel(top, text="", anchor="w")
        self.model_status.pack(side="left", padx=8, pady=6)
        self.stop_button = ctk.CTkButton(top, text="Stop", width=70, state="disabled", command=self._stop)
        self.stop_button.pack(side="right", padx=8)
        ctk.CTkButton(top, text="Clear chat", width=90, command=self._clear_chat).pack(side="right")

        self.chat = ctk.CTkTextbox(self, wrap="word", state="disabled", border_width=0)
        self.chat.grid(row=2, column=0, sticky="nsew", padx=4)
        self.progress = ctk.CTkProgressBar(self, mode="indeterminate", height=4)
        self.progress.grid(row=3, column=0, sticky="ew", padx=4, pady=(4, 0))

        composer = ctk.CTkFrame(
            self, corner_radius=10, border_width=1, border_color=("gray70", "gray40")
        )
        composer.grid(row=4, column=0, sticky="ew", padx=4, pady=(6, 4))
        composer.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            composer, text="Message", anchor="w", text_color=("gray40", "gray60")
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(6, 0))
        self.input = ctk.CTkTextbox(composer, height=68, wrap="word", border_width=0)
        self.input.grid(row=1, column=0, sticky="ew", padx=(10, 6), pady=(2, 2))
        self.input.bind("<Control-Return>", self._on_send)
        self.input.bind("<Command-Return>", self._on_send)
        self.send_button = ctk.CTkButton(
            composer, text="Send", width=96, height=68, command=self._on_send
        )
        self.send_button.grid(row=1, column=1, sticky="e", padx=(0, 10), pady=(2, 2))
        ctk.CTkLabel(
            composer,
            text="Describe an edit, for example: trim the first clip to 5 seconds. Ctrl+Enter sends.",
            anchor="w",
            text_color=("gray40", "gray60"),
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))
        self._append(
            "Assistant",
            "Ask for an edit and I will call the editor tools. Use Ctrl+Enter to send. "
            "The provider, model, and API key come from Settings.",
        )

    def _clear_chat(self) -> None:
        """Clear the transcript and the conversation history."""
        self._history = []
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")

    def _finish(self, prompt: str, reply: str) -> None:
        """Show the final reply and store the turn in history."""
        self._busy = False
        self.progress.stop()
        self.send_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._append("Assistant", reply or "(no reply)")
        self._history.append({"role": "user", "content": prompt})
        self._history.append({"role": "assistant", "content": reply or ""})

    def _on_send(self, _event: Any = None) -> str:
        """Start an agent run for the typed prompt."""
        if self._busy:
            return "break"
        prompt = self.input.get("1.0", "end").strip()
        if not prompt:
            return "break"
        self.input.delete("1.0", "end")
        self._append("You", prompt)
        self._busy = True
        self._cancel.clear()
        self.send_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.start()
        threading.Thread(target=self._worker, args=(prompt,), daemon=True).start()
        return "break"

    def refresh(self) -> None:
        """Show the provider and model configured in the Settings view."""
        if not self._providers:
            self.model_status.configure(text="No providers found in settings/llm_base.json.")
            return
        provider_id, model = self._settings_selection()
        if not model:
            models = agent.get_provider_models(provider_id)
            model = models[0] if models else "none"
        self.model_status.configure(text=f"Using {provider_id} / {model} - change in Settings")

    def _settings_selection(self) -> tuple[str | None, str | None]:
        """Resolve the provider and model saved by the Settings view."""
        provider_id = str(self.app.preferences.get("provider") or "")
        vendors = [str(item.get("vendor") or "") for item in self._providers]
        if provider_id not in vendors:
            provider_id = vendors[0] if vendors else ""
        model = str(self.app.preferences.get("model") or "")
        return provider_id or None, model or None

    def _show_event(self, event: dict) -> None:
        """Display agent tool activity in the transcript and refresh the views."""
        kind = event.get("type")
        if kind == "tool_start":
            args = json.dumps(event.get("args") or {}, ensure_ascii=False)
            self._append("Tool call", f"{event.get('name')} {args[:220]}")
        elif kind == "tool_end":
            result = event.get("result") or {}
            state = "ok" if result.get("success") else "failed"
            self._append("Tool result", f"[{state}] {result.get('message', '')}")
            self.app.refresh_views()

    def _stop(self) -> None:
        """Request cancellation of the running agent."""
        self._cancel.set()
        self._append("Assistant", "Stopping...")

    def _worker(self, prompt: str) -> None:
        """Run the agent loop off the UI thread and stream events back."""
        state = execute_tool_json("timeline_get_state", {})
        provider_id, model = self._settings_selection()
        try:
            reply = agent.run_agent(
                prompt,
                provider_id=provider_id,
                model=model,
                history=list(self._history),
                context=state,
                on_event=lambda event: self.after(0, self._show_event, event),
                cancel_event=self._cancel,
            )
        except (ValueError, OSError) as exc:
            reply = f"Could not start the assistant: {exc}"
        self.after(0, lambda: self._finish(prompt, reply))


def _safe_providers() -> list[dict]:
    """Load providers, returning an empty list when the config is broken."""
    try:
        return agent.list_providers()
    except (OSError, ValueError):
        return []
