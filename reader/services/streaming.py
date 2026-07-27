import math
import re
import wave
from dataclasses import dataclass
from difflib import SequenceMatcher


TOKEN_PATTERN = re.compile(r"\S+|\s+")
LETTER_PATTERN = re.compile(r"[\wÀ-ÿ]", re.UNICODE)


@dataclass
class DisplayToken:
    text: str
    is_word: bool
    start_char: int
    end_char: int


def tokenize_display_text(text, base_offset=0):
    return [
        DisplayToken(
            text=match.group(0),
            is_word=not match.group(0).isspace(),
            start_char=base_offset + match.start(),
            end_char=base_offset + match.end(),
        )
        for match in TOKEN_PATTERN.finditer(text)
    ]


def _speech_weight(token):
    letters = len(LETTER_PATTERN.findall(token.text))
    weight = max(1.0, math.sqrt(max(1, letters)))
    if token.text.endswith((",", ":", ";")):
        weight += 0.45
    elif token.text.endswith((".", "!", "?")):
        weight += 0.9
    return weight


def build_word_timings(text, duration_seconds, base_offset=0):
    words = [
        token for token in tokenize_display_text(text, base_offset) if token.is_word
    ]
    if not words or duration_seconds <= 0:
        return []

    weights = [_speech_weight(word) for word in words]
    total_weight = sum(weights)
    cursor = 0.0
    timings = []
    for index, (word, weight) in enumerate(zip(words, weights)):
        start = cursor
        cursor += duration_seconds * weight / total_weight
        end = duration_seconds if index == len(words) - 1 else cursor
        timings.append(
            {
                "text": word.text,
                "start": round(start, 4),
                "end": round(end, 4),
                "char_start": word.start_char,
                "char_end": word.end_char,
            }
        )
    return timings


def _normalized_word(text):
    return "".join(character.lower() for character in text if character.isalnum())


def map_spoken_word_timings(
    display_text,
    spoken_timings,
    duration_seconds,
    base_offset=0,
):
    display_words = [
        token
        for token in tokenize_display_text(display_text, base_offset)
        if token.is_word
    ]
    if not display_words or not spoken_timings:
        return []

    spoken = [
        {
            "text": timing[0],
            "start": float(timing[1]),
            "end": float(timing[2]),
        }
        for timing in spoken_timings
        if len(timing) == 3
    ]
    if not spoken:
        return []

    matcher = SequenceMatcher(
        None,
        [_normalized_word(word.text) for word in display_words],
        [_normalized_word(timing["text"]) for timing in spoken],
        autojunk=False,
    )
    mapped = [None] * len(display_words)

    def distribute(display_start, display_end, speech_start, speech_end):
        selected_words = display_words[display_start:display_end]
        if not selected_words:
            return
        if speech_start >= speech_end:
            boundary = (
                spoken[speech_start - 1]["end"]
                if speech_start
                else spoken[0]["start"]
            )
            for index in range(display_start, display_end):
                mapped[index] = (boundary, boundary)
            return

        interval_start = spoken[speech_start]["start"]
        interval_end = spoken[speech_end - 1]["end"]
        weights = [_speech_weight(word) for word in selected_words]
        total_weight = sum(weights)
        cursor = interval_start
        for relative_index, weight in enumerate(weights):
            start = cursor
            cursor += (interval_end - interval_start) * weight / total_weight
            end = (
                interval_end
                if relative_index == len(selected_words) - 1
                else cursor
            )
            mapped[display_start + relative_index] = (start, end)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for display_index, speech_index in zip(range(i1, i2), range(j1, j2)):
                mapped[display_index] = (
                    spoken[speech_index]["start"],
                    spoken[speech_index]["end"],
                )
        elif tag in {"replace", "delete"}:
            distribute(i1, i2, j1, j2)
        elif tag == "insert" and i1:
            previous = mapped[i1 - 1]
            if previous:
                mapped[i1 - 1] = (previous[0], spoken[j2 - 1]["end"])

    output = []
    for word, timing in zip(display_words, mapped):
        if timing is None:
            continue
        start, end = timing
        output.append(
            {
                "text": word.text,
                "start": round(max(0, min(start, duration_seconds)), 4),
                "end": round(max(0, min(end, duration_seconds)), 4),
                "char_start": word.start_char,
                "char_end": word.end_char,
            }
        )
    return output


def wav_duration(path):
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()
