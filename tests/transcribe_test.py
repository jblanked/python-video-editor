"""Transcription test: silence detection, audio extraction, and clip text mapping.

Run with: venv/bin/python tests/transcribe_test.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import agent as agent_module  # noqa: E402
from tools import context, transcribe_ops  # noqa: E402
from tools import ffmpeg_utils as ff  # noqa: E402
from tools import timeline_ops  # noqa: E402
from tools.dispatch import execute_tool  # noqa: E402
from tools.project import Project  # noqa: E402

SAMPLE = "/tmp/ve_test/sample_a.mp4"
SAMPLE_B = "/tmp/ve_test/sample_b.mp4"

FAILED: list[str] = []


def expect(name: str, condition: bool, detail: object = "") -> None:
    """Record a pass or fail for one check."""
    if condition:
        print(f"[ok ] {name}")
    else:
        FAILED.append(name)
        print(f"[ERR] {name} {detail}")


def make_gap_audio(path: Path) -> None:
    """Create a tone-silence-tone file for silence detection."""
    expression = "if(lt(t,2)+gt(t,4),sin(2*PI*440*t),0)"
    subprocess.run(
        [
            ff.get_ffmpeg_path(),
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc='{expression}':s=44100:d=6",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(path),
        ],
        check=True,
    )


def main() -> int:
    """Exercise the transcription helpers without hitting the network."""
    expect(
        "test media present",
        Path(SAMPLE).exists() and Path(SAMPLE_B).exists(),
        "run tests/smoke_test.py first",
    )
    if FAILED:
        print(f"transcription test failed: {len(FAILED)} check(s)")
        return 1

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)

        gap_file = temp / "gap.mp3"
        make_gap_audio(gap_file)
        gaps = transcribe_ops._silence_ranges(gap_file, 6.0)
        expect("one silence gap detected", len(gaps) == 1, gaps)
        if gaps:
            expect(
                "gap spans about 2-4s",
                abs(gaps[0]["start"] - 2.0) < 0.25 and abs(gaps[0]["end"] - 4.0) < 0.25,
                gaps[0],
            )

        extracted = transcribe_ops._extract_audio(SAMPLE)
        expect("audio extracted", extracted is not None and extracted.exists())
        if extracted:
            stream = ff.audio_stream(ff.probe(extracted))
            expect(
                "audio is mono 16 kHz",
                int(stream.get("channels") or 0) == 1
                and int(stream.get("sample_rate") or 0) == 16000,
                stream,
            )
            extracted.unlink()

        segments = [
            {"path": SAMPLE, "name": "a.mp4", "start": 2.0, "end": 6.0},
            {"path": SAMPLE_B, "name": "b.mp4", "start": 0.0, "end": 3.0},
        ]
        transcript = [
            {"start": 2.5, "end": 4.0, "text": "hello"},
            {"start": 5.5, "end": 6.5, "text": "world"},
        ]
        mapped = transcribe_ops._clip_texts(segments, transcript)
        expect(
            "clip text mapped",
            [item["text"] for item in mapped] == ["hello", "world"],
            mapped,
        )
        expect(
            "clip offsets mapped",
            mapped[0]["start"] == 0.0 and mapped[1]["start"] == 4.0 and mapped[1]["end"] == 7.0,
            mapped,
        )

        built = transcribe_ops._build_result({"text": "hi", "segments": transcript}, 7.0, gaps)
        details = built["details"]
        expect("speech total", abs(details["speech_seconds"] - 2.5) < 0.01, details)
        silence_total = sum(float(gap["duration"]) for gap in gaps)
        expect("silence total", abs(details["silence_seconds"] - silence_total) < 0.01, details)
        expect("duration text", details["duration_text"] == "00:00:07.000", details["duration_text"])

        rendered = transcribe_ops._timeline_audio(segments)
        expect("timeline audio rendered", rendered is not None and rendered.exists())
        if rendered:
            duration = ff.media_duration(ff.probe(rendered))
            expect("timeline audio length", abs(duration - 7.0) < 0.3, duration)
            rendered.unlink()

        spoken = transcribe_ops.transcribe_clip(SAMPLE_B)
        expect(
            "silent clip rejected",
            not spoken["success"] and "no audio" in spoken["message"],
            spoken,
        )

        project = Project()
        context.set_project(None)
        result = transcribe_ops.transcribe_timeline()
        expect(
            "no project reported",
            not result["success"] and "No project" in result["message"],
            result,
        )

        context.set_project(project)
        result = transcribe_ops.transcribe_timeline()
        expect(
            "empty timeline reported",
            not result["success"] and "empty" in result["message"],
            result,
        )

        project.add_to_timeline(SAMPLE_B)
        result = transcribe_ops.transcribe_timeline()
        expect(
            "silent timeline reported",
            not result["success"] and "no audio" in result["message"],
            result,
        )

        project.clear_timeline()
        project.add_to_timeline(SAMPLE)
        list_backup = agent_module.list_providers
        agent_module.list_providers = lambda: [
            {
                "vendor": "jblanked",
                "models": ["jblanked-v1"],
                "apiKey": "",
                "endpoint": "https://www.jblanked.com/ai/v1/chat/completions",
            }
        ]
        context.set_provider_id("jblanked")
        try:
            result = transcribe_ops.transcribe_clip(SAMPLE)
            expect(
                "missing key names the provider",
                not result["success"]
                and "jblanked" in result["message"]
                and "Settings" in result["message"],
                result,
            )
            result = transcribe_ops.transcribe_timeline()
            expect(
                "timeline missing key reported",
                not result["success"] and "API key" in result["message"],
                result,
            )
        finally:
            context.set_project(None)

        # The active provider wins over a keyed fallback, even without its own key.
        agent_module.list_providers = lambda: [
            {
                "vendor": "openai",
                "models": ["m"],
                "apiKey": "sk-test",
                "endpoint": "https://api.openai.com/v1/chat/completions",
            },
            {
                "vendor": "anthropic",
                "models": [],
                "apiKey": "",
                "endpoint": "https://api.anthropic.com/v1/messages",
            },
        ]
        context.set_provider_id("anthropic")
        result = transcribe_ops.transcribe_clip(SAMPLE)
        expect(
            "active provider wins over keyed fallback",
            not result["success"] and "anthropic" in result["message"],
            result,
        )

        # A keyed provider without an OpenAI-compatible endpoint is rejected before any call.
        agent_module.list_providers = lambda: [
            {
                "vendor": "anthropic",
                "models": [],
                "apiKey": "key-set",
                "endpoint": "https://api.anthropic.com/v1/messages",
            }
        ]
        result = transcribe_ops.transcribe_clip(SAMPLE)
        expect(
            "incompatible endpoint reported",
            not result["success"] and "OpenAI-compatible" in result["message"],
            result,
        )

        # The Settings transcription override wins over the active chat provider.
        agent_module.list_providers = lambda: [
            {
                "vendor": "openai",
                "models": ["m"],
                "apiKey": "sk-test",
                "endpoint": "https://api.openai.com/v1/chat/completions",
            },
            {
                "vendor": "jblanked",
                "models": ["jblanked-v1"],
                "apiKey": "key",
                "endpoint": "https://www.jblanked.com/ai/v1/chat/completions",
            },
        ]
        context.set_provider_id("jblanked")
        context.set_transcribe_provider("openai")
        provider = transcribe_ops._transcription_provider()
        expect(
            "transcribe override wins",
            provider.get("vendor") == "openai",
            provider.get("vendor"),
        )
        context.set_transcribe_provider(None)
        provider = transcribe_ops._transcription_provider()
        expect(
            "transcribe falls back to chat provider",
            provider.get("vendor") == "jblanked",
            provider.get("vendor"),
        )
        expect("default transcribe model", transcribe_ops._transcribe_model() == "whisper-1")
        context.set_transcribe_model("whisper-x")
        expect("configured transcribe model", transcribe_ops._transcribe_model() == "whisper-x")
        context.set_transcribe_model(None)

        html_body = "<!DOCTYPE html><html><body>Page not found</body></html>"
        message = transcribe_ops._api_error(404, html_body, "jblanked")
        expect(
            "html error sanitized",
            "not found" in message and "<" not in message and "jblanked" in message,
            message,
        )
        message = transcribe_ops._api_error(401, '{"error": "bad key"}', "openai")
        expect(
            "json error kept",
            "401" in message and "bad key" in message,
            message,
        )

        expect("language code kept", transcribe_ops._normalize_language("EN") == "en")
        expect("language name mapped", transcribe_ops._normalize_language("English") == "en")
        expect("language locale mapped", transcribe_ops._normalize_language("en-US") == "en")
        expect("language note mapped", transcribe_ops._normalize_language("English (US)") == "en")
        expect(
            "language multi-word mapped",
            transcribe_ops._normalize_language("Brazilian Portuguese") == "pt",
        )
        expect("language empty auto-detects", transcribe_ops._normalize_language("") is None)
        try:
            transcribe_ops._normalize_language("klingon")
            expect("unknown language rejected", False, "no error raised")
        except ValueError as exc:
            expect("unknown language rejected", "ISO-639-1" in str(exc), exc)

        # Results are saved into the output folder and reported in the message.
        fake = {
            "text": "hello world",
            "language": "en",
            "segments": [{"start": 0.0, "end": 4.0, "text": "hello world"}],
        }
        real_request = transcribe_ops._request_transcription
        transcribe_ops._request_transcription = lambda audio, language=None: fake
        context.set_output_dir(temp / "out")
        try:
            result = transcribe_ops.transcribe_clip(SAMPLE)
            clip_file = Path(str(result.get("output") or ""))
            expect("clip transcript saved", result["success"] and clip_file.exists(), result)
            expect(
                "clip transcript named",
                clip_file.name == "sample_a_transcript.txt",
                clip_file.name,
            )
            expect(
                "clip transcript text",
                clip_file.exists() and "hello world" in clip_file.read_text(encoding="utf-8"),
                clip_file,
            )
            expect(
                "message mentions the file",
                "Saved to sample_a_transcript.txt" in result["message"],
                result["message"],
            )

            context.set_project(project)
            result = transcribe_ops.transcribe_timeline()
            timeline_file = Path(str(result.get("output") or ""))
            expect(
                "timeline transcript saved",
                result["success"] and timeline_file.exists(),
                result,
            )
            expect(
                "timeline transcript named",
                timeline_file.name == "timeline_transcript.txt",
                timeline_file.name,
            )
            body = timeline_file.read_text(encoding="utf-8") if timeline_file.exists() else ""
            expect(
                "timeline transcript has per-clip text",
                "Per clip" in body and "hello world" in body,
                body,
            )
            formatted = transcribe_ops.transcript_text(
                {"text": "", "segments": [], "silence": []}
            )
            expect("empty transcript text", formatted == "No speech detected.", formatted)
        finally:
            transcribe_ops._request_transcription = real_request
            context.set_project(None)

        # Removing silence rebuilds the timeline from the speech parts only.
        context.set_project(project)
        project.clear_timeline()
        project.add_to_timeline(gap_file)
        result = execute_tool("timeline_remove_silence", {"margin": "0.05"})
        spans = [(float(item["start"]), float(item["end"])) for item in project.timeline]
        total = sum(end - start for start, end in spans)
        expect("silence removal succeeded", result["success"] and len(spans) == 2, result)
        expect("speech kept", abs(total - 3.9) < 0.4, spans)
        expect(
            "removed seconds reported",
            abs(float(result["details"]["removed_seconds"]) - 2.1) < 0.4,
            result["details"],
        )
        again = timeline_ops.timeline_remove_silence()
        expect("second run finds no silence", "No silence" in again["message"], again)
        context.set_project(None)

        context.set_provider_id(None)
        agent_module.list_providers = list_backup

        endpoint = transcribe_ops._transcription_endpoint(
            "https://api.openai.com/v1/chat/completions"
        )
        expect("endpoint mapping", endpoint.endswith("/audio/transcriptions"), endpoint)
        custom = transcribe_ops._transcription_endpoint(
            "https://www.jblanked.com/ai/v1/chat/completions"
        )
        expect(
            "custom endpoint mapping",
            custom == "https://www.jblanked.com/ai/v1/audio/transcriptions",
            custom,
        )
        fallback = transcribe_ops._transcription_endpoint("")
        expect(
            "endpoint fallback",
            fallback == "https://api.openai.com/v1/audio/transcriptions",
            fallback,
        )

    if FAILED:
        print(f"transcription test failed: {len(FAILED)} check(s)")
        return 1
    print("transcription test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
