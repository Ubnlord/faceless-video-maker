"""Caption generation.

Produces two artefacts from the same word timings:

* an ``.ass`` file with karaoke tags, burned into the video by libass, and
* an ``.srt`` sidecar for platforms that prefer soft subtitles.

Captions are grouped into short chunks (a few words at a time) and each chunk
is highlighted word by word as it is spoken, which is the style short-form
video actually uses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from .tts import WordTiming

# Caption looks. name -> (font, size, primary, outline, back, bold, border)
CAPTION_STYLES = {
    "bold":    {"font": "DejaVu Sans", "size": 78, "primary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H80000000", "bold": -1, "border": 4},
    "minimal": {"font": "DejaVu Sans", "size": 62, "primary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H00000000", "bold": 0, "border": 2},
    "boxed":   {"font": "DejaVu Sans", "size": 70, "primary": "&H00FFFFFF", "outline": "&H00000000", "back": "&HCC000000", "bold": -1, "border": 3},
    "neon":    {"font": "DejaVu Sans", "size": 76, "primary": "&H00FFFFFF", "outline": "&H00FF7BE0", "back": "&H80000000", "bold": -1, "border": 5},
}

HIGHLIGHT = "&H0000E5FF"  # BGR: warm amber highlight for the spoken word


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def _escape_srt(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class CaptionChunk:
    words: List[WordTiming]
    start: float
    end: float

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def chunk_words(
    words: Sequence[WordTiming],
    max_words: int = 4,
    max_chars: int = 26,
    max_gap: float = 0.7,
) -> List[CaptionChunk]:
    """Group word timings into short, readable caption chunks."""
    chunks: List[CaptionChunk] = []
    current: List[WordTiming] = []

    def flush() -> None:
        if current:
            chunks.append(CaptionChunk(words=list(current), start=current[0].start, end=current[-1].end))
            current.clear()

    for word in words:
        if current:
            gap = word.start - current[-1].end
            projected = len(" ".join(w.text for w in current)) + 1 + len(word.text)
            if len(current) >= max_words or projected > max_chars or gap > max_gap:
                flush()
        current.append(word)
    flush()
    return chunks


def build_ass(
    words: Sequence[WordTiming],
    out_path: Path,
    width: int,
    height: int,
    style: str = "bold",
    brand: str = "",
    brand_until: float = 0.0,
) -> Path:
    """Write a karaoke-style ASS subtitle file."""
    cfg = CAPTION_STYLES.get(style, CAPTION_STYLES["bold"])
    chunks = chunk_words(words)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{cfg['font']},{cfg['size']},{cfg['primary']},{HIGHLIGHT},{cfg['outline']},{cfg['back']},{cfg['bold']},0,0,0,100,100,0,0,1,{cfg['border']},2,2,60,60,{int(height * 0.30)},1
Style: Brand,{cfg['font']},{max(28, cfg['size'] // 3)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,2,0,1,2,1,8,60,60,{int(height * 0.055)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: List[str] = []

    if brand and brand_until > 0:
        lines.append(
            f"Dialogue: 0,{_ass_time(0)},{_ass_time(brand_until)},Brand,,0,0,0,,{_escape_ass(brand.upper())}"
        )

    for chunk in chunks:
        # One dialogue line per word so the spoken word can be highlighted.
        for i, word in enumerate(chunk.words):
            start = word.start
            end = chunk.words[i + 1].start if i + 1 < len(chunk.words) else chunk.end
            if end <= start:
                end = start + 0.12
            parts = []
            for j, w in enumerate(chunk.words):
                text = _escape_ass(w.text)
                if j == i:
                    parts.append(f"{{\\c{HIGHLIGHT}\\fscx108\\fscy108}}{text}{{\\r}}")
                else:
                    parts.append(text)
            lines.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{' '.join(parts)}"
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def build_srt(words: Sequence[WordTiming], out_path: Path) -> Path:
    """Write a plain SRT sidecar from the same timings."""
    chunks = chunk_words(words, max_words=6, max_chars=42)
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        blocks.append(
            f"{i}\n{_srt_time(chunk.start)} --> {_srt_time(chunk.end)}\n{_escape_srt(chunk.text)}\n"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(blocks), encoding="utf-8")
    return out_path
