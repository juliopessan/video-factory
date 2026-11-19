"""Fila de revisão local (FastAPI). Só escuta em 127.0.0.1 por padrão."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import MIN_SECONDS, jobs, pipeline
from .media import ClipperError

app = FastAPI(title="Clipper — fila de revisão")
STATIC = Path(__file__).resolve().parent / "static"


class ClipPatch(BaseModel):
    status: str | None = None
    title: str | None = None
    hashtags: str | None = None


class Trim(BaseModel):
    start: float
    end: float


def _guard(fn, *args):
    try:
        return fn(*args)
    except ClipperError as exc:
        raise HTTPException(404 if "não encontrado" in str(exc) else 400, str(exc)) from exc


def _decorate(clip: dict) -> dict:
    length = clip["end"] - clip["start"]
    return {**clip, "length": round(length, 1), "monetizable": length >= MIN_SECONDS - 1}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/jobs")
def api_jobs() -> list[dict]:
    return jobs.list_jobs()


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    manifest = _guard(jobs.read_manifest, job_id)
    return {**manifest, "clips": [_decorate(c) for c in manifest["clips"]]}


@app.patch("/api/jobs/{job_id}/clips/{clip_id}")
def api_patch(job_id: str, clip_id: str, patch: ClipPatch) -> dict:
    changes = patch.model_dump(exclude_none=True)
    if "status" in changes and changes["status"] not in ("pending", "approved", "rejected"):
        raise HTTPException(400, "status inválido")
    if "title" in changes:
        changes["title"] = changes["title"].strip()[:150]
    return _decorate(_guard(jobs.update_clip, job_id, clip_id, changes))


@app.post("/api/jobs/{job_id}/clips/{clip_id}/trim")
def api_trim(job_id: str, clip_id: str, trim: Trim) -> dict:
    return _decorate(_guard(pipeline.rerender, job_id, clip_id, trim.start, trim.end))


@app.post("/api/jobs/{job_id}/export")
def api_export(job_id: str) -> dict:
    return {"path": str(_guard(jobs.export_approved, job_id))}


@app.get("/media/{job_id}/{name}")
def media_file(job_id: str, name: str) -> FileResponse:
    if Path(name).name != name or Path(name).suffix not in (".mp4", ".jpg"):
        raise HTTPException(400, "arquivo inválido")
    path = jobs.job_dir(job_id) / "clips" / name
    if not path.is_file():
        raise HTTPException(404, "arquivo não encontrado")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})
