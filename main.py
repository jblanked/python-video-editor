"""Video Editor entry point."""

from __future__ import annotations

from views.app import VideoEditorApp


def main() -> None:
    """Launch the video editor window."""
    app = VideoEditorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
