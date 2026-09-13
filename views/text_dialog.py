"""Read-only text window for scripts and transcripts."""

from __future__ import annotations

from typing import Any

import customtkinter as ctk


class TextDialog(ctk.CTkToplevel):
    """Show a block of text in a scrollable read-only window."""

    def __init__(self, master: Any, title: str, text: str) -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("720x520")
        self.minsize(420, 300)
        self.transient(master)
        box = ctk.CTkTextbox(self, wrap="word")
        box.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        box.insert("1.0", text)
        box.configure(state="disabled")
        ctk.CTkButton(self, text="Close", width=90, command=self.destroy).pack(pady=(0, 10))
        self.after(60, self.lift)
