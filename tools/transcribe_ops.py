"""Speech-to-text and silence analysis for clips and the open timeline."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import requests

from tools import audio_ops, context
from tools import ffmpeg_utils as ff
from tools.results import ensure_file, error_result, success_result

TRANSCRIBE_MODEL = "whisper-1"
TRANSCRIBE_TIMEOUT = 600
SILENCE_NOISE_DB = "-35dB"
SILENCE_MIN_LENGTH = 0.4
SILENCE_PATTERN = re.compile(r"silence_(start|end):\s*([0-9.]+)")
LANGUAGE_PATTERN = re.compile(r"[a-z]{2}")
LANGUAGE_CODES = {
    "afrikaans": "af",
    "albanian": "sq",
    "amharic": "am",
    "arabic": "ar",
    "armenian": "hy",
    "assamese": "as",
    "azerbaijani": "az",
    "bashkir": "ba",
    "basque": "eu",
    "belarusian": "be",
    "bengali": "bn",
    "bosnian": "bs",
    "breton": "br",
    "bulgarian": "bg",
    "burmese": "my",
    "castilian": "es",
    "catalan": "ca",
    "chinese": "zh",
    "croatian": "hr",
    "czech": "cs",
    "danish": "da",
    "dutch": "nl",
    "english": "en",
    "estonian": "et",
    "farsi": "fa",
    "finnish": "fi",
    "flemish": "nl",
    "french": "fr",
    "galician": "gl",
    "georgian": "ka",
    "german": "de",
    "greek": "el",
    "gujarati": "gu",
    "haitian": "ht",
    "hausa": "ha",
    "hebrew": "he",
    "hindi": "hi",
    "hungarian": "hu",
    "icelandic": "is",
    "indonesian": "id",
    "italian": "it",
    "japanese": "ja",
    "javanese": "jv",
    "kannada": "kn",
    "kazakh": "kk",
    "khmer": "km",
    "korean": "ko",
    "lao": "lo",
    "latvian": "lv",
    "lingala": "ln",
    "lithuanian": "lt",
    "luxembourgish": "lb",
    "macedonian": "mk",
    "malagasy": "mg",
    "malay": "ms",
    "malayalam": "ml",
    "maltese": "mt",
    "maori": "mi",
    "marathi": "mr",
    "moldavian": "ro",
    "mongolian": "mn",
    "myanmar": "my",
    "nepali": "ne",
    "norwegian": "no",
    "occitan": "oc",
    "pashto": "ps",
    "persian": "fa",
    "polish": "pl",
    "portuguese": "pt",
    "punjabi": "pa",
    "romanian": "ro",
    "russian": "ru",
    "sanskrit": "sa",
    "serbian": "sr",
    "shona": "sn",
    "sinhala": "si",
    "slovak": "sk",
    "slovenian": "sl",
    "somali": "so",
    "spanish": "es",
    "sundanese": "su",
    "swahili": "sw",
    "swedish": "sv",
    "tagalog": "tl",
    "tajik": "tg",
    "tamil": "ta",
    "tatar": "tt",
    "telugu": "te",
    "thai": "th",
    "tibetan": "bo",
    "turkish": "tr",
    "turkmen": "tk",
    "uighur": "ug",
    "ukrainian": "uk",
    "urdu": "ur",
    "uzbek": "uz",
    "vietnamese": "vi",
    "welsh": "cy",
    "yoruba": "yo",
}


def transcribe_clip(path: str | Path, language: str | None = None) -> dict:
    """Transcribe a clip's speech and report the script, timings, and silence."""
    source = ensure_file(path)
    info = ff.probe(source)
    if not ff.has_audio(info):
        return error_result("This clip has no audio track to transcribe.")
    audio_file = _extract_audio(source)
    if audio_file is None:
        return error_result("Could not extract audio for transcription.")
    try:
        data = _request_transcription(audio_file, language)
    except (ValueError, OSError) as exc:
        return error_result(str(exc))
    finally:
        audio_file.unlink(missing_ok=True)
    duration = ff.media_duration(info)
    result = _build_result(data, duration, _silence_ranges(source, duration))
    return _attach_transcript(result, f"{source.stem}_transcript.txt")


def transcribe_timeline(language: str | None = None) -> dict:
    """Transcribe the open timeline, mapping the script onto every clip."""
    project = context.get_project()
    if project is None:
        return error_result("No project is open.")
    segments = project.timeline
    if not segments:
        return error_result("The timeline is empty.")
    audio_file = _timeline_audio(segments)
    if audio_file is None:
        return error_result("The timeline has no audio to transcribe.")
    duration = sum(
        max(0.0, float(item.get("end") or 0.0) - float(item.get("start") or 0.0))
        for item in segments
    )
    try:
        data = _request_transcription(audio_file, language)
        silence = _silence_ranges(audio_file, duration)
    except (ValueError, OSError) as exc:
        return error_result(str(exc))
    finally:
        audio_file.unlink(missing_ok=True)
    result = _build_result(data, duration, silence)
    if result["success"]:
        transcript = (result.get("details") or {}).get("segments") or []
        result["details"]["clips"] = _clip_texts(segments, transcript)
    return _attach_transcript(result, "timeline_transcript.txt")


def detect_silence(path: str | Path, duration: float = 0.0) -> list[dict]:
    """Return the silence ranges of a file for trimming decisions."""
    return _silence_ranges(path, duration)


def transcript_text(details: dict) -> str:
    """Format transcript details as readable text."""
    lines: list[str] = []
    text = str(details.get("text") or "").strip()
    if text:
        lines.extend(["Script", text, ""])
    clips = details.get("clips") or []
    segments = details.get("segments") or []
    if clips:
        lines.append("Per clip")
        for item in clips:
            span = _span(item.get("start"), item.get("end"))
            spoken = item.get("text") or "(no speech)"
            index = int(item.get("index") or 0) + 1
            lines.append(f"[{index}] {item.get('name') or 'clip'} ({span}): {spoken}")
        lines.append("")
    elif segments:
        lines.append("Segments")
        for item in segments:
            lines.append(f"{_span(item.get('start'), item.get('end'))}: {item.get('text')}")
        lines.append("")
    silence = details.get("silence") or []
    if silence:
        lines.append("Silence")
        for gap in silence:
            span = _span(gap.get("start"), gap.get("end"))
            lines.append(f"{span}  ({float(gap.get('duration') or 0):.1f}s)")
        lines.append("")
    return "\n".join(lines).strip() or "No speech detected."


def _attach_transcript(result: dict, name: str) -> dict:
    """Save the transcript into the output folder and point the result at it."""
    if not result.get("success"):
        return result
    target = _write_transcript(result.get("details") or {}, name)
    if target is not None:
        result["output"] = str(target)
        result["message"] = f"{result.get('message')}. Saved to {target.name}."
    return result


def _build_result(
    data: dict, duration: float, silence: list[dict], clips: list[dict] | None = None
) -> dict:
    """Build the transcript result dictionary from the API response."""
    text = str(data.get("text") or "").strip()
    segments = [
        {
            "start": round(float(item.get("start") or 0.0), 2),
            "end": round(float(item.get("end") or 0.0), 2),
            "text": str(item.get("text") or "").strip(),
        }
        for item in data.get("segments") or []
    ]
    speech = sum(max(0.0, item["end"] - item["start"]) for item in segments)
    silence_seconds = sum(float(item.get("duration") or 0.0) for item in silence)
    message = (
        f"Transcribed {ff.format_time(duration)}: {len(segments)} speech segments, "
        f"{speech:.1f}s speech"
    )
    if silence:
        message += f", {len(silence)} silence gaps ({silence_seconds:.1f}s)"
    details: dict[str, Any] = {
        "text": text,
        "language": str(data.get("language") or ""),
        "duration": round(duration, 2),
        "duration_text": ff.format_time(duration),
        "segments": segments,
        "silence": silence,
        "speech_seconds": round(speech, 2),
        "silence_seconds": round(silence_seconds, 2),
    }
    if clips is not None:
        details["clips"] = clips
    return success_result(None, message, **details)


def _clip_texts(segments: list[dict], transcript: list[dict]) -> list[dict]:
    """Map transcript segments onto timeline clips by time range."""
    clips: list[dict] = []
    position = 0.0
    for index, segment in enumerate(segments):
        length = max(
            0.0, float(segment.get("end") or 0.0) - float(segment.get("start") or 0.0)
        )
        start = position
        end = position + length
        spoken = [
            item["text"]
            for item in transcript
            if float(item.get("end") or 0.0) > start + 1e-6
            and float(item.get("start") or 0.0) < end - 1e-6
        ]
        clips.append(
            {
                "index": index,
                "name": segment.get("name") or Path(str(segment.get("path"))).name,
                "start": round(start, 2),
                "end": round(end, 2),
                "text": " ".join(part for part in spoken if part).strip(),
            }
        )
        position = end
    return clips


def _extract_audio(source: str | Path) -> Path | None:
    """Extract mono 16 kHz mp3 audio for transcription."""
    descriptor, name = tempfile.mkstemp(prefix="video_editor_audio_", suffix=".mp3")
    os.close(descriptor)
    target = Path(name)
    ok, _ = ff.run_ffmpeg(
        [
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(target),
        ]
    )
    return target if ok and target.exists() else None


def _api_error(status: int, body: str, vendor: str) -> str:
    """Turn an API error response into a readable message."""
    text = str(body or "")
    if "<html" in text.lower() or "<!doctype" in text.lower():
        return (
            f"Transcription endpoint not found on {vendor} (HTTP {status}). "
            "Set 'Transcribe with' in the Settings view to a provider that supports audio."
        )
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return f"Transcription error ({status}): {plain[:200]}"


def _gap(start: float, end: float) -> dict:
    """Build one silence range record."""
    return {
        "start": round(start, 2),
        "end": round(end, 2),
        "duration": round(max(0.0, end - start), 2),
    }


def _normalize_language(value: str | None) -> str | None:
    """Turn a language name, code, or locale into an ISO-639-1 code."""
    if value is None:
        return None
    text = re.sub(r"\(.*?\)", " ", str(value).lower()).replace("_", "-").replace(",", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    head = text.split("-")[0].strip()
    if LANGUAGE_PATTERN.fullmatch(head):
        return head
    words = text.split()
    for candidate in (text, words[-1], words[0]):
        if candidate in LANGUAGE_CODES:
            return LANGUAGE_CODES[candidate]
    raise ValueError(
        f"Unknown language {value!r}. Use an ISO-639-1 code such as 'en' or a name like 'English'."
    )


def _request_transcription(audio_file: Path, language: str | None = None) -> dict:
    """Send audio to the transcription endpoint of the configured provider."""
    provider = _transcription_provider()
    vendor = str(provider.get("vendor") or "provider")
    api_key = provider.get("apiKey")
    if not api_key:
        raise ValueError(f"No API key set for {vendor}. Add one in the Settings view.")
    endpoint = str(provider.get("endpoint") or "")
    if "/chat/completions" not in endpoint:
        raise ValueError(
            f"Transcription needs an OpenAI-compatible provider, but {vendor} uses "
            f"{endpoint or 'no endpoint'}. Pick one under 'Transcribe with' in the Settings view."
        )
    form: dict[str, Any] = {"model": _transcribe_model(), "response_format": "verbose_json"}
    language_code = _normalize_language(language)
    if language_code:
        form["language"] = language_code
    with open(audio_file, "rb") as handle:
        response = requests.post(
            _transcription_endpoint(endpoint),
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (audio_file.name, handle, "audio/mpeg")},
            data=form,
            timeout=TRANSCRIBE_TIMEOUT,
        )
    if response.status_code >= 400:
        raise ValueError(_api_error(response.status_code, response.text, vendor))
    return response.json()


def _silence_ranges(source: str | Path, duration: float = 0.0) -> list[dict]:
    """Detect silence ranges with the ffmpeg silencedetect filter."""
    ok, stderr = ff.run_ffmpeg(
        [
            "-i",
            str(source),
            "-af",
            f"silencedetect=noise={SILENCE_NOISE_DB}:d={SILENCE_MIN_LENGTH}",
            "-f",
            "null",
            "-",
        ]
    )
    if not ok:
        return []
    ranges: list[dict] = []
    pending: float | None = None
    for line in stderr.splitlines():
        match = SILENCE_PATTERN.search(line)
        if match is None:
            continue
        value = float(match.group(2))
        if match.group(1) == "start":
            pending = value
        elif pending is not None:
            ranges.append(_gap(pending, value))
            pending = None
    if pending is not None and duration > pending:
        ranges.append(_gap(pending, duration))
    return ranges


def _span(start: Any, end: Any) -> str:
    """Format a start/end pair as a clock range."""
    return f"{ff.format_time(start or 0)} - {ff.format_time(end or 0)}"


def _timeline_audio(segments: list[dict]) -> Path | None:
    """Render the timeline audio to a mono 16 kHz mp3 for transcription."""
    descriptor, name = tempfile.mkstemp(prefix="video_editor_timeline_", suffix=".mp3")
    os.close(descriptor)
    target = Path(name)
    return audio_ops.render_audio_range(
        segments,
        target,
        codec_args=["-c:a", "libmp3lame", "-q:a", "4"],
        sample_rate=16000,
        channels=1,
    )


def _transcription_endpoint(endpoint: str) -> str:
    """Derive the audio transcription endpoint from the chat endpoint."""
    if "/chat/completions" in endpoint:
        return endpoint.replace("/chat/completions", "/audio/transcriptions")
    return "https://api.openai.com/v1/audio/transcriptions"


def _transcription_provider() -> dict:
    """Pick the transcription provider: the Settings choice, then one with a key."""
    from tools import agent

    active = context.get_transcribe_provider() or context.get_provider_id()
    providers = agent.list_providers()
    if active:
        for item in providers:
            if item.get("vendor") == active:
                return item
    for item in providers:
        if item.get("apiKey"):
            return item
    return providers[0] if providers else agent.get_provider(None)


def _transcribe_model() -> str:
    """Return the configured transcription model, defaulting to whisper-1."""
    return context.get_transcribe_model() or TRANSCRIBE_MODEL


def _write_transcript(details: dict, name: str) -> Path | None:
    """Save a readable transcript into the output folder."""
    try:
        target = context.get_output_dir() / name
        target.write_text(transcript_text(details), encoding="utf-8")
    except OSError:
        return None
    return target
