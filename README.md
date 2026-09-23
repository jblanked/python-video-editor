# Python Video Editor

A desktop video editor for local files, built with customtkinter and ffmpeg.
A built-in AI assistant can run any editing operation for you.

## Requirements

- Python 3.10 or newer
- ffmpeg and ffprobe on PATH (macOS: `brew install ffmpeg`)

## Setup
### 1. Clone the repository:
```sh
git clone https://github.com/jblanked/python-video-editor.git
cd python-video-editor
```

### 2. Set up the virtual environment:
Windows
```sh
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

Mac/Linux
```sh
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the application:
```sh
python main.py
```

## Features

- **Timeline** - the single editor view: a media pool on the left and a
  horizontal strip of clips with an embedded preview player. Add media to the
  pool with thumbnails, preview a clip, and add it to the timeline, or add
  the whole pool in order, or drag a pool card straight onto the strip. Drag
  a clip sideways to slide it along its lane without changing its length, drag it
  up or down to move it onto another layer, and drag its edges to trim. Detach
  Audio moves a clip's sound onto its own lane below the clip so it can be moved
  and trimmed on its own. Undo and redo (Cmd/Ctrl+Z, Shift+Cmd/Ctrl+Z) cover
  every edit, and Copy/Paste (Cmd/Ctrl+C, Cmd/Ctrl+V) duplicates a clip onto its
  lane. Double-click to preview from a clip, zoom the strip, drag the playhead
  to start anywhere, and play with the button or the space bar. Right-click a
  clip for every operation that applies to it. Projects can be saved and the
  sequence rendered into one file.
- **AI Assistant** - describe an edit in chat and the assistant runs the
  editor's operations, for example "trim the first clip to five seconds and
  add a fade" or "remove all white space from the timeline". Ctrl+Enter sends,
  Stop cancels.
- **Transcription** - turn a clip or the whole timeline into text and detect
  silence. Transcripts are saved to the output folder as
  `timeline_transcript.txt` or `<clipname>_transcript.txt` and open in a
  script window. The assistant can cut every silent gap out of the timeline
  on request.

## Operations

Trim, cut, split and join clips; resize, crop, rotate, flip, change speed,
reverse, fade and blur; adjust volume, mute, extract and add audio; add text,
subtitles and watermarks; convert formats, make GIFs, thumbnails and frame
dumps; and edit the timeline (add, trim, move, remove, clear, render,
transcribe, remove silence, detach audio). Times accept seconds, `MM:SS` or `HH:MM:SS`.

## Settings

Set the theme, output folder and ffmpeg path, and the single AI setup used by
both the assistant and transcription: provider, model and API key. Pick a
different provider under "Transcribe with" if your chat provider does not
offer audio. Keys are stored in `settings/llm.json`.

## Build as an app

```sh
cd python-video-editor
source venv/bin/activate # or on Windows use .\venv\Scripts\activate
pyinstaller PythonVideoEditor.spec
```

The bundle lands in `dist/Python Video Editor.app` (macOS). Frozen builds keep their
settings and output in `~/Library/Application Support/VideoEditor`.