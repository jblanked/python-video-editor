"""Operation registry: one definition per operation feeding both the agent
tool schemas and the GUI forms, so the editor and the AI stay in sync."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from tools import (
    audio_ops,
    clip_ops,
    export_ops,
    info_ops,
    overlay_ops,
    timeline_ops,
    transcribe_ops,
    transform_ops,
)
from tools.tool import Parameters, Property, Tool

POSITION_CHOICES = (
    "top-left",
    "top-center",
    "top-right",
    "center",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)

AGENT_TYPES = {"integer": "integer", "number": "number", "boolean": "boolean"}


@dataclass(frozen=True)
class Param:
    """Describes one operation parameter for forms and tool schemas."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    label: str = ""

    @property
    def agent_type(self) -> str:
        """Return the JSON schema type the agent should use for this parameter."""
        return AGENT_TYPES.get(self.type, "string")

    def agent_description(self) -> str:
        """Return the description shown to the agent, including hints and defaults."""
        parts = [self.description or self.name]
        if self.choices:
            parts.append(f"Options: {', '.join(self.choices)}.")
        if self.default not in (None, "") and not self.choices:
            parts.append(f"Default: {self.default}.")
        return " ".join(part for part in parts if part)

    @property
    def display_label(self) -> str:
        """Return the GUI label for this parameter."""
        return self.label or self.name.replace("_", " ").title()


@dataclass(frozen=True)
class Op:
    """Describes one editor operation and the function that runs it."""

    name: str
    label: str
    group: str
    description: str
    function: Callable[..., dict]
    params: tuple[Param, ...] = ()
    scope: str = "clip"
    output_kind: str = "file"
    output_tag: str = ""
    output_ext: Any = None


SOURCE = Param(
    name="path",
    type="path",
    description="Path to the source video file.",
    required=True,
    label="Source clip",
)
OUTPUT = Param(
    name="output",
    type="output",
    description="Output file path; defaults to the output folder with a generated name.",
)
OUTPUT_DIR = Param(
    name="output_dir",
    type="output_dir",
    description="Folder for the generated files; defaults to the output folder.",
)
START = Param(
    name="start",
    type="time",
    description="Start time as seconds, MM:SS, or HH:MM:SS.",
    label="Start",
)
END = Param(name="end", type="time", description="End time as seconds, MM:SS, or HH:MM:SS.", label="End")
POSITION = Param(
    name="position",
    type="choice",
    description="Position of the overlay.",
    choices=POSITION_CHOICES,
    default="bottom-center",
    label="Position",
)

OPS: tuple[Op, ...] = (
    Op(
        name="get_video_info",
        label="Inspect clip",
        group="Info",
        description=(
            "Inspect a video file and return its duration, resolution, frame rate, "
            "codecs, audio availability, and size."
        ),
        function=info_ops.get_video_info,
        params=(SOURCE,),
        scope="info",
        output_kind="none",
    ),
    Op(
        name="trim_video",
        label="Trim",
        group="Clip",
        description=(
            "Keep only the segment between start and end (or start plus duration), "
            "saving the result as a new clip."
        ),
        function=clip_ops.trim_video,
        params=(
            SOURCE,
            Param(name="start", type="time", description="Start time of the kept segment.", label="Start"),
            END,
            Param(
                name="duration",
                type="time",
                description="Length to keep from the start time; alternative to end.",
                label="Duration",
            ),
            OUTPUT,
        ),
        output_tag="trim",
    ),
    Op(
        name="cut_video",
        label="Cut segment",
        group="Clip",
        description="Remove the segment between start and end, keeping the rest of the clip.",
        function=clip_ops.cut_video,
        params=(
            SOURCE,
            Param(
                name="start",
                type="time",
                description="Start of the section to remove.",
                required=True,
                label="Cut from",
            ),
            Param(
                name="end",
                type="time",
                description="End of the section to remove.",
                required=True,
                label="Cut to",
            ),
            OUTPUT,
        ),
        output_tag="cut",
    ),
    Op(
        name="split_video",
        label="Split into parts",
        group="Clip",
        description="Split a clip into equal parts by count, or fixed-length parts by seconds.",
        function=clip_ops.split_video,
        params=(
            SOURCE,
            Param(name="count", type="integer", description="Number of equal parts.", label="Parts"),
            Param(
                name="seconds",
                type="time",
                description="Length of each part in seconds.",
                label="Seconds per part",
            ),
            Param(name="prefix", type="string", description="File name prefix for the parts."),
            OUTPUT_DIR,
        ),
        output_kind="dir",
        output_tag="parts",
    ),
    Op(
        name="join_videos",
        label="Join clips",
        group="Clip",
        description=(
            "Concatenate two or more clips in the given order into one file. "
            "Copy mode is faster but needs matching codecs; auto falls back to re-encoding."
        ),
        function=clip_ops.join_videos,
        params=(
            Param(
                name="paths",
                type="paths",
                description="Clip paths in the order they should be joined.",
                required=True,
                label="Clips to join",
            ),
            Param(
                name="mode",
                type="choice",
                description="Stream copy, full re-encode, or try copy first.",
                choices=("auto", "copy", "reencode"),
                default="auto",
            ),
            OUTPUT,
        ),
        output_tag="join",
    ),
    Op(
        name="resize_video",
        label="Resize",
        group="Transform",
        description=(
            "Resize a clip. Give a width, a height, or both; fit pads to keep the "
            "aspect ratio while stretch forces exact dimensions."
        ),
        function=transform_ops.resize_video,
        params=(
            SOURCE,
            Param(name="width", type="integer", description="Target width in pixels.", label="Width"),
            Param(name="height", type="integer", description="Target height in pixels.", label="Height"),
            Param(
                name="mode",
                type="choice",
                description="Fit keeps the aspect ratio, stretch forces the exact size.",
                choices=("fit", "stretch"),
                default="fit",
            ),
            OUTPUT,
        ),
        output_tag="resize",
    ),
    Op(
        name="crop_video",
        label="Crop",
        group="Transform",
        description="Crop the frame to a width and height; x and y default to centring the crop.",
        function=transform_ops.crop_video,
        params=(
            SOURCE,
            Param(
                name="width",
                type="integer",
                description="Crop width in pixels.",
                required=True,
                label="Width",
            ),
            Param(
                name="height",
                type="integer",
                description="Crop height in pixels.",
                required=True,
                label="Height",
            ),
            Param(name="x", type="integer", description="Left edge of the crop area.", label="X"),
            Param(name="y", type="integer", description="Top edge of the crop area.", label="Y"),
            OUTPUT,
        ),
        output_tag="crop",
    ),
    Op(
        name="rotate_video",
        label="Rotate",
        group="Transform",
        description="Rotate a clip clockwise by 90, 180, 270, or any other angle.",
        function=transform_ops.rotate_video,
        params=(
            SOURCE,
            Param(
                name="angle",
                type="number",
                description="Clockwise rotation angle in degrees.",
                default=90,
                choices=("90", "180", "270"),
                label="Angle",
            ),
            OUTPUT,
        ),
        output_tag="rotate",
    ),
    Op(
        name="flip_video",
        label="Flip",
        group="Transform",
        description="Mirror a clip horizontally, vertically, or both.",
        function=transform_ops.flip_video,
        params=(
            SOURCE,
            Param(
                name="direction",
                type="choice",
                description="Mirror axis.",
                choices=("horizontal", "vertical", "both"),
                default="horizontal",
            ),
            OUTPUT,
        ),
        output_tag="flip",
    ),
    Op(
        name="change_speed",
        label="Speed",
        group="Transform",
        description="Speed up (factor above 1) or slow down (factor below 1) a clip.",
        function=transform_ops.change_speed,
        params=(
            SOURCE,
            Param(name="factor", type="number", description="Speed multiplier.", default=2.0),
            OUTPUT,
        ),
        output_tag="speed",
    ),
    Op(
        name="reverse_video",
        label="Reverse",
        group="Transform",
        description="Play a clip backwards, reversing both video and audio.",
        function=transform_ops.reverse_video,
        params=(SOURCE, OUTPUT),
        output_tag="reverse",
    ),
    Op(
        name="fade_video",
        label="Fade in/out",
        group="Transform",
        description="Add a fade in at the start and a fade out at the end of a clip.",
        function=transform_ops.fade_video,
        params=(
            SOURCE,
            Param(name="fade_in", type="number", description="Fade in duration.", default=1.0, label="Fade in"),
            Param(
                name="fade_out",
                type="number",
                description="Fade out duration.",
                default=1.0,
                label="Fade out",
            ),
            OUTPUT,
        ),
        output_tag="fade",
    ),
    Op(
        name="blur_video",
        label="Blur",
        group="Transform",
        description="Apply a Gaussian blur to the whole frame; strength is the sigma value.",
        function=transform_ops.blur_video,
        params=(
            SOURCE,
            Param(name="strength", type="number", description="Blur strength in pixels.", default=10),
            OUTPUT,
        ),
        output_tag="blur",
    ),
    Op(
        name="adjust_volume",
        label="Volume",
        group="Audio",
        description="Change the audio level; 1.0 is unchanged, 2.0 doubles, 0.5 halves.",
        function=audio_ops.adjust_volume,
        params=(
            SOURCE,
            Param(name="volume", type="number", description="Volume multiplier.", default=1.0),
            OUTPUT,
        ),
        output_tag="volume",
    ),
    Op(
        name="mute_video",
        label="Mute",
        group="Audio",
        description="Remove the audio track from a clip.",
        function=audio_ops.mute_video,
        params=(SOURCE, OUTPUT),
        output_tag="mute",
    ),
    Op(
        name="extract_audio",
        label="Extract audio",
        group="Audio",
        description="Extract the audio track as mp3, wav, m4a, flac, or ogg.",
        function=audio_ops.extract_audio,
        params=(
            SOURCE,
            Param(
                name="format",
                type="choice",
                description="Audio file format.",
                choices=("mp3", "wav", "m4a", "flac", "ogg"),
                default="mp3",
            ),
            OUTPUT,
        ),
        output_tag="audio",
        output_ext=lambda args: audio_ops.AUDIO_CODECS.get(str(args.get("format", "mp3")), ("", ".mp3"))[1],
    ),
    Op(
        name="add_audio",
        label="Add audio",
        group="Audio",
        description="Replace the clip audio track with an audio file, or mix it with the original.",
        function=audio_ops.add_audio,
        params=(
            SOURCE,
            Param(
                name="audio",
                type="path",
                description="Audio file to add.",
                required=True,
                label="Audio file",
            ),
            Param(
                name="mix",
                type="boolean",
                description="Mix with the existing audio instead of replacing it.",
                default=False,
            ),
            Param(name="volume", type="number", description="Volume of the new audio.", default=1.0),
            OUTPUT,
        ),
        output_tag="mixed",
    ),
    Op(
        name="transcribe_clip",
        label="Transcribe clip",
        group="Audio",
        description=(
            "Transcribe a clip's speech to text with timestamps, and list silence ranges "
            "that can be trimmed away."
        ),
        function=transcribe_ops.transcribe_clip,
        params=(
            SOURCE,
            Param(
                name="language",
                type="string",
                description="Optional language such as en or English; empty auto-detects.",
            ),
        ),
        output_kind="none",
    ),
    Op(
        name="add_text",
        label="Add text",
        group="Overlay",
        description=(
            "Draw a text overlay on the video, optionally limited to a time range "
            "and shown in a semi-transparent box."
        ),
        function=overlay_ops.add_text,
        params=(
            SOURCE,
            Param(name="text", type="text", description="Text to draw.", required=True),
            Param(
                name="position",
                type="choice",
                description="Position of the text.",
                choices=POSITION_CHOICES,
                default="bottom-center",
                label="Position",
            ),
            Param(
                name="font_size",
                type="integer",
                description="Font size in pixels.",
                default=48,
                label="Font size",
            ),
            Param(name="color", type="string", description="Text colour name or hex code.", default="white"),
            Param(name="start", type="time", description="Only show from this time.", label="Show from"),
            Param(name="end", type="time", description="Only show until this time.", label="Show until"),
            Param(
                name="box",
                type="boolean",
                description="Draw a semi-transparent box behind the text.",
                default=False,
            ),
            OUTPUT,
        ),
        output_tag="text",
    ),
    Op(
        name="add_subtitles",
        label="Add subtitles",
        group="Overlay",
        description="Burn a subtitle file (srt, ass, or vtt) into the video.",
        function=overlay_ops.add_subtitles,
        params=(
            SOURCE,
            Param(
                name="subtitle_file",
                type="path",
                description="Subtitle file to burn in.",
                required=True,
                label="Subtitles",
            ),
            OUTPUT,
        ),
        output_tag="subtitled",
    ),
    Op(
        name="add_watermark",
        label="Watermark",
        group="Overlay",
        description="Overlay an image watermark with adjustable position, opacity, and width.",
        function=overlay_ops.add_watermark,
        params=(
            SOURCE,
            Param(
                name="image",
                type="path",
                description="Watermark image file.",
                required=True,
                label="Image",
            ),
            Param(
                name="position",
                type="choice",
                description="Position of the watermark.",
                choices=POSITION_CHOICES,
                default="bottom-right",
                label="Position",
            ),
            Param(
                name="opacity",
                type="number",
                description="Watermark opacity from 0 to 1.",
                default=0.7,
                label="Opacity",
            ),
            Param(
                name="width",
                type="integer",
                description="Watermark width in pixels; keeps the original size when empty.",
                label="Width",
            ),
            OUTPUT,
        ),
        output_tag="watermark",
    ),
    Op(
        name="convert_video",
        label="Convert format",
        group="Export",
        description="Convert a clip to mp4, mov, mkv, webm, hevc, or gif at a quality preset.",
        function=export_ops.convert_video,
        params=(
            SOURCE,
            Param(
                name="format",
                type="choice",
                description="Target format.",
                choices=("mp4", "mov", "mkv", "webm", "hevc", "gif"),
                default="mp4",
            ),
            Param(
                name="quality",
                type="choice",
                description="Quality preset.",
                choices=("low", "medium", "high", "ultra"),
                default="high",
            ),
            OUTPUT,
        ),
        output_tag="converted",
        output_ext=lambda args: export_ops.FORMAT_EXTENSIONS.get(str(args.get("format", "mp4")), ".mp4"),
    ),
    Op(
        name="create_gif",
        label="Create GIF",
        group="Export",
        description="Turn all or part of a clip into an optimised animated GIF.",
        function=export_ops.create_gif,
        params=(
            SOURCE,
            Param(name="start", type="time", description="GIF start time.", label="Start"),
            Param(name="duration", type="time", description="GIF length in seconds.", label="Duration"),
            Param(name="fps", type="integer", description="Frames per second.", default=12),
            Param(name="width", type="integer", description="GIF width in pixels.", default=480),
            OUTPUT,
        ),
        output_tag="gif",
        output_ext="gif",
    ),
    Op(
        name="create_thumbnail",
        label="Thumbnail",
        group="Export",
        description="Save a single frame from a clip as a jpg or png thumbnail.",
        function=export_ops.create_thumbnail,
        params=(
            SOURCE,
            Param(
                name="time",
                type="time",
                description="Time of the frame to capture.",
                default="00:00:01",
                label="Time",
            ),
            Param(name="width", type="integer", description="Thumbnail width in pixels.", default=640),
            OUTPUT,
        ),
        output_tag="thumb",
        output_ext="jpg",
    ),
    Op(
        name="extract_frames",
        label="Extract frames",
        group="Export",
        description=(
            "Extract frames as png files by interval in seconds, total frame count, or fps."
        ),
        function=export_ops.extract_frames,
        params=(
            SOURCE,
            Param(
                name="mode",
                type="choice",
                description="How the value is interpreted.",
                choices=("interval", "count", "fps"),
                default="interval",
            ),
            Param(
                name="value",
                type="number",
                description="Seconds per frame, total frames, or frames per second.",
                default=1.0,
            ),
            Param(name="start", type="time", description="Range start.", label="Start"),
            Param(name="end", type="time", description="Range end.", label="End"),
            OUTPUT_DIR,
        ),
        output_kind="dir",
        output_tag="frames",
    ),
    Op(
        name="timeline_get_state",
        label="Get timeline state",
        group="Timeline",
        description="Return the media pool and timeline layout of the open project.",
        function=timeline_ops.timeline_get_state,
        scope="timeline",
        output_kind="none",
    ),
    Op(
        name="timeline_add_clip",
        label="Add to timeline",
        group="Timeline",
        description="Add a clip to the editor timeline, optionally with in and out points.",
        function=timeline_ops.timeline_add_clip,
        params=(
            SOURCE,
            Param(name="start", type="time", description="Timeline in point.", label="In"),
            Param(name="end", type="time", description="Timeline out point.", label="Out"),
            Param(name="position", type="integer", description="Timeline index to insert at."),
            Param(
                name="layer",
                type="integer",
                description="Layer to place the clip on; 0 is the base, higher layers sit on top.",
                default=0,
                label="Layer",
            ),
        ),
        scope="timeline",
        output_kind="none",
    ),
    Op(
        name="timeline_move_to_layer",
        label="Move clip to layer",
        group="Timeline",
        description=(
            "Move a timeline clip onto another layer. Higher layers are drawn over lower "
            "ones, so the topmost clip at any moment is the one seen."
        ),
        function=timeline_ops.timeline_move_to_layer,
        params=(
            Param(
                name="index",
                type="integer",
                description="Timeline clip index.",
                required=True,
                label="Index",
            ),
            Param(
                name="layer",
                type="integer",
                description="Target layer; 0 is the base, higher layers sit on top.",
                required=True,
                label="Layer",
            ),
        ),
        scope="timeline",
        output_kind="none",
    ),
    Op(
        name="timeline_detach_audio",
        label="Detach audio",
        group="Timeline",
        description=(
            "Move a timeline clip's audio onto its own lane underneath the clip, so the audio "
            "can be moved and trimmed on its own. The clip keeps playing without its audio."
        ),
        function=timeline_ops.timeline_detach_audio,
        params=(
            Param(
                name="index",
                type="integer",
                description="Timeline clip index.",
                required=True,
                label="Index",
            ),
        ),
        scope="timeline",
        output_kind="none",
    ),
    Op(
        name="timeline_trim_clip",
        label="Trim timeline clip",
        group="Timeline",
        description="Change the in and out points of a timeline clip at the given index.",
        function=timeline_ops.timeline_trim_clip,
        params=(
            Param(
                name="index",
                type="integer",
                description="Timeline clip index.",
                required=True,
                label="Index",
            ),
            Param(name="start", type="time", description="New in point.", label="In"),
            Param(name="end", type="time", description="New out point.", label="Out"),
        ),
        scope="timeline",
        output_kind="none",
    ),
    Op(
        name="timeline_move_clip",
        label="Move timeline clip",
        group="Timeline",
        description="Move a timeline clip from one index to another.",
        function=timeline_ops.timeline_move_clip,
        params=(
            Param(
                name="index",
                type="integer",
                description="Current timeline index.",
                required=True,
                label="Index",
            ),
            Param(
                name="new_index",
                type="integer",
                description="Target timeline index.",
                required=True,
                label="New index",
            ),
        ),
        scope="timeline",
        output_kind="none",
    ),
    Op(
        name="timeline_remove_clip",
        label="Remove timeline clip",
        group="Timeline",
        description="Remove the timeline clip at the given index.",
        function=timeline_ops.timeline_remove_clip,
        params=(
            Param(
                name="index",
                type="integer",
                description="Timeline clip index.",
                required=True,
                label="Index",
            ),
        ),
        scope="timeline",
        output_kind="none",
    ),
    Op(
        name="timeline_clear",
        label="Clear timeline",
        group="Timeline",
        description="Remove every clip from the editor timeline.",
        function=timeline_ops.timeline_clear,
        scope="timeline",
        output_kind="none",
    ),
    Op(
        name="timeline_remove_silence",
        label="Remove silence",
        group="Timeline",
        description=(
            "Cut every silent stretch out of the timeline in one call, rebuilding it from the "
            "speech parts only. Uses ffmpeg silencedetect on each clip."
        ),
        function=timeline_ops.timeline_remove_silence,
        params=(
            Param(
                name="margin",
                type="number",
                description="Seconds cut into each side of a silence gap.",
                default=0.05,
                label="Margin",
            ),
        ),
        scope="timeline",
        output_kind="none",
    ),
    Op(
        name="timeline_render",
        label="Render timeline",
        group="Timeline",
        description="Render the editor timeline into a single video file.",
        function=timeline_ops.timeline_render,
        params=(
            OUTPUT,
            Param(name="width", type="integer", description="Output width; defaults to the first clip."),
            Param(
                name="height",
                type="integer",
                description="Output height; defaults to the first clip.",
            ),
            Param(name="fps", type="number", description="Output frame rate; defaults to the first clip."),
        ),
        scope="timeline",
        output_tag="timeline",
        output_ext="mp4",
    ),
    Op(
        name="transcribe_timeline",
        label="Transcribe timeline",
        group="Timeline",
        description=(
            "Transcribe the open timeline into a script with per-clip text and silence "
            "ranges, so clips can be reordered by what was said and gaps trimmed."
        ),
        function=transcribe_ops.transcribe_timeline,
        params=(
            Param(
                name="language",
                type="string",
                description="Optional language such as en or English; empty auto-detects.",
            ),
        ),
        scope="timeline",
        output_kind="none",
    ),
)


def build_tools() -> list[Tool]:
    """Build agent tool schemas from the operation registry."""
    tools = []
    for op in OPS:
        properties = [
            Property(
                name=param.name,
                property_type=param.agent_type,
                description=param.agent_description(),
                required=param.required,
            )
            for param in op.params
        ]
        tools.append(
            Tool(
                name=op.name,
                description=op.description,
                parameters=Parameters(properties=properties),
            )
        )
    return tools


def find_op(name: str) -> Op | None:
    """Return the operation with the given tool name, or None."""
    for op in OPS:
        if op.name == name:
            return op
    return None


def get_groups(scope: str | None = None) -> list[str]:
    """Return the group names in registry order for the given scope."""
    groups: list[str] = []
    for op in OPS:
        if scope and op.scope != scope:
            continue
        if op.group not in groups:
            groups.append(op.group)
    return groups


def get_openai_tools() -> list[dict]:
    """Return all tool schemas in OpenAI function-calling format."""
    return [tool.json_openai for tool in build_tools()]


def get_ops(scope: str | None = None, group: str | None = None) -> list[Op]:
    """Return operations filtered by scope and group."""
    return [
        op
        for op in OPS
        if (scope is None or op.scope == scope) and (group is None or op.group == group)
    ]


def output_extension(op: Op, args: dict) -> str:
    """Resolve the output file extension for an operation."""
    if callable(op.output_ext):
        extension = str(op.output_ext(args))
    elif isinstance(op.output_ext, str) and op.output_ext:
        extension = op.output_ext
    else:
        source = first_source(args)
        extension = Path(source).suffix or ".mp4"
    return extension if extension.startswith(".") else f".{extension}"


def first_source(args: dict) -> str:
    """Return the first source path referenced by operation arguments."""
    single = args.get("path")
    if single:
        return str(single)
    multiple = args.get("paths")
    if isinstance(multiple, list) and multiple:
        return str(multiple[0])
    if isinstance(multiple, str) and multiple.strip():
        return multiple.replace(",", "\n").splitlines()[0].strip()
    return ""
