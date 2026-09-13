"""Settings view: appearance, folders, ffmpeg, and LLM provider setup."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog
from typing import Any

import customtkinter as ctk

from tools import agent, context, ffmpeg_utils

APPEARANCE_MODES = ("System", "Dark", "Light")
SAME_PROVIDER_LABEL = "Same as AI provider"
DEFAULT_TRANSCRIBE_MODEL = "whisper-1"


class SettingsView(ctk.CTkFrame):
    """Preferences for appearance, output folders, ffmpeg, and the AI provider."""

    def __init__(self, master: Any, app: Any) -> None:
        """Create the settings view and load current values."""
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._providers = _safe_providers()
        self._build_ui()
        self.refresh()

    def refresh(self) -> None:
        """Reload saved values into the form fields."""
        appearance = str(self.app.preferences.get("appearance", "Dark"))
        self.appearance_menu.set(appearance if appearance in APPEARANCE_MODES else "Dark")
        output_dir = str(self.app.preferences.get("output_dir") or context.get_output_dir())
        self.output_var.set(output_dir)
        self.ffmpeg_var.set(str(self.app.preferences.get("ffmpeg_path") or ""))
        if self._providers:
            saved = str(self.app.preferences.get("provider") or "")
            vendors = [item.get("vendor", "") for item in self._providers]
            provider = saved if saved in vendors else vendors[0]
            self.provider_menu.set(provider)
            self._refresh_models(provider)
            saved_model = str(self.app.preferences.get("model") or "")
            if saved_model in (self.model_menu.cget("values") or []):
                self.model_menu.set(saved_model)
            self._load_api_key(provider)
            transcribe = str(self.app.preferences.get("transcribe_provider") or "")
            values = self.transcribe_menu.cget("values") or []
            self.transcribe_menu.set(transcribe if transcribe in values else SAME_PROVIDER_LABEL)
            self.transcribe_var.set(
                str(self.app.preferences.get("transcribe_model") or DEFAULT_TRANSCRIBE_MODEL)
            )
        self._check_binary(quiet=True)

    def _build_section(self, title: str) -> ctk.CTkFrame:
        """Create a titled section frame inside the scroll area."""
        wrapper = ctk.CTkFrame(self.content, corner_radius=8)
        wrapper.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(wrapper, text=title, font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(
            fill="x", padx=10, pady=(8, 2)
        )
        body = ctk.CTkFrame(wrapper, fg_color="transparent")
        body.pack(fill="x", padx=10, pady=(0, 10))
        return body

    def _build_ui(self) -> None:
        """Create every settings section."""
        self.content = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True)

        appearance = self._build_section("Appearance")
        ctk.CTkLabel(appearance, text="Theme").pack(side="left")
        self.appearance_menu = ctk.CTkOptionMenu(
            appearance, values=list(APPEARANCE_MODES), command=self._apply_appearance, width=120
        )
        self.appearance_menu.pack(side="left", padx=8)

        folders = self._build_section("Output folder")
        self.output_var = tk.StringVar()
        ctk.CTkEntry(folders, textvariable=self.output_var).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(folders, text="Browse", width=80, command=self._browse_output).pack(side="left", padx=6)
        ctk.CTkButton(folders, text="Save", width=70, command=self._save_output).pack(side="left")

        binary = self._build_section("FFmpeg")
        self.ffmpeg_var = tk.StringVar()
        ctk.CTkEntry(binary, textvariable=self.ffmpeg_var, placeholder_text="Auto-detected from PATH").pack(
            side="left", fill="x", expand=True
        )
        ctk.CTkButton(binary, text="Browse", width=80, command=self._browse_ffmpeg).pack(side="left", padx=6)
        ctk.CTkButton(binary, text="Detect", width=70, command=self._detect_ffmpeg).pack(side="left", padx=(0, 6))
        ctk.CTkButton(binary, text="Save", width=70, command=self._save_ffmpeg).pack(side="left")
        self.binary_status = ctk.CTkLabel(self.content, text="", anchor="w")
        self.binary_status.pack(fill="x", padx=16, pady=(0, 4))

        providers = self._build_section("AI provider")
        row = ctk.CTkFrame(providers, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="Provider").pack(side="left")
        vendors = [item.get("vendor", "") for item in self._providers] or ["none"]
        self.provider_menu = ctk.CTkOptionMenu(
            row, values=vendors, command=self._on_provider_change, width=120
        )
        self.provider_menu.pack(side="left", padx=8)
        ctk.CTkLabel(row, text="Model").pack(side="left")
        self.model_menu = ctk.CTkOptionMenu(row, values=["none"], width=180)
        self.model_menu.pack(side="left", padx=8)
        key_row = ctk.CTkFrame(providers, fg_color="transparent")
        key_row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(key_row, text="API key").pack(side="left")
        self.key_var = tk.StringVar()
        self.key_entry = ctk.CTkEntry(key_row, textvariable=self.key_var, show="*")
        self.key_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.reveal_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            key_row, text="Show", variable=self.reveal_var, command=self._toggle_key, width=70
        ).pack(side="left")
        ctk.CTkButton(key_row, text="Save", width=70, command=self._save_llm).pack(side="left", padx=6)
        transcribe_row = ctk.CTkFrame(providers, fg_color="transparent")
        transcribe_row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(transcribe_row, text="Transcribe with").pack(side="left")
        self.transcribe_menu = ctk.CTkOptionMenu(
            transcribe_row, values=[SAME_PROVIDER_LABEL, *vendors], width=150
        )
        self.transcribe_menu.pack(side="left", padx=8)
        ctk.CTkLabel(transcribe_row, text="Model").pack(side="left", padx=(8, 0))
        self.transcribe_var = tk.StringVar()
        ctk.CTkEntry(
            transcribe_row,
            textvariable=self.transcribe_var,
            placeholder_text=DEFAULT_TRANSCRIBE_MODEL,
        ).pack(side="left", fill="x", expand=True, padx=8)
        self.llm_status = ctk.CTkLabel(providers, text="", anchor="w")
        self.llm_status.pack(fill="x", pady=(6, 0))

    def _apply_appearance(self, mode: str) -> None:
        """Apply and save the appearance mode."""
        self.app.update_preferences(appearance=mode)

    def _browse_ffmpeg(self) -> None:
        """Pick the ffmpeg executable."""
        chosen = filedialog.askopenfilename(title="Select ffmpeg binary")
        if chosen:
            self.ffmpeg_var.set(chosen)

    def _browse_output(self) -> None:
        """Pick the default output folder."""
        chosen = filedialog.askdirectory(title="Select output folder")
        if chosen:
            self.output_var.set(chosen)

    def _check_binary(self, quiet: bool = False) -> None:
        """Show whether the configured ffmpeg binary can be executed."""
        available, version = ffmpeg_utils.binary_status()
        if available:
            self.binary_status.configure(text=version.split(" Copyright")[0])
        elif not quiet:
            self.binary_status.configure(text=f"ffmpeg not usable: {version[:120]}")

    def _detect_ffmpeg(self) -> None:
        """Fill the ffmpeg field with the binary found on PATH."""
        found = ffmpeg_utils.find_tool("ffmpeg")
        self.ffmpeg_var.set(found)
        self.binary_status.configure(text=found or "ffmpeg not found on PATH")

    def _load_api_key(self, provider: str) -> None:
        """Show the stored API key for the selected provider."""
        for item in self._providers:
            if item.get("vendor") == provider:
                self.key_var.set(str(item.get("apiKey") or ""))
                return
        self.key_var.set("")

    def _on_provider_change(self, provider: str) -> None:
        """Refresh models and key display when the provider changes."""
        self._refresh_models(provider)
        self._load_api_key(provider)

    def _refresh_models(self, provider: str) -> None:
        """Fill the model menu for a provider."""
        try:
            models = agent.get_provider_models(provider) or ["none"]
        except (ValueError, OSError):
            models = ["none"]
        self.model_menu.configure(values=models)
        self.model_menu.set(models[0])

    def _save_ffmpeg(self) -> None:
        """Persist the ffmpeg path and re-check the binary."""
        self.app.update_preferences(ffmpeg_path=self.ffmpeg_var.get().strip())
        self._check_binary()

    def _save_llm(self) -> None:
        """Persist the provider, model, API key, and transcription choice."""
        provider = self.provider_menu.get()
        model = self.model_menu.get()
        try:
            agent.save_api_key(provider, self.key_var.get().strip())
        except (OSError, ValueError) as exc:
            self.llm_status.configure(text=f"Could not save key: {exc}")
            return
        transcribe = self.transcribe_menu.get()
        self.app.update_preferences(
            provider=provider,
            model=model,
            transcribe_provider="" if transcribe == SAME_PROVIDER_LABEL else transcribe,
            transcribe_model=self.transcribe_var.get().strip(),
        )
        self._providers = _safe_providers()
        self.llm_status.configure(text=f"Saved settings for {provider} ({model}).")

    def _save_output(self) -> None:
        """Persist the default output folder."""
        folder = self.output_var.get().strip()
        if not folder:
            self.output_var.set(str(context.get_output_dir()))
            return
        self.app.update_preferences(output_dir=folder)

    def _toggle_key(self) -> None:
        """Show or hide the API key text."""
        self.key_entry.configure(show="" if self.reveal_var.get() else "*")


def _safe_providers() -> list[dict]:
    """Load providers, returning an empty list when the config is broken."""
    try:
        return agent.list_providers()
    except (OSError, ValueError):
        return []
