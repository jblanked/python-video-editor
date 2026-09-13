# Video Editor

A desktop video editor for local files, built with customtkinter and ffmpeg.
A built-in AI assistant can run any editing operation for you.

## Requirements

- Python 3.10 or newer
- ffmpeg and ffprobe on PATH (macOS: `brew install ffmpeg`)

## Setup
### 1. Clone the repository:
```
git clone https://github.com/jblanked/python-video-editor.git
cd python-video-editor
```

### 2. Set up the virtual environment:
Windows
```
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

Mac/Linux
```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the application:
```
python main.py
```

## Features

- **Clips** - import media with thumbnails, preview it, and run per-clip
  operations (see the list below). Add to Timeline appends the selected clip;
  Add All To Timeline appends the whole pool in order.
- **Timeline** - a horizontal strip of clips with an embedded preview player.
  Drag clips to reorder, drag their edges to trim, double-click to preview from
  a clip, zoom the strip, drag the playhead to start anywhere, and play with
  the button or the space bar. Right-click a clip for every operation that
  applies to it. Projects can be saved and the sequence rendered into one file.
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
transcribe, remove silence). Times accept seconds, `MM:SS` or `HH:MM:SS`.

## Settings

Set the theme, output folder and ffmpeg path, and the single AI setup used by
both the assistant and transcription: provider, model and API key. Pick a
different provider under "Transcribe with" if your chat provider does not
offer audio. Keys are stored in `settings/llm.json`.