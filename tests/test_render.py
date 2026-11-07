"""Render tests. These need ffmpeg and are skipped without it."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import render, visuals

pytestmark = pytest.mark.skipif(
    not render.ffmpeg_available(), reason="ffmpeg/ffprobe are not installed"
)


W, H, FPS = 180, 320, 12


def test_render_scene_clip_produces_playable_video(workdir: Path) -> None:
    bg = visuals.generate_background(workdir / "bg.jpg", W, H, palette="aurora", seed=5)
    clip = render.render_scene_clip(bg, workdir / "clip.mp4", 1.5, W, H, FPS, motion="zoom_in")
    assert clip.path.exists()
    assert clip.path.stat().st_size > 1000
    assert render.probe_duration(clip.path) == pytest.approx(1.5, abs=0.25)


@pytest.mark.parametrize("motion", ["zoom_in", "zoom_out", "pan_up", "pan_down"])
def test_all_motions_render(workdir: Path, motion: str) -> None:
    bg = visuals.generate_background(workdir / f"bg_{motion}.jpg", W, H, palette="ocean", seed=6)
    clip = render.render_scene_clip(bg, workdir / f"clip_{motion}.mp4", 1.0, W, H, FPS, motion=motion)
    assert clip.path.exists() and clip.path.stat().st_size > 1000


def test_concat_and_assemble_with_audio(workdir: Path) -> None:
    from app import captions
    from app.tts import WordTiming

    clips = []
    for i in range(2):
        bg = visuals.generate_background(workdir / f"b{i}.jpg", W, H, palette="ember", seed=i)
        clips.append(render.render_scene_clip(bg, workdir / f"c{i}.mp4", 1.0, W, H, FPS))

    silent = render.concat_clips(clips, workdir / "silent.mp4")
    assert render.probe_duration(silent) == pytest.approx(2.0, abs=0.3)

    # A real tone stands in for the voiceover.
    voice = workdir / "voice.m4a"
    render.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", str(voice)])

    words = [WordTiming(f"w{i}", i * 0.2, i * 0.2 + 0.15) for i in range(10)]
    ass = captions.build_ass(words, workdir / "c.ass", W, H)

    out = render.assemble(silent, voice, workdir / "final.mp4", ass_path=ass, width=W, height=H, fps=FPS)
    assert out.exists() and out.stat().st_size > 2000
    assert render.probe_duration(out) == pytest.approx(2.0, abs=0.4)


def test_music_bed_is_not_silent(workdir: Path) -> None:
    bed = render.build_music_bed(workdir / "music.m4a", 2.0)
    assert bed.exists() and bed.stat().st_size > 1000
    assert render.probe_duration(bed) == pytest.approx(2.0, abs=0.4)


def test_thumbnail(workdir: Path) -> None:
    bg = visuals.generate_background(workdir / "bg.jpg", W, H, palette="mono", seed=9)
    clip = render.render_scene_clip(bg, workdir / "clip.mp4", 1.0, W, H, FPS)
    thumb = render.make_thumbnail(clip.path, workdir / "thumb.jpg", at=0.4)
    assert thumb.exists() and thumb.stat().st_size > 500
