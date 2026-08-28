"""API local do Video Factory (FastAPI) + entrega da interface web."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, pipeline as pipeline_mod, studio, textgen
from .config import (
    ASPECT_RATIOS,
    CLIP_SECONDS,
    COST_UNITS_PER_SECOND,
    EXTENSION_SECONDS,
    MAX_CUMULATIVE_SECONDS,
    MAX_REFERENCE_VIDEOS,
    MAX_REFERENCE_VIDEO_SECONDS,
    RESOLUTIONS,
    settings,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Video Factory", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


# --------------------------------------------------------------------------- schemas


class MediaRef(BaseModel):
    asset_id: str
    kind: str = "image"
    role: str = "reference"


class ProjectIn(BaseModel):
    name: str = ""


class GenerationIn(BaseModel):
    prompt: str = ""
    mode: str = "text_to_video"
    resolution: str = "720p"
    aspect_ratio: str = "16:9"
    duration_seconds: int = CLIP_SECONDS
    parent_id: str | None = None
    media: list[MediaRef] = Field(default_factory=list)
    seed: int | None = None


class PipelineIn(BaseModel):
    brand: str = ""
    product: str = ""
    audience: str = ""
    problem: str = ""
    turning_point: str = ""
    value: str = ""
    cta: str = ""
    characters: str = ""
    aesthetic: str = ""
    reference_note: str = ""
    reference_asset_id: str | None = None
    aspect_ratio: str = "16:9"
    resolution: str = "720p"
    duration_seconds: int = 30


class PipelineUpdateIn(BaseModel):
    story: dict | None = None
    storyboard: dict | None = None


class PipelineRenderIn(BaseModel):
    resolution: str | None = None


class DraftBatchIn(BaseModel):
    prompts: list[str]
    aspect_ratio: str = "16:9"
    duration_seconds: int = CLIP_SECONDS
    resolution: str = "360p"


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except studio.StudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------- rotas


@app.get("/api/config")
def read_config() -> dict:
    return {
        "provider": settings.effective_provider,
        "model": settings.model,
        "has_api_key": bool(settings.api_key),
        "modes": list(studio.MODES),
        "resolutions": list(RESOLUTIONS),
        "aspect_ratios": list(ASPECT_RATIOS),
        "clip_seconds": CLIP_SECONDS,
        "extension_seconds": EXTENSION_SECONDS,
        "max_cumulative_seconds": MAX_CUMULATIVE_SECONDS,
        "max_reference_videos": MAX_REFERENCE_VIDEOS,
        "max_reference_video_seconds": MAX_REFERENCE_VIDEO_SECONDS,
        "cost_units_per_second": COST_UNITS_PER_SECOND,
        "segment_seconds": pipeline_mod.SEGMENT_SECONDS,
        "text_model": textgen.TEXT_MODEL,
        "text_available": textgen.available(),
    }


@app.get("/api/projects")
def get_projects() -> list[dict]:
    projects = studio.list_projects()
    if not projects:
        studio.ensure_default_project()
        projects = studio.list_projects()
    return projects


@app.post("/api/projects", status_code=201)
def post_project(payload: ProjectIn) -> dict:
    return studio.create_project(payload.name)


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    _guard(studio.delete_project, project_id)


@app.get("/api/projects/{project_id}/generations")
def get_generations(project_id: str) -> dict:
    _guard(studio.get_project, project_id)
    return {
        "generations": studio.list_generations(project_id),
        "stats": studio.project_stats(project_id),
    }


@app.post("/api/projects/{project_id}/generations", status_code=202)
def post_generation(project_id: str, payload: GenerationIn) -> dict:
    return _guard(
        studio.create_generation,
        project_id=project_id,
        prompt=payload.prompt,
        mode=payload.mode,
        resolution=payload.resolution,
        aspect_ratio=payload.aspect_ratio,
        duration_seconds=payload.duration_seconds,
        parent_id=payload.parent_id,
        media=[m.model_dump() for m in payload.media],
        seed=payload.seed,
    )


@app.post("/api/projects/{project_id}/draft-batches", status_code=202)
def post_draft_batch(project_id: str, payload: DraftBatchIn) -> list[dict]:
    return _guard(
        studio.create_draft_batch,
        project_id=project_id,
        prompts=payload.prompts,
        aspect_ratio=payload.aspect_ratio,
        duration_seconds=payload.duration_seconds,
        resolution=payload.resolution,
    )


# ---- pipeline: contexto -> storytelling -> storyboard -> resultado final


@app.get("/api/projects/{project_id}/pipelines")
def get_pipelines(project_id: str) -> list[dict]:
    _guard(studio.get_project, project_id)
    return pipeline_mod.list_pipelines(project_id)


@app.post("/api/projects/{project_id}/pipelines", status_code=201)
def post_pipeline(project_id: str, payload: PipelineIn) -> dict:
    return _guard(pipeline_mod.create_pipeline, project_id, payload.model_dump())


@app.get("/api/pipelines/{pipeline_id}")
def get_pipeline(pipeline_id: str) -> dict:
    return _guard(pipeline_mod.get_pipeline, pipeline_id)


@app.patch("/api/pipelines/{pipeline_id}")
def patch_pipeline(pipeline_id: str, payload: PipelineUpdateIn) -> dict:
    return _guard(pipeline_mod.update_pipeline, pipeline_id, payload.story, payload.storyboard)


@app.post("/api/pipelines/{pipeline_id}/prompts")
def post_pipeline_prompts(pipeline_id: str) -> dict:
    return _guard(pipeline_mod.regenerate_prompts, pipeline_id)


@app.post("/api/pipelines/{pipeline_id}/render", status_code=202)
def post_pipeline_render(pipeline_id: str, payload: PipelineRenderIn) -> dict:
    return _guard(pipeline_mod.render, pipeline_id, payload.resolution)


@app.delete("/api/pipelines/{pipeline_id}", status_code=204)
def delete_pipeline(pipeline_id: str) -> None:
    _guard(pipeline_mod.delete_pipeline, pipeline_id)


@app.get("/api/generations/{generation_id}")
def get_generation(generation_id: str) -> dict:
    return _guard(studio.get_generation, generation_id)


@app.get("/api/generations/{generation_id}/chain")
def get_chain(generation_id: str) -> list[dict]:
    generation = _guard(studio.get_generation, generation_id)
    return studio.get_chain(generation["root_id"])


@app.get("/api/generations/{generation_id}/media")
def get_media(generation_id: str):
    generation = _guard(studio.get_generation, generation_id)
    path = generation.get("asset_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Midia ainda nao disponivel.")
    return FileResponse(path, media_type=generation["mime_type"] or "video/mp4")


@app.post("/api/projects/{project_id}/assets", status_code=201)
async def post_asset(
    project_id: str,
    file: UploadFile = File(...),
    kind: str = Form("image"),
) -> dict:
    _guard(studio.get_project, project_id)
    if kind not in ("image", "video"):
        raise HTTPException(status_code=400, detail="kind deve ser 'image' ou 'video'.")
    content = await file.read()
    return _guard(studio.save_upload, project_id, file.filename or "upload", content, kind)


@app.get("/api/projects/{project_id}/assets")
def get_assets(project_id: str) -> list[dict]:
    _guard(studio.get_project, project_id)
    return studio.list_assets(project_id)


@app.get("/api/assets/{asset_id}/file")
def get_asset_file(asset_id: str):
    asset = _guard(studio.get_asset, asset_id)
    if not Path(asset["path"]).exists():
        raise HTTPException(status_code=404, detail="Arquivo ausente no disco.")
    return FileResponse(asset["path"], media_type=asset["mime_type"])


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/app/")


app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="web")
