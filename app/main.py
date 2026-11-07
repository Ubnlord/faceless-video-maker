"""FastAPI application: REST API + the single-page UI.

The API is the contract the browser UI talks to. Rendering happens on a
background worker thread; every endpoint returns immediately and the UI polls
``GET /api/videos/{id}`` for progress.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, render, scriptgen, tts, visuals
from .config import settings
from .jobs import JobStore, Worker
from .models import (
    BackgroundsOut,
    HealthOut,
    JobListOut,
    JobOut,
    OptionsOut,
    ScriptOut,
    ScriptRequest,
    ScriptSceneOut,
    VideoRequest,
    VoicesOut,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Faceless Video Maker", version=__version__)

store = JobStore(settings.data_dir)
worker = Worker(store)
worker.start()

NICHES = ["Motivation", "Finance", "Health", "Productivity", "History", "Science", "Business", "Self-Improvement"]
LENGTHS = ["30 seconds", "60 seconds", "90 seconds", "3 minutes"]
STYLES = ["Cinematic", "Minimal", "Bold", "Warm"]
CAPTION_STYLES = ["bold", "minimal", "boxed", "neon"]
ASPECTS = ["9:16", "1:1", "16:9"]


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return Path("index.html").read_text(encoding="utf-8")


@app.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(
        status="ok",
        ffmpeg=render.ffmpeg_available(),
        llm=settings.llm_enabled,
        llm_model=settings.openai_model,
        engine="llm" if settings.llm_enabled else "offline",
        resolution=settings.resolution,
        version=__version__,
    )


@app.get("/api/options", response_model=OptionsOut)
def options() -> OptionsOut:
    return OptionsOut(
        niches=NICHES,
        lengths=LENGTHS,
        styles=STYLES,
        caption_styles=CAPTION_STYLES,
        aspects=ASPECTS,
    )


@app.get("/api/voices", response_model=VoicesOut)
def voices() -> VoicesOut:
    return VoicesOut(voices=tts.list_voices(), default=settings.default_voice)


@app.get("/api/backgrounds", response_model=BackgroundsOut)
def backgrounds() -> BackgroundsOut:
    return BackgroundsOut(backgrounds=visuals.BACKGROUND_NAMES, default="aurora")


@app.post("/api/script", response_model=ScriptOut)
def preview_script(req: ScriptRequest) -> ScriptOut:
    if req.script and req.script.strip():
        script = scriptgen.script_from_text(req.script, req.length)
    else:
        script = scriptgen.generate_script(req.topic, req.niche, req.length, req.style)
    scenes = [
        ScriptSceneOut(index=i, narration=s.narration, caption=s.caption)
        for i, s in enumerate(script.scenes)
    ]
    return ScriptOut(
        title=script.title,
        hook=script.hook,
        scenes=scenes,
        cta=script.cta,
        engine=script.engine,
        script=script.to_text(),
    )


@app.post("/api/videos", response_model=JobOut, status_code=202)
def create_video(req: VideoRequest) -> JobOut:
    job = store.create(req)
    return store.to_out(job)


@app.get("/api/videos", response_model=JobListOut)
def list_videos(limit: int = Query(50, ge=1, le=200)) -> JobListOut:
    return JobListOut(videos=[store.to_out(j) for j in store.list(limit)])


@app.get("/api/videos/{job_id}", response_model=JobOut)
def get_video(job_id: str) -> JobOut:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return store.to_out(job)


@app.delete("/api/videos/{job_id}", status_code=204)
def delete_video(job_id: str) -> None:
    if not store.delete(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    _remove_job_files(job_id)


def _remove_job_files(job_id: str) -> None:
    import shutil

    job_dir = settings.output_dir / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)


def _media_file(job_id: str, name: str) -> Path:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    path = settings.output_dir / job_id / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return path


@app.get("/api/videos/{job_id}/video")
def download_video(job_id: str) -> FileResponse:
    return FileResponse(_media_file(job_id, "video.mp4"), media_type="video/mp4", filename="video.mp4")


@app.get("/api/videos/{job_id}/voice")
def download_voice(job_id: str) -> FileResponse:
    return FileResponse(_media_file(job_id, "voiceover.mp3"), media_type="audio/mpeg", filename="voiceover.mp3")


@app.get("/api/videos/{job_id}/captions")
def download_captions(job_id: str) -> FileResponse:
    return FileResponse(_media_file(job_id, "captions.srt"), media_type="text/plain", filename="captions.srt")


@app.get("/api/videos/{job_id}/thumbnail")
def download_thumbnail(job_id: str) -> FileResponse:
    return FileResponse(_media_file(job_id, "thumbnail.jpg"), media_type="image/jpeg", filename="thumbnail.jpg")


# Serve the rendered media so the <video> element can stream it.
app.mount("/media", StaticFiles(directory=settings.output_dir), name="media")
