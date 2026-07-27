import base64
import html
import json
import re
import subprocess
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass

from django.conf import settings


MAX_SEGMENT_CHARS = 1200


@dataclass
class TextSegment:
    text: str
    start_char: int
    end_char: int


def split_text(text, max_chars=MAX_SEGMENT_CHARS):
    text = text.strip()
    if not text:
        return []

    candidates = re.split(r"(?<=[.!?;:])\s+|\n{2,}", text)
    pieces = []
    cursor = 0
    buffer = ""
    buffer_start = 0

    def append_buffer():
        nonlocal buffer
        if buffer:
            pieces.append(TextSegment(buffer, buffer_start, buffer_start + len(buffer)))
            buffer = ""

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        found_at = text.find(candidate, cursor)
        start = found_at if found_at >= 0 else cursor
        cursor = start + len(candidate)

        if len(candidate) > max_chars:
            append_buffer()
            local_start = 0
            while local_start < len(candidate):
                local_end = min(local_start + max_chars, len(candidate))
                if local_end < len(candidate):
                    break_at = candidate.rfind(" ", local_start, local_end)
                    if break_at > local_start:
                        local_end = break_at
                chunk = candidate[local_start:local_end].strip()
                chunk_offset = candidate.find(chunk, local_start)
                pieces.append(
                    TextSegment(chunk, start + chunk_offset, start + chunk_offset + len(chunk))
                )
                local_start = local_end
            continue

        proposed = f"{buffer} {candidate}".strip()
        if buffer and len(proposed) > max_chars:
            append_buffer()
        if not buffer:
            buffer_start = start
            buffer = candidate
        else:
            buffer = f"{buffer} {candidate}"
    append_buffer()
    return pieces


def neural_speed(words_per_minute):
    return min(1.75, max(0.55, words_per_minute / 170))


def synthesize_neural(text, output_path, voice_code, words_per_minute):
    payload = json.dumps(
        {
            "text": text,
            "voice": voice_code,
            "speed": neural_speed(words_per_minute),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.NEURAL_TTS_URL.rstrip('/')}/synthesize",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "audio/wav"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=settings.NEURAL_TTS_TIMEOUT_SECONDS
        ) as response:
            output_path.write_bytes(response.read())
            encoded_timings = response.headers.get("X-Word-Timings", "")
            if not encoded_timings:
                return []
            return json.loads(
                base64.urlsafe_b64decode(encoded_timings.encode("ascii")).decode(
                    "utf-8"
                )
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"O serviço neural recusou a síntese: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "O serviço de voz neural está indisponível. "
            "Verifique o container neural-tts."
        ) from exc


def azure_rate(words_per_minute):
    """Convert the UI's words-per-minute setting to an SSML percentage."""
    percentage = round((words_per_minute / 170 - 1) * 100)
    return min(75, max(-50, percentage))


def azure_ssml(text, voice_code, words_per_minute):
    rate = azure_rate(words_per_minute)
    signed_rate = f"{rate:+d}%"
    return (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="pt-BR">'
        f'<voice name="{html.escape(voice_code, quote=True)}">'
        f'<prosody rate="{signed_rate}">{html.escape(text)}</prosody>'
        "</voice></speak>"
    )


def finalize_azure_timings(boundaries, duration_seconds):
    """Keep punctuation pauses attached to the preceding highlighted word."""
    ordered = sorted(
        (
            {
                "text": str(boundary["text"]),
                "start": max(0.0, float(boundary["start"])),
            }
            for boundary in boundaries
            if re.search(r"\w", str(boundary.get("text", "")), re.UNICODE)
        ),
        key=lambda boundary: boundary["start"],
    )
    timings = []
    for index, boundary in enumerate(ordered):
        next_start = (
            ordered[index + 1]["start"]
            if index + 1 < len(ordered)
            else duration_seconds
        )
        end = max(boundary["start"], min(float(duration_seconds), next_start))
        timings.append(
            [
                boundary["text"],
                round(min(boundary["start"], duration_seconds), 4),
                round(end, 4),
            ]
        )
    return timings


def synthesize_azure(text, output_path, voice_code, words_per_minute):
    if not settings.AZURE_SPEECH_ENABLED:
        raise RuntimeError(
            "O Azure Speech não está configurado. Defina AZURE_SPEECH_KEY "
            "e AZURE_SPEECH_REGION no ambiente do worker."
        )

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:
        raise RuntimeError(
            "O SDK do Azure Speech não está instalado no ambiente."
        ) from exc

    speech_config = speechsdk.SpeechConfig(
        subscription=settings.AZURE_SPEECH_KEY,
        region=settings.AZURE_SPEECH_REGION,
    )
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )
    audio_config = speechsdk.audio.AudioOutputConfig(filename=str(output_path))
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )
    boundaries = []

    def collect_boundary(event):
        boundaries.append(
            {
                "text": event.text,
                "start": event.audio_offset / 10_000_000,
            }
        )

    synthesizer.synthesis_word_boundary.connect(collect_boundary)
    result = synthesizer.speak_ssml_async(
        azure_ssml(text, voice_code, words_per_minute)
    ).get()

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        cancellation = speechsdk.SpeechSynthesisCancellationDetails(result)
        detail = cancellation.error_details or str(cancellation.reason)
        if "429" in detail or "quota" in detail.lower():
            raise RuntimeError(
                "O limite gratuito mensal ou de solicitações do Azure Speech "
                "foi atingido. Tente novamente mais tarde."
            )
        raise RuntimeError(f"O Azure Speech recusou a síntese: {detail}")

    with wave.open(str(output_path), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    return finalize_azure_timings(boundaries, duration)


def synthesize_segment(
    text, output_path, voice_code, words_per_minute, provider="espeak"
):
    if provider in {"kokoro", "chatterbox"}:
        return synthesize_neural(text, output_path, voice_code, words_per_minute)
    if provider == "azure":
        return synthesize_azure(text, output_path, voice_code, words_per_minute)
    command = [
        "espeak-ng",
        "-v",
        voice_code,
        "-s",
        str(words_per_minute),
        "-w",
        str(output_path),
        text,
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
    return []


def concatenate_wav(input_paths, output_path):
    params = None
    total_frames = 0
    segment_ranges = []
    with wave.open(str(output_path), "wb") as output:
        for input_path in input_paths:
            with wave.open(str(input_path), "rb") as source:
                if params is None:
                    params = source.getparams()
                    output.setparams(params)
                frames = source.readframes(source.getnframes())
                start_seconds = total_frames / source.getframerate()
                total_frames += source.getnframes()
                end_seconds = total_frames / source.getframerate()
                output.writeframes(frames)
                segment_ranges.append((start_seconds, end_seconds))
    duration = segment_ranges[-1][1] if segment_ranges else 0
    return duration, segment_ranges


def safe_audio_name(document_id):
    return f"documento-{document_id}.wav"
