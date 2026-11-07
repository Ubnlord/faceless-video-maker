"""Video rendering with ffmpeg.

Pipeline per scene:

    background JPEG  --zoompan-->  animated clip  --fade-->  scene.mp4

Then all scene clips are concatenated, the voiceover is muxed in, an optional
procedural music bed is mixed underneath, and the ASS captions are burned in
by libass during the final encode.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from .config import settings

log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    """Raised when ffmpeg fails."""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def run_ffmpeg(args: Sequence[str], timeout: int = 1800) -> None:
    """Run ffmpeg, raising :class:`RenderError` with the tail of stderr on failure."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-12:]
        raise RenderError("ffmpeg failed: " + " | ".join(tail))


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return float(out.stdout.strip())


def _escape_filter_path(path: Path) -> str:
    """Escape a filesystem path for use inside an ffmpeg filtergraph."""
    s = path.resolve().as_posix()
    for ch in ("\\", ":", "'", ",", "[", "]", ";"):
        s = s.replace(ch, "\\" + ch)
    return s


@dataclass
class SceneClip:
    index: int
    path: Path
    duration: float


# --------------------------------------------------------------------------- #
# Scene clips
# --------------------------------------------------------------------------- #
def render_scene_clip(
    image_path: Path,
    out_path: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
    motion: str = "zoom_in",
    fade: float = 0.35,
) -> SceneClip:
    """Turn a still background into an animated clip of ``duration`` seconds."""
    frames = max(2, int(round(duration * fps)))

    # zoompan works on an upscaled source so the motion stays smooth.
    scale_w = int(width * 1.6)
    scale_h = int(height * 1.6)

    if motion == "zoom_out":
        z = "if(eq(on,1),1.18,max(zoom-0.0009,1.0))"
    elif motion in ("pan_up", "pan_down"):
        z = "1.14"
    else:  # zoom_in
        z = "min(zoom+0.0009,1.18)"

    if motion == "pan_up":
        x, y = "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(1-on/{frames})"
    elif motion == "pan_down":
        x, y = "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(on/{frames})"
    else:
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"

    vf = (
        f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase,"
        f"crop={scale_w}:{scale_h},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps},"
        f"fade=t=in:st=0:d={fade},fade=t=out:st={max(0.0, duration - fade):.3f}:d={fade},"
        f"format=yuv420p"
    )

    run_ffmpeg(
        [
            "-loop", "1", "-i", str(image_path),
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-r", str(fps),
            "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p",
            str(out_path),
        ]
    )
    return SceneClip(index=0, path=out_path, duration=duration)


def concat_clips(clips: Sequence[SceneClip], out_path: Path) -> Path:
    """Concatenate scene clips into one silent video."""
    list_file = out_path.parent / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{c.path.resolve().as_posix()}'" for c in clips) + "\n",
        encoding="utf-8",
    )
    run_ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy",
            str(out_path),
        ]
    )
    return out_path


# --------------------------------------------------------------------------- #
# Music bed
# --------------------------------------------------------------------------- #
def build_music_bed(out_path: Path, duration: float) -> Path:
    """Synthesise a soft ambient music bed with ffmpeg's own generators.

    No external audio assets are needed: a few detuned sine tones are mixed,
    filtered and faded to produce a calm pad that sits under the voice.
    """
    freqs = [110.0, 164.81, 220.0, 329.63]
    inputs: List[str] = []
    for f in freqs:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=44100:duration={duration:.3f}"]

    chains = []
    for i in range(len(freqs)):
        chains.append(
            f"[{i}:a]volume=0.16,tremolo=f={0.12 + i * 0.05:.3f}:d=0.6,lowpass=f=900[a{i}]"
        )
    mix_inputs = "".join(f"[a{i}]" for i in range(len(freqs)))
    filter_complex = (
        ";".join(chains)
        + f";{mix_inputs}amix=inputs={len(freqs)}:normalize=0[mix]"
        + f";[mix]afade=t=in:st=0:d=1.5,afade=t=out:st={max(0.0, duration - 2.0):.3f}:d=2.0,volume=0.5[out]"
    )

    run_ffmpeg(
        [
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:a", "aac", "-b:a", "128k",
            "-t", f"{duration:.3f}",
            str(out_path),
        ]
    )
    return out_path


# --------------------------------------------------------------------------- #
# Final assembly
# --------------------------------------------------------------------------- #
def assemble(
    silent_video: Path,
    voiceover: Path,
    out_path: Path,
    ass_path: Optional[Path] = None,
    music_path: Optional[Path] = None,
    music_gain: float = 0.16,
    voice_gain: float = 1.0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Mux audio, mix music and burn captions into the final MP4."""
    args: List[str] = ["-i", str(silent_video), "-i", str(voiceover)]
    if music_path is not None:
        args += ["-i", str(music_path)]

    vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    if ass_path is not None:
        vf += f",ass={_escape_filter_path(ass_path)}"
    vf += ",format=yuv420p"

    if music_path is not None:
        filter_complex = (
            f"[1:a]volume={voice_gain}[voice];"
            f"[2:a]volume={music_gain}[music];"
            f"[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        args += [
            "-filter_complex", filter_complex,
            "-map", "0:v:0", "-map", "[aout]",
        ]
    else:
        args += ["-map", "0:v:0", "-map", "1:a:0"]

    args += [
        "-vf", vf,
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
        "-profile:v", "high", "-level", "4.1",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        "-shortest",
        str(out_path),
    ]
    run_ffmpeg(args)
    return out_path


def make_thumbnail(video: Path, out_path: Path, at: float = 1.0) -> Path:
    run_ffmpeg(["-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out_path)])
    return out_path
