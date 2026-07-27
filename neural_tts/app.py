import base64
import io
import json
import os
import re
import threading
from contextlib import asynccontextmanager

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from kokoro import KPipeline
from phonemizer.separator import Separator
from pydantic import BaseModel, Field


VOICE_CATALOG = {
    "pf_dora": {
        "name": "Lia",
        "language": "pt-BR",
        "description": "Clara, acolhedora e equilibrada.",
    },
    "pm_alex": {
        "name": "Caio",
        "language": "pt-BR",
        "description": "Objetiva, firme e contemporânea.",
    },
    "pm_santa": {
        "name": "Ravi",
        "language": "pt-BR",
        "description": "Grave, calma e reflexiva.",
    },
}
SAMPLE_RATE = 24_000
_pipeline = None
_pipeline_lock = threading.Lock()
_inference_lock = threading.Lock()


class SynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5_000)
    voice: str
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:
                configured_device = os.getenv("TTS_DEVICE", "").strip() or None
                _pipeline = KPipeline(
                    lang_code="p",
                    repo_id=os.getenv("KOKORO_MODEL_ID", "hexgrad/Kokoro-82M"),
                    device=configured_device,
                )
    return _pipeline


def _phonemes_with_word_boundaries(pipeline, text):
    g2p = pipeline.g2p
    text = text.replace("«", chr(8220)).replace("»", chr(8221))
    text = text.replace("(", "«").replace(")", "»")
    phonemes = g2p.backend.phonemize(
        [text],
        separator=Separator(phone="", word="|", syllable=""),
    )[0].strip()
    for old, new in g2p.e2m:
        phonemes = phonemes.replace(old, new)
    phonemes = phonemes.replace("^", "")
    if g2p.version == "2.0":
        phonemes = phonemes.replace(chr(809), "").replace(chr(810), "")
        phonemes = re.sub(r"(\S)\u0329", r"ᵊ\1", phonemes)
    else:
        phonemes = phonemes.replace("-", "")
    return phonemes.replace("«", "(").replace("»", ")").strip("|")


def _result_word_timings(pipeline, result):
    if result.pred_dur is None or result.audio is None:
        return []
    bounded = _phonemes_with_word_boundaries(pipeline, result.graphemes)
    phoneme_words = bounded.split("|") if bounded else []
    grapheme_words = re.findall(r"\S+", result.graphemes)
    reconstructed = " ".join(phoneme_words)
    if reconstructed != result.phonemes or len(phoneme_words) != len(grapheme_words):
        return []

    durations = result.pred_dur.detach().cpu().tolist()
    if len(durations) != len(result.phonemes) + 2 or not sum(durations):
        return []
    unit_seconds = (
        len(result.audio) / sum(durations) / SAMPLE_RATE
    )
    phoneme_durations = durations[1:-1]
    prefix = [0]
    for duration in phoneme_durations:
        prefix.append(prefix[-1] + duration)

    leading_seconds = durations[0] * unit_seconds
    audio_duration = len(result.audio) / SAMPLE_RATE
    timings = []
    char_cursor = 0
    for index, (word, phoneme_word) in enumerate(
        zip(grapheme_words, phoneme_words)
    ):
        start = leading_seconds + prefix[char_cursor] * unit_seconds
        char_cursor += len(phoneme_word)
        if index < len(phoneme_words) - 1:
            char_cursor += 1
            end = leading_seconds + prefix[char_cursor] * unit_seconds
        else:
            end = audio_duration
        timings.append([word, round(start, 4), round(end, 4)])
    return timings


def render_wav(payload):
    pipeline = get_pipeline()
    audio_chunks = []
    word_timings = []
    time_offset = 0.0
    with _inference_lock:
        for result in pipeline(
            payload.text,
            voice=payload.voice,
            speed=payload.speed,
            split_pattern=r"\n+",
        ):
            audio = result.audio
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            audio_chunks.append(np.asarray(audio, dtype=np.float32))
            result_timings = _result_word_timings(pipeline, result)
            word_timings.extend(
                [
                    [word, round(start + time_offset, 4), round(end + time_offset, 4)]
                    for word, start, end in result_timings
                ]
            )
            time_offset += len(audio) / SAMPLE_RATE
    if not audio_chunks:
        raise ValueError("O modelo não produziu áudio para o texto informado.")
    audio = np.concatenate(audio_chunks)
    output = io.BytesIO()
    sf.write(output, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return output.getvalue(), word_timings


@asynccontextmanager
async def lifespan(_app):
    if os.getenv("TTS_PRELOAD_MODEL", "0").lower() in {"1", "true", "yes", "on"}:
        get_pipeline()
    yield


app = FastAPI(
    title="ProjectLecture Neural TTS",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "engine": "kokoro",
        "device": os.getenv("TTS_DEVICE", "auto"),
        "model_loaded": _pipeline is not None,
    }


@app.get("/voices")
def voices():
    return [
        {"id": voice_id, **metadata}
        for voice_id, metadata in VOICE_CATALOG.items()
    ]


@app.post("/synthesize")
def synthesize(payload: SynthesisRequest):
    if payload.voice not in VOICE_CATALOG:
        raise HTTPException(status_code=422, detail="Voz neural desconhecida.")
    try:
        wav, word_timings = render_wav(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Falha ao sintetizar áudio: {exc}"
        ) from exc
    timing_header = base64.urlsafe_b64encode(
        json.dumps(word_timings, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).decode("ascii")
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={
            "X-TTS-Engine": "kokoro",
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Word-Timings": timing_header,
        },
    )
