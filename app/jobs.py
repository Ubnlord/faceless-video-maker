"""Job store and background worker.

Jobs are kept in memory and mirrored to a JSON file so a restart does not lose
the library. A single background thread renders one job at a time; the API
returns immediately with a ``202`` and the UI polls for progress.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .config import settings
from .models import JobOut, ScriptSceneOut, VideoRequest
from .pipeline import PipelineResult, run_pipeline

log = logging.getLogger(__name__)


class JobStore:
    """In-memory job registry with a JSON mirror on disk."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._path = data_dir / "jobs.json"
        self._lock = threading.Lock()
        self._jobs: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._jobs = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not load jobs.json: %s", exc)
            self._jobs = {}

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps(self._jobs, indent=2, default=str), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not persist jobs.json: %s", exc)

    def create(self, request: VideoRequest) -> dict:
        now = time.time()
        job = {
            "id": uuid.uuid4().hex[:12],
            "status": "queued",
            "progress": 0,
            "stage": "queued",
            "message": "Queued",
            "error": None,
            "request": request.model_dump(),
            "engine": None,
            "duration": None,
            "scenes": [],
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[job["id"]] = job
            self._save()
        return job

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> List[dict]:
        with self._lock:
            items = sorted(
                self._jobs.values(), key=lambda j: j.get("created_at", 0), reverse=True
            )
        return items[:limit]

    def delete(self, job_id: str) -> bool:
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
            if existed:
                self._save()
        return existed

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.update(fields)
            job["updated_at"] = time.time()
            self._save()

    def to_out(self, job: dict) -> JobOut:
        scenes = [
            ScriptSceneOut(**s) for s in job.get("scenes", []) if isinstance(s, dict)
        ]
        request = job.get("request")
        return JobOut(
            id=job["id"],
            status=job.get("status", "queued"),
            progress=job.get("progress", 0),
            stage=job.get("stage", "queued"),
            message=job.get("message", ""),
            error=job.get("error"),
            request=VideoRequest(**request) if request else None,
            engine=job.get("engine"),
            duration=job.get("duration"),
            scenes=scenes,
            created_at=job.get("created_at", 0.0),
            updated_at=job.get("updated_at", 0.0),
        )


class Worker:
    """Runs renders on a background thread, one at a time."""

    def __init__(self, store: JobStore) -> None:
        self.store = store
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self._next_queued()
            if job is None:
                time.sleep(0.5)
                continue
            self._render(job)

    def _next_queued(self) -> Optional[dict]:
        for job in self.store.list(limit=200):
            if job.get("status") == "queued":
                return job
        return None

    def _render(self, job: dict) -> None:
        job_id = job["id"]
        self.store.update(job_id, status="rendering", stage="script", message="Starting")
        request = VideoRequest(**job["request"])
        work_dir = settings.output_dir / job_id / "work"
        out_dir = settings.output_dir / job_id

        def progress(pct: int, stage: str, message: str) -> None:
            self.store.update(
                job_id, progress=pct, stage=stage, message=message
            )

        try:
            result: PipelineResult = run_pipeline(
                job_id, request, work_dir=work_dir, out_dir=out_dir, progress=progress
            )
            scenes = [
                {
                    "index": i,
                    "narration": b.narration,
                    "caption": b.caption,
                    "start": round(b.start, 3),
                    "end": round(b.start + b.duration, 3),
                }
                for i, b in enumerate(result.beats)
            ]
            self.store.update(
                job_id,
                status="completed",
                progress=100,
                stage="done",
                message="Video ready",
                engine=result.engine,
                duration=round(result.duration, 2),
                scenes=scenes,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("render failed for job %s", job_id)
            self.store.update(
                job_id,
                status="failed",
                stage="error",
                message="Render failed",
                error=str(exc),
            )
