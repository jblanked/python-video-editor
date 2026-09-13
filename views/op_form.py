"""Reusable operation form and dialog for running clip operations."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Any, Callable

import customtkinter as ctk

from tools.dispatch import default_output_path, execute_tool
from tools.registry import Op, Param

MEDIA_TYPES = [
    ("Media files", "*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.m4a *.png *.jpg *.srt *.ass *.vtt"),
    ("All files", "*.*"),
]


class OperationForm(ctk.CTkScrollableFrame):
    """Parameter form for one operation, built from its registry definition."""

    def __init__(self, master: Any, source_path: str = "") -> None:
        """Create an empty form that renders the parameters of an operation."""
        super().__init__(master, fg_color="transparent")
        self.source_path = source_path
        self.operation: Op | None = None
        self.widgets: dict[str, Any] = {}

    def build(self, op: Op, source_path: str | None = None) -> None:
        """Render the parameter rows for an operation."""
        if source_path is not None:
            self.source_path = source_path
        for child in self.winfo_children():
            child.destroy()
        self.widgets = {}
        self.operation = op
        for param in op.params:
            if param.name == "path" and param.type == "path":
                continue
            self._build_row(param)
        self._update_output_default()

    def collect(self) -> dict:
        """Return the arguments entered in the form, including the source clip."""
        args: dict[str, Any] = {}
        for name, widget in self.widgets.items():
            if isinstance(widget, ctk.CTkTextbox):
                text = widget.get("1.0", "end").strip()
                if text:
                    args[name] = text
            elif isinstance(widget, tk.BooleanVar):
                args[name] = widget.get()
            elif isinstance(widget, tk.StringVar):
                value = widget.get().strip()
                if value:
                    args[name] = value
        if self.operation is not None and self.source_path:
            if any(param.name == "path" for param in self.operation.params):
                args.setdefault("path", self.source_path)
        return args

    def update_source(self, source_path: str) -> None:
        """Point the form at a new source clip and refresh default outputs."""
        self.source_path = source_path
        self._update_output_default()

    def _browse(self, param: Param, variable: tk.StringVar) -> None:
        """Open a file, folder, or save dialog for a path parameter."""
        if param.type == "output_dir":
            chosen = filedialog.askdirectory(title=param.display_label)
        elif param.type == "path":
            chosen = filedialog.askopenfilename(title=param.display_label, filetypes=MEDIA_TYPES)
        else:
            chosen = filedialog.asksaveasfilename(title=param.display_label)
        if chosen:
            variable.set(chosen)

    def _build_row(self, param: Param) -> None:
        """Create one labelled input row for an operation parameter."""
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=2)
        ctk.CTkLabel(row, text=param.display_label, width=110, anchor="w").pack(side="left")
        if param.type == "boolean":
            variable = tk.BooleanVar(value=bool(param.default))
            ctk.CTkCheckBox(row, text="", variable=variable, width=26).pack(side="left")
            self.widgets[param.name] = variable
            return
        if param.type == "paths":
            box = ctk.CTkTextbox(row, height=64, wrap="none")
            box.pack(side="left", fill="x", expand=True)
            if self.source_path:
                box.insert("1.0", self.source_path)
            self.widgets[param.name] = box
            return
        if param.type in {"path", "output", "output_dir"}:
            variable = tk.StringVar()
            ctk.CTkEntry(row, textvariable=variable).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(
                row,
                text="...",
                width=30,
                command=lambda item=param, var=variable: self._browse(item, var),
            ).pack(side="left", padx=(4, 0))
            self.widgets[param.name] = variable
            return
        default = "" if param.default is None else str(param.default)
        variable = tk.StringVar(value=default)
        if param.type == "choice":
            ctk.CTkOptionMenu(row, values=list(param.choices), variable=variable).pack(side="left")
        else:
            ctk.CTkEntry(row, textvariable=variable).pack(side="left", fill="x", expand=True)
            if param.type == "time":
                ctk.CTkLabel(row, text="seconds, MM:SS or HH:MM:SS", text_color=("gray40", "gray60")).pack(
                    side="left", padx=(6, 0)
                )
        self.widgets[param.name] = variable

    def _update_output_default(self) -> None:
        """Fill the output field with its default path while it is empty."""
        op = self.operation
        if op is None or op.output_kind == "none":
            return
        target = "output" if op.output_kind == "file" else "output_dir"
        variable = self.widgets.get(target)
        if not isinstance(variable, tk.StringVar) or variable.get().strip():
            return
        args = self.collect()
        if any(param.name == "path" for param in op.params) and not args.get("path"):
            return
        try:
            variable.set(str(default_output_path(op, args)))
        except (ValueError, OSError):
            return


class OperationDialog(ctk.CTkToplevel):
    """Dialog that configures and runs one operation on a clip."""

    def __init__(
        self,
        master: Any,
        op: Op,
        source_path: str,
        on_done: Callable[[dict], None] | None = None,
    ) -> None:
        """Create the dialog for an operation and its source clip."""
        super().__init__(master)
        self.operation = op
        self.source_path = source_path
        self._on_done = on_done
        self._busy = False
        self.title(f"{op.label} - {Path(source_path).name}")
        self.geometry("620x470")
        self.minsize(480, 360)
        self.transient(master.winfo_toplevel())
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        ctk.CTkLabel(
            self, text=op.description, anchor="w", wraplength=580, justify="left"
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        self.form = OperationForm(self, source_path=source_path)
        self.form.grid(row=2, column=0, sticky="nsew", padx=10, pady=4)
        self.form.build(op, source_path)
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=14, pady=6)
        self.run_button = ctk.CTkButton(actions, text="Run", width=90, command=self._run)
        self.run_button.pack(side="left")
        ctk.CTkButton(actions, text="Close", width=90, command=self.destroy).pack(
            side="left", padx=8
        )
        self.progress = ctk.CTkProgressBar(actions, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        self.status = ctk.CTkLabel(self, text="", anchor="w", wraplength=580)
        self.status.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 12))
        self.after(60, self.lift)

    def run_now(self) -> None:
        """Run the operation without waiting for a button press."""
        self._run()

    def _finish(self, result: dict) -> None:
        """Report the operation result and notify the caller."""
        self._busy = False
        self.progress.stop()
        self.run_button.configure(state="normal")
        prefix = "OK" if result.get("success") else "FAILED"
        self.status.configure(text=f"{prefix}: {result.get('message', '')}")
        if self._on_done is not None:
            self._on_done(result)

    def _run(self) -> None:
        """Run the operation in a worker thread."""
        if self._busy:
            return
        self._busy = True
        self.run_button.configure(state="disabled")
        self.progress.start()
        self.status.configure(text="Running...")
        args = self.form.collect()
        threading.Thread(target=self._worker, args=(args,), daemon=True).start()

    def _worker(self, args: dict) -> None:
        """Execute the tool off the UI thread."""
        result = execute_tool(self.operation.name, args)
        self.after(0, lambda: self._finish(result))
