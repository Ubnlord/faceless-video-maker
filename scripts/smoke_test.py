#!/usr/bin/env python3
"""End-to-end smoke test: generate one real video and verify it with ffprobe.

Run from the project root:

    python scripts/smoke_test.py

It renders a short video at a reduced resolution so it finishes quickly, then
asserts that the output really is a playable MP4 with a video stream, an audio
stream and a non-trivial duration.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Keep the smoke test fast and self-contained.
os.environ.setdefault("FVM_WIDTH", "540")
os.environ.setdefault("FVM_HEIGHT", "960")
os.environ.setdefault("FVM_FPS", "24")
os.environ.setdefault("FVM_PRESET", "ultrafast")

from app.models import VideoRequest  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402


def probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fvm-smoke-"))
    work = tmp / "work"
    out = tmp / "out"

    request = VideoRequest(
        topic="5 habits that completely change your life",
        niche="Motivation",
        length="30 seconds",
        style="Cinematic",
        caption_style="bold",
        background="aurora",
        music=True,
    )

    stages: list[str] = []

    def progress(pct: int, stage: str, message: str) -> None:
        if not stages or stages[-1] != stage:
            stages.append(stage)
            print(f"  [{pct:3d}%] {stage:<9} {message}")

    print("==> running pipeline")
    result = run_pipeline("smoke", request, work_dir=work, out_dir=out, progress=progress)

    print(f"==> video: {result.video}")
    info = probe(result.video)
    streams = info["streams"]
    kinds = {s["codec_type"] for s in streams}
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next(s for s in streams if s["codec_type"] == "audio")
    duration = float(info["format"]["duration"])
    size = result.video.stat().st_size

    print(f"    container : {info['format']['format_name']}")
    print(f"    video     : {video['codec_name']} {video['width']}x{video['height']} @ {video['r_frame_rate']}")
    print(f"    audio     : {audio['codec_name']} {audio.get('sample_rate')}Hz {audio.get('channels')}ch")
    print(f"    duration  : {duration:.2f}s")
    print(f"    size      : {size / 1024:.0f} KB")
    print(f"    scenes    : {len(result.beats)}")
    print(f"    engine    : {result.engine}")
    print(f"    voiceover : {result.voice} ({result.voice.stat().st_size / 1024:.0f} KB)")
    print(f"    captions  : {result.srt}")
    print(f"    thumbnail : {result.thumbnail}")

    failures = []
    if "video" not in kinds:
        failures.append("no video stream")
    if "audio" not in kinds:
        failures.append("no audio stream")
    if video["codec_name"] != "h264":
        failures.append(f"unexpected video codec {video['codec_name']}")
    if audio["codec_name"] != "aac":
        failures.append(f"unexpected audio codec {audio['codec_name']}")
    if duration < 5:
        failures.append(f"video too short ({duration:.2f}s)")
    if size < 50_000:
        failures.append(f"video suspiciously small ({size} bytes)")
    if not result.srt.exists() or result.srt.stat().st_size == 0:
        failures.append("no captions written")
    if not result.voice.exists() or result.voice.stat().st_size == 0:
        failures.append("no voiceover written")

    # The audio must not be silent: measure its mean volume.
    vol = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(result.video), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    mean = None
    for line in vol.stderr.splitlines():
        if "mean_volume" in line:
            mean = float(line.split("mean_volume:")[1].split("dB")[0].strip())
    print(f"    mean vol  : {mean} dB")
    if mean is None or mean < -60:
        failures.append(f"audio appears silent (mean {mean} dB)")

    if failures:
        print("\n!! SMOKE TEST FAILED")
        for f in failures:
            print(f"   - {f}")
        return 1

    print("\n==> SMOKE TEST PASSED")
    print(f"    output kept at {result.video}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
