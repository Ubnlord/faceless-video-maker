#!/usr/bin/env python3
"""End-to-end API test: start the server, render a real video over HTTP, verify it.

    python scripts/api_e2e.py

This is the test that proves the app actually works as a service: it drives the
public REST API exactly as the browser UI does, waits for the background render
to finish, downloads the MP4 and checks it with ffprobe.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
OUT = Path("/tmp/fvm-e2e")


def call(path: str, method: str = "GET", body: dict | None = None, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = resp.read()
        return payload if raw else json.loads(payload)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    print("==> health")
    health = call("/api/health")
    print("   ", json.dumps(health))
    if not health.get("ffmpeg"):
        print("!! ffmpeg missing on the server")
        return 1

    print("==> options / voices / backgrounds")
    opts = call("/api/options")
    voices = call("/api/voices")
    bgs = call("/api/backgrounds")
    print(f"    niches={len(opts['niches'])} voices={len(voices['voices'])} backgrounds={len(bgs['backgrounds'])}")

    print("==> script preview")
    script = call(
        "/api/script",
        "POST",
        {"topic": "why most people never build a daily reading habit", "length": "30 seconds"},
    )
    print(f"    engine={script['engine']} scenes={len(script['scenes'])}")
    print("    " + script["script"].splitlines()[0])

    print("==> creating a video")
    job = call(
        "/api/videos",
        "POST",
        {
            "topic": "why most people never build a daily reading habit",
            "niche": "Motivation",
            "length": "30 seconds",
            "style": "Cinematic",
            "caption_style": "bold",
            "background": "aurora",
            "brand": "FACELESS VIDEO MAKER",
            "music": True,
        },
    )
    job_id = job["id"]
    print(f"    job {job_id} queued")

    print("==> waiting for the render")
    deadline = time.time() + 900
    last = -1
    while time.time() < deadline:
        state = call(f"/api/videos/{job_id}")
        if state["progress"] != last:
            last = state["progress"]
            print(f"    [{state['progress']:3d}%] {state['stage']:<9} {state['message']}")
        if state["status"] == "completed":
            break
        if state["status"] == "failed":
            print("!! render failed:", state.get("error"))
            return 1
        time.sleep(2)
    else:
        print("!! timed out")
        return 1

    print("==> downloading the MP4")
    video_bytes = call(f"/api/videos/{job_id}/video", raw=True)
    video_path = OUT / "video.mp4"
    video_path.write_bytes(video_bytes)
    print(f"    {len(video_bytes) / 1024:.0f} KB -> {video_path}")

    voice_bytes = call(f"/api/videos/{job_id}/voice", raw=True)
    (OUT / "voiceover.mp3").write_bytes(voice_bytes)
    srt = call(f"/api/videos/{job_id}/captions", raw=True)
    (OUT / "captions.srt").write_bytes(srt)
    thumb = call(f"/api/videos/{job_id}/thumbnail", raw=True)
    (OUT / "thumbnail.jpg").write_bytes(thumb)

    print("==> verifying with ffprobe")
    info = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(video_path)],
            capture_output=True, text=True, check=True,
        ).stdout
    )
    streams = info["streams"]
    v = next(s for s in streams if s["codec_type"] == "video")
    a = next(s for s in streams if s["codec_type"] == "audio")
    dur = float(info["format"]["duration"])
    print(f"    video : {v['codec_name']} {v['width']}x{v['height']} {v['r_frame_rate']}")
    print(f"    audio : {a['codec_name']} {a.get('sample_rate')}Hz {a.get('channels')}ch")
    print(f"    length: {dur:.2f}s")

    vol = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video_path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    mean = next(
        (float(l.split("mean_volume:")[1].split("dB")[0]) for l in vol.stderr.splitlines() if "mean_volume" in l),
        None,
    )
    print(f"    mean vol: {mean} dB")

    print("==> library listing")
    lib = call("/api/videos")
    print(f"    {len(lib['videos'])} job(s) recorded")

    failures = []
    if v["codec_name"] != "h264":
        failures.append("video codec is not h264")
    if (v["width"], v["height"]) != (1080, 1920):
        failures.append(f"unexpected resolution {v['width']}x{v['height']}")
    if a["codec_name"] != "aac":
        failures.append("audio codec is not aac")
    if dur < 10:
        failures.append(f"video too short ({dur:.1f}s)")
    if mean is None or mean < -60:
        failures.append(f"audio is silent ({mean} dB)")
    if len(video_bytes) < 200_000:
        failures.append(f"video too small ({len(video_bytes)} bytes)")
    if not srt.strip():
        failures.append("captions are empty")

    if failures:
        print("\n!! E2E FAILED")
        for f in failures:
            print("   -", f)
        return 1

    print("\n==> E2E PASSED")
    print(f"    artifacts in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
