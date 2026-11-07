"""Text to speech with word level timings.

Uses Microsoft Edge's neural voices through the ``edge-tts`` package. The
stream carries ``WordBoundary`` events, which is what lets us build captions
that are genuinely synchronised with the spoken audio instead of guessed.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .config import settings

log = logging.getLogger(__name__)

# The speech service is occasionally flaky; retry a beat before giving up.
MAX_ATTEMPTS = 4
RETRY_BACKOFF = 1.5


@dataclass
class WordTiming:
    text: str
    start: float
    end: float


@dataclass
class VoiceResult:
    audio_path: Path
    duration: float
    words: List[WordTiming] = field(default_factory=list)
    voice: str = ""


# Fallback voices, used when the live voice list cannot be fetched.
FALLBACK_VOICES = [
    {"name": "en-US-AndrewNeural", "label": "Andrew - US male, warm", "locale": "en-US", "gender": "Male"},
    {"name": "en-US-BrianNeural", "label": "Brian - US male, casual", "locale": "en-US", "gender": "Male"},
    {"name": "en-US-GuyNeural", "label": "Guy - US male, news", "locale": "en-US", "gender": "Male"},
    {"name": "en-US-AriaNeural", "label": "Aria - US female, confident", "locale": "en-US", "gender": "Female"},
    {"name": "en-US-JennyNeural", "label": "Jenny - US female, friendly", "locale": "en-US", "gender": "Female"},
    {"name": "en-GB-RyanNeural", "label": "Ryan - UK male", "locale": "en-GB", "gender": "Male"},
    {"name": "en-GB-SoniaNeural", "label": "Sonia - UK female", "locale": "en-GB", "gender": "Female"},
    {"name": "en-NG-EzinneNeural", "label": "Ezinne - Nigerian female", "locale": "en-NG", "gender": "Female"},
    {"name": "en-KE-AsiliaNeural", "label": "Asilia - Kenyan female", "locale": "en-KE", "gender": "Female"},
    {"name": "en-IN-NeerjaNeural", "label": "Neerja - Indian female", "locale": "en-IN", "gender": "Female"},
]


def _normalise_for_speech(text: str) -> str:
    """Make text friendlier for the synthesiser."""
    text = re.sub(r"\s+", " ", text or "").strip()
    text = text.replace("&", " and ")
    text = re.sub(r"\s*[\u2013\u2014]\s*", ", ", text)
    return text


def _probe_duration(path: Path) -> float:
    """Read a media file's duration with ffprobe."""
    import subprocess

    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return float(out.stdout.strip())
    except Exception as exc:  # noqa: BLE001
        log.warning("ffprobe failed for %s: %s", path, exc)
        return 0.0


def estimate_word_timings(text: str, duration: float) -> List[WordTiming]:
    """Distribute words across ``duration`` when the service sends no boundaries.

    Weighted by word length so longer words get proportionally more time, which
    keeps the karaoke highlight close to the real speech.
    """
    tokens = [t for t in re.split(r"\s+", (text or "").strip()) if t]
    if not tokens or duration <= 0:
        return []
    weights = [max(1, len(re.sub(r"[^\w']", "", t))) for t in tokens]
    total = sum(weights)
    timings: List[WordTiming] = []
    cursor = 0.0
    for token, weight in zip(tokens, weights):
        span = duration * weight / total
        timings.append(WordTiming(text=token, start=cursor, end=cursor + span))
        cursor += span
    return timings


async def _synthesise(text: str, voice: str, out_path: Path, rate: str) -> List[WordTiming]:
    import edge_tts

    # Ask for word level boundaries explicitly: the service defaults to
    # sentence boundaries, which are far too coarse for karaoke captions.
    communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    words: List[WordTiming] = []

    with open(out_path, "wb") as fh:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # edge-tts reports offsets in 100-nanosecond ticks.
                start = chunk["offset"] / 1e7
                end = (chunk["offset"] + chunk["duration"]) / 1e7
                words.append(WordTiming(text=chunk["text"], start=start, end=end))

    return words


def synthesise(
    text: str,
    out_path: Path,
    voice: Optional[str] = None,
    rate: Optional[str] = None,
) -> VoiceResult:
    """Render ``text`` to an MP3 and return it with word level timings."""
    voice = voice or settings.default_voice
    rate = rate or settings.tts_rate
    spoken = _normalise_for_speech(text)
    if not spoken:
        raise ValueError("nothing to speak")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # The speech service occasionally returns an empty stream (transient rate
    # limiting). Retry with backoff before giving up on the beat.
    last_error: Optional[Exception] = None
    words: List[WordTiming] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            words = asyncio.run(_synthesise(spoken, voice, out_path, rate))
            if out_path.exists() and out_path.stat().st_size > 0:
                break
            raise RuntimeError("empty audio stream")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.warning(
                "voice attempt %d/%d failed for %r: %s", attempt, MAX_ATTEMPTS, voice, exc
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF * attempt)
    else:
        raise RuntimeError(f"voice synthesis failed after {MAX_ATTEMPTS} attempts: {last_error}")

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"voice synthesis produced no audio for voice {voice!r}")

    duration = _probe_duration(out_path)
    if duration <= 0:
        raise RuntimeError("could not determine voiceover duration")

    if not words:
        log.info("no word boundaries returned; estimating timings from duration")
        words = estimate_word_timings(spoken, duration)

    return VoiceResult(audio_path=out_path, duration=duration, words=words, voice=voice)


def list_voices() -> List[dict]:
    """Return the available voices, falling back to a curated list offline."""
    try:
        import edge_tts

        raw = asyncio.run(edge_tts.list_voices())
        voices = []
        for v in raw:
            short = v.get("ShortName", "")
            if not short:
                continue
            locale = v.get("Locale", "")
            friendly = short.split("-", 2)[-1].replace("Neural", "")
            voices.append(
                {
                    "name": short,
                    "label": f"{friendly} - {locale}",
                    "locale": locale,
                    "gender": v.get("Gender", ""),
                }
            )
        voices.sort(key=lambda x: (not x["locale"].startswith("en"), x["locale"], x["name"]))
        return voices or FALLBACK_VOICES
    except Exception as exc:  # noqa: BLE001
        log.warning("could not fetch voice list (%s); using fallback", exc)
        return FALLBACK_VOICES
