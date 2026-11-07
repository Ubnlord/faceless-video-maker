"""The end-to-end generation pipeline.

    script  ->  per-beat voiceover  ->  word timings  ->  captions
            ->  procedural backgrounds  ->  animated clips
            ->  concat  ->  music bed  ->  final MP4

Each narration beat is synthesised on its own, which makes every scene's
duration exact and keeps the burned-in captions perfectly in sync with the
spoken audio - no guessing, no proportional fudging.
"""
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from . import captions, render, scriptgen, tts, visuals
from .config import settings
from .scriptgen import Script

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, str, str], None]

BEAT_GAP = 0.28
BEAT_PAUSE = 0.35
MOTIONS = ["zoom_in", "pan_up", "zoom_out", "pan_down"]


@dataclass
class Beat:
    index: int
    narration: str
    caption: str
    audio: Optional[Path] = None
    duration: float = 0.0
    words: List[tts.WordTiming] = field(default_factory=list)
    start: float = 0.0
    background: Optional[Path] = None
    palette: str = "aurora"
    motion: str = "zoom_in"


@dataclass
class PipelineResult:
    video: Path
    voice: Path
    srt: Optional[Path]
    thumbnail: Optional[Path]
    duration: float
    script: Script
    beats: List[Beat]
    engine: str


def build_beats(script: Script) -> List[Beat]:
    """Turn a script into the ordered list of narration beats."""
    beats: List[Beat] = []
    if script.hook.strip():
        beats.append(Beat(0, script.hook.strip(), scriptgen._caption_from(script.hook)))
    for scene in script.scenes:
        if scene.narration.strip():
            beats.append(Beat(len(beats), scene.narration.strip(), scene.caption.strip()))
    if script.cta.strip():
        beats.append(Beat(len(beats), script.cta.strip(), scriptgen._caption_from(script.cta)))
    if not beats:
        raise ValueError("script produced no narration beats")
    return beats


def _silent_beat(path: Path, seconds: float) -> Path:
    """A silent placeholder so one failed beat cannot kill the whole render."""
    render.run_ffmpeg(
        [
            "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", f"{seconds:.3f}",
            "-c:a", "libmp3lame", "-b:a", "96k",
            str(path),
        ]
    )
    return path


def _concat_audio(parts: List[Path], out_path: Path, gap: float = BEAT_GAP, sample_rate: int = 24000) -> Path:
    """Join the beat recordings, inserting a short pause between them."""
    if not parts:
        raise ValueError("no audio parts to concatenate")
    if len(parts) == 1:
        shutil.copy(parts[0], out_path)
        return out_path

    silence = out_path.parent / "_silence.mp3"
    _silent_beat(silence, gap)

    args: List[str] = []
    for part in parts:
        args += ["-i", str(part)]
        args += ["-i", str(silence)]
    n_inputs = len(parts) * 2 - 1  # trailing silence is dropped

    chains = ";".join(
        f"[{i}:a]aformat=sample_rates={sample_rate}:channel_layouts=mono[a{i}]" for i in range(n_inputs)
    )
    labels = "".join(f"[a{i}]" for i in range(n_inputs))
    filter_complex = f"{chains};{labels}concat=n={n_inputs}:v=0:a=1[out]"

    args += [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(out_path),
    ]
    render.run_ffmpeg(args)
    return out_path


def run_pipeline(
    job_id: str,
    request,
    work_dir: Path,
    out_dir: Path,
    progress: Optional[ProgressFn] = None,
) -> PipelineResult:
    """Generate one complete video. Returns the paths of everything produced."""

    def report(pct: int, stage: str, message: str = "") -> None:
        if progress:
            progress(pct, stage, message)

    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 1. script ---------------------------------------------------------
    report(4, "script", "Writing the script")
    if request.script and request.script.strip():
        script = scriptgen.script_from_text(request.script, request.length)
    else:
        script = scriptgen.generate_script(
            request.topic, request.niche, request.length, request.style
        )

    beats = build_beats(script)
    n = len(beats)
    log.info("job %s: %d beats via %s", job_id, n, script.engine)

    # --- 2. voiceover ------------------------------------------------------
    voice = request.voice or settings.default_voice
    report(12, "voice", f"Recording {n} narration beats")
    for i, beat in enumerate(beats):
        audio = work_dir / f"beat_{i:02d}.mp3"
        if i:
            # A short pause between requests keeps the speech service happy.
            time.sleep(BEAT_PAUSE)
        try:
            result = tts.synthesise(beat.narration, audio, voice=voice)
            beat.audio = result.audio_path
            beat.duration = result.duration
            beat.words = result.words
        except Exception as exc:  # noqa: BLE001
            log.warning("beat %d synthesis failed (%s); using silence", i, exc)
            estimate = max(2.0, len(beat.narration.split()) / 2.6)
            beat.audio = _silent_beat(audio, estimate)
            beat.duration = estimate
            beat.words = []
        report(12 + int(28 * (i + 1) / n), "voice", f"Beat {i + 1}/{n} recorded")

    report(42, "voice", "Assembling the voiceover")
    voice_path = out_dir / "voiceover.mp3"
    _concat_audio([b.audio for b in beats if b.audio], voice_path)
    total_voice = render.probe_duration(voice_path)

    # Exact offsets: beat i starts after every previous beat plus its pause.
    cursor = 0.0
    for i, beat in enumerate(beats):
        beat.start = cursor
        cursor += beat.duration + (BEAT_GAP if i < n - 1 else 0.0)
    computed_total = cursor

    # Guard against mp3 frame padding drift so captions stay in sync.
    drift = (total_voice / computed_total) if computed_total > 0 else 1.0
    if 0.9 < drift < 1.1 and abs(drift - 1.0) > 0.002:
        for beat in beats:
            beat.start *= drift

    # --- 3. captions -------------------------------------------------------
    report(50, "captions", "Timing the captions")
    all_words: List[tts.WordTiming] = []
    for beat in beats:
        for word in beat.words:
            all_words.append(
                tts.WordTiming(
                    text=word.text,
                    start=word.start + beat.start,
                    end=word.end + beat.start,
                )
            )

    ass_path = work_dir / "captions.ass"
    captions.build_ass(
        all_words,
        ass_path,
        settings.width,
        settings.height,
        style=request.caption_style,
        brand=request.brand or "",
        brand_until=2.5,
    )
    srt_path = out_dir / "captions.srt"
    captions.build_srt(all_words, srt_path)

    # --- 4. backgrounds ----------------------------------------------------
    report(56, "visuals", "Generating backgrounds")
    seed = abs(hash(job_id)) % 100_000
    for i, beat in enumerate(beats):
        palette = visuals.palette_for_index(request.background, i)
        background = work_dir / f"bg_{i:02d}.jpg"
        visuals.generate_background(
            background, settings.width, settings.height, palette=palette, seed=seed, variant=i
        )
        beat.background = background
        beat.palette = palette
        beat.motion = MOTIONS[i % len(MOTIONS)]
        report(56 + int(14 * (i + 1) / n), "visuals", f"Background {i + 1}/{n}")

    # --- 5. animated scene clips -------------------------------------------
    report(72, "render", "Animating the scenes")
    clips: List[render.SceneClip] = []
    for i, beat in enumerate(beats):
        duration = beat.duration + (BEAT_GAP if i < n - 1 else 0.0)
        clip_path = work_dir / f"clip_{i:02d}.mp4"
        render.render_scene_clip(
            beat.background,
            clip_path,
            duration,
            settings.width,
            settings.height,
            settings.fps,
            motion=beat.motion,
        )
        clips.append(render.SceneClip(index=i, path=clip_path, duration=duration))
        report(72 + int(12 * (i + 1) / n), "render", f"Scene {i + 1}/{n} animated")

    silent_video = work_dir / "silent.mp4"
    render.concat_clips(clips, silent_video)

    # --- 6. music bed ------------------------------------------------------
    music_path: Optional[Path] = None
    if request.music:
        report(86, "audio", "Composing the music bed")
        candidate = work_dir / "music.m4a"
        try:
            render.build_music_bed(candidate, total_voice)
            music_path = candidate
        except Exception as exc:  # noqa: BLE001
            log.warning("music bed failed (%s); continuing without it", exc)
            music_path = None

    # --- 7. final encode ---------------------------------------------------
    report(90, "encode", "Encoding the final MP4")
    video_path = out_dir / "video.mp4"
    render.assemble(
        silent_video,
        voice_path,
        video_path,
        ass_path=ass_path,
        music_path=music_path,
        width=settings.width,
        height=settings.height,
        fps=settings.fps,
    )
    duration = render.probe_duration(video_path)

    thumbnail: Optional[Path] = None
    try:
        thumbnail = render.make_thumbnail(video_path, out_dir / "thumbnail.jpg", at=min(1.5, duration / 3))
    except Exception as exc:  # noqa: BLE001
        log.warning("thumbnail failed: %s", exc)

    report(100, "done", "Video ready")

    return PipelineResult(
        video=video_path,
        voice=voice_path,
        srt=srt_path,
        thumbnail=thumbnail,
        duration=duration,
        script=script,
        beats=beats,
        engine=script.engine,
    )
