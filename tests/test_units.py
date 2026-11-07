"""Unit tests for the pure-Python parts of the engine (no network, no ffmpeg)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import captions, scriptgen, visuals
from app.tts import WordTiming, estimate_word_timings


# --------------------------------------------------------------------------- #
# Script generation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "length,expected",
    [("30 seconds", 4), ("60 seconds", 7), ("90 seconds", 10), ("3 minutes", 16)],
)
def test_scene_count_mapping(length: str, expected: int) -> None:
    assert scriptgen.scene_count_for(length) == expected


def test_offline_script_has_all_parts() -> None:
    script = scriptgen.generate_script_offline("5 habits that change your life", length="60 seconds")
    assert script.hook.strip()
    assert script.cta.strip()
    assert len(script.scenes) == 7
    for scene in script.scenes:
        assert scene.narration.strip()
        assert scene.caption.strip()
        assert scene.caption == scene.caption.upper()
        assert len(scene.caption) <= 70


def test_offline_script_is_deterministic() -> None:
    a = scriptgen.generate_script_offline("compound interest", length="30 seconds")
    b = scriptgen.generate_script_offline("compound interest", length="30 seconds")
    assert a.to_text() == b.to_text()


def test_offline_script_mentions_the_topic() -> None:
    script = scriptgen.generate_script_offline("compound interest", length="30 seconds")
    assert "compound interest" in script.full_narration().lower()


def test_script_to_text_round_trips_labels() -> None:
    script = scriptgen.generate_script_offline("focus", length="30 seconds")
    text = script.to_text()
    assert "HOOK:" in text
    assert "SCENE 1:" in text
    assert "ON-SCREEN TEXT:" in text
    assert "CTA:" in text


def test_script_from_text_strips_labels_and_splits() -> None:
    raw = (
        "HOOK: This is the opening line of the video.\n"
        "SCENE 1: The first real point that we want to make here.\n"
        "SCENE 2: The second point, which builds on the first one.\n"
        "CTA: Follow for more videos like this one."
    )
    script = scriptgen.script_from_text(raw, length="60 seconds")
    assert len(script.scenes) >= 3
    # The labels must be stripped from the narration itself, not carried over.
    for scene in script.scenes:
        assert "HOOK:" not in scene.narration
        assert "SCENE" not in scene.narration.upper()
        assert "CTA:" not in scene.narration
    assert all(s.narration.strip() for s in script.scenes)


def test_script_from_text_rejects_empty() -> None:
    with pytest.raises(ValueError):
        scriptgen.script_from_text("   ")


def test_caption_from_is_short_and_upper() -> None:
    caption = scriptgen._caption_from(
        "The uncomfortable truth about compound interest is that almost nobody starts."
    )
    assert caption == caption.upper()
    assert len(caption) <= 62


# --------------------------------------------------------------------------- #
# Word timings
# --------------------------------------------------------------------------- #
def test_estimate_word_timings_is_monotonic_and_complete() -> None:
    words = estimate_word_timings("one two three four five", 5.0)
    assert len(words) == 5
    assert words[0].start == 0.0
    assert words[-1].end == pytest.approx(5.0, abs=1e-6)
    for a, b in zip(words, words[1:]):
        assert a.end <= b.start + 1e-9


def test_estimate_word_timings_handles_empty() -> None:
    assert estimate_word_timings("", 5.0) == []
    assert estimate_word_timings("hello", 0.0) == []


# --------------------------------------------------------------------------- #
# Captions
# --------------------------------------------------------------------------- #
def _words(n: int, step: float = 0.4) -> list[WordTiming]:
    return [WordTiming(text=f"word{i}", start=i * step, end=i * step + step * 0.8) for i in range(n)]


def test_chunk_words_respects_limits() -> None:
    chunks = captions.chunk_words(_words(20), max_words=4, max_chars=26)
    assert chunks
    for chunk in chunks:
        assert 1 <= len(chunk.words) <= 4
        assert len(chunk.text) <= 26
    # Every word must appear exactly once, in order.
    flat = [w.text for c in chunks for w in c.words]
    assert flat == [w.text for w in _words(20)]


def test_chunk_words_splits_on_long_pause() -> None:
    words = [
        WordTiming("hello", 0.0, 0.4),
        WordTiming("there", 0.5, 0.9),
        WordTiming("again", 3.0, 3.4),  # 2.1s gap
    ]
    chunks = captions.chunk_words(words, max_gap=0.7)
    assert len(chunks) == 2


def test_build_ass_writes_valid_file(workdir: Path) -> None:
    path = captions.build_ass(_words(12), workdir / "c.ass", 1080, 1920, style="bold", brand="TEST BRAND", brand_until=2.0)
    text = path.read_text(encoding="utf-8")
    assert "[Script Info]" in text
    assert "[V4+ Styles]" in text
    assert "[Events]" in text
    assert "Style: Caption," in text
    assert "Style: Brand," in text
    assert "Dialogue:" in text
    assert "TEST BRAND" in text
    # One dialogue line per word, plus the brand line.
    assert text.count("Dialogue:") == 12 + 1


def test_build_ass_escapes_braces(workdir: Path) -> None:
    words = [WordTiming("a{b}c", 0.0, 0.5)]
    path = captions.build_ass(words, workdir / "c.ass", 1080, 1920)
    text = path.read_text(encoding="utf-8")
    assert "a(b)c" in text


def test_build_srt_writes_valid_file(workdir: Path) -> None:
    path = captions.build_srt(_words(8), workdir / "c.srt")
    text = path.read_text(encoding="utf-8")
    assert "-->" in text
    assert text.strip().startswith("1")


def test_ass_time_format() -> None:
    assert captions._ass_time(0) == "0:00:00.00"
    assert captions._ass_time(61.5) == "0:01:01.50"
    assert captions._ass_time(3661.25) == "1:01:01.25"


def test_srt_time_format() -> None:
    assert captions._srt_time(0) == "00:00:00,000"
    assert captions._srt_time(61.5) == "00:01:01,500"


# --------------------------------------------------------------------------- #
# Visuals
# --------------------------------------------------------------------------- #
def test_generate_background_writes_correct_size(workdir: Path) -> None:
    from PIL import Image

    path = visuals.generate_background(workdir / "bg.jpg", 270, 480, palette="ocean", seed=1, variant=0)
    assert path.exists()
    with Image.open(path) as img:
        assert img.size == (270, 480)
        assert img.mode == "RGB"


def test_generate_background_is_deterministic(workdir: Path) -> None:
    a = visuals.generate_background(workdir / "a.jpg", 120, 200, palette="ember", seed=42, variant=3)
    b = visuals.generate_background(workdir / "b.jpg", 120, 200, palette="ember", seed=42, variant=3)
    assert a.read_bytes() == b.read_bytes()


def test_palette_rotation_starts_with_base() -> None:
    assert visuals.palette_for_index("ocean", 0) == "ocean"
    assert visuals.palette_for_index("ocean", 1) != "ocean"
    assert visuals.palette_for_index("nonsense", 0) in visuals.BACKGROUND_NAMES
