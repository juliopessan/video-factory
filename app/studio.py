"""Regras de dominio e execucao das geracoes."""
from __future__ import annotations

import json
import mimetypes
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db
from .mediainfo import probe_duration_seconds
from .config import (
    ASPECT_RATIOS,
    MAX_REFERENCE_VIDEO_SECONDS,
    CLIP_SECONDS,
    COST_UNITS_PER_SECOND,
    EXTENSION_SECONDS,
    MAX_CUMULATIVE_SECONDS,
    MAX_REFERENCE_VIDEOS,
    RESOLUTIONS,
    settings,
)
from .providers import MediaInput, ProviderError, VideoRequest, get_provider

EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="vf-job")

MODES = ("text_to_video", "image_to_video", "interpolate", "reference_to_video", "extend", "upscale")
UPSCALE_RESOLUTIONS = ("1080p", "4k")


class StudioError(ValueError):
    """Erro de validacao de dominio (vira HTTP 400)."""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def cost_units(resolution: str, seconds: int) -> float:
    return round(COST_UNITS_PER_SECOND.get(resolution, 3.0) * seconds, 2)


# --------------------------------------------------------------------------- projetos


def create_project(name: str) -> dict:
    name = (name or "").strip() or "Projeto sem titulo"
    project = {"id": new_id("prj"), "name": name, "created_at": db.now()}
    db.insert("projects", project)
    return project


def list_projects() -> list[dict]:
    return db.query(
        """
        SELECT p.*, (SELECT COUNT(*) FROM generations g WHERE g.project_id = p.id) AS generation_count
        FROM projects p ORDER BY p.created_at DESC
        """
    )


def get_project(project_id: str) -> dict:
    project = db.query_one("SELECT * FROM projects WHERE id = ?", [project_id])
    if not project:
        raise StudioError(f"Projeto {project_id} nao encontrado.")
    return project


def delete_project(project_id: str) -> None:
    get_project(project_id)
    db.execute("DELETE FROM generations WHERE project_id = ?", [project_id])
    db.execute("DELETE FROM assets WHERE project_id = ?", [project_id])
    db.execute("DELETE FROM projects WHERE id = ?", [project_id])


def ensure_default_project() -> dict:
    projects = list_projects()
    return projects[0] if projects else create_project("Primeiro projeto")


# --------------------------------------------------------------------------- assets


def save_upload(project_id: str | None, filename: str, content: bytes, kind: str) -> dict:
    if not content:
        raise StudioError("Arquivo vazio.")
    suffix = Path(filename).suffix.lower() or (".png" if kind == "image" else ".mp4")
    asset_id = new_id("ast")
    path = settings.uploads_dir / f"{asset_id}{suffix}"
    path.write_bytes(content)
    duration = probe_duration_seconds(path) if kind == "video" else None
    asset = {
        "id": asset_id,
        "project_id": project_id,
        "kind": kind,
        "filename": filename,
        "path": str(path),
        "mime_type": mimetypes.guess_type(filename)[0] or ("image/png" if kind == "image" else "video/mp4"),
        "size": len(content),
        "duration_seconds": round(duration, 3) if duration else None,
        "created_at": db.now(),
    }
    db.insert("assets", asset)
    return asset


def get_asset(asset_id: str) -> dict:
    asset = db.query_one("SELECT * FROM assets WHERE id = ?", [asset_id])
    if not asset:
        raise StudioError(f"Asset {asset_id} nao encontrado.")
    return asset


def list_assets(project_id: str) -> list[dict]:
    return db.query(
        "SELECT * FROM assets WHERE project_id = ? ORDER BY created_at DESC", [project_id]
    )


# --------------------------------------------------------------------------- geracoes


def get_generation(generation_id: str) -> dict:
    row = db.query_one("SELECT * FROM generations WHERE id = ?", [generation_id])
    if not row:
        raise StudioError(f"Geracao {generation_id} nao encontrada.")
    return db.loads_meta(row)


def list_generations(project_id: str) -> list[dict]:
    rows = db.query(
        "SELECT * FROM generations WHERE project_id = ? ORDER BY created_at DESC", [project_id]
    )
    return [db.loads_meta(r) for r in rows]


def get_chain(root_id: str) -> list[dict]:
    """Cena completa: clipe raiz + extensoes, em ordem cronologica."""
    rows = db.query(
        "SELECT * FROM generations WHERE root_id = ? ORDER BY created_at ASC", [root_id]
    )
    return [db.loads_meta(r) for r in rows]


def _validate_common(mode: str, resolution: str, aspect_ratio: str) -> None:
    if mode not in MODES:
        raise StudioError(f"Modo invalido: {mode}. Use um de {', '.join(MODES)}.")
    if resolution not in RESOLUTIONS:
        raise StudioError(f"Resolucao invalida: {resolution}. Use uma de {', '.join(RESOLUTIONS)}.")
    if aspect_ratio not in ASPECT_RATIOS:
        raise StudioError(f"Aspect ratio invalido: {aspect_ratio}.")


def _validate_reference_videos(videos: list[dict]) -> None:
    """Referencias de video sao limitadas a 3 segundos.

    A duracao vem do cabecalho do arquivo (MP4/MOV/WebM). Contêiner que nao
    sabemos ler devolve `None`: nesse caso o limite nao e aplicado, para nao
    recusar um arquivo valido so porque nao conseguimos medi-lo.
    """
    for ref in videos:
        asset = get_asset(ref["asset_id"])
        duration = asset.get("duration_seconds")
        if duration and duration > MAX_REFERENCE_VIDEO_SECONDS + 0.05:
            raise StudioError(
                f"'{asset['filename']}' tem {duration:.1f}s; referencias de video "
                f"aceitam ate {MAX_REFERENCE_VIDEO_SECONDS}s."
            )


def _validate_media(mode: str, media_refs: list[dict]) -> None:
    images = [m for m in media_refs if m["kind"] == "image"]
    videos = [m for m in media_refs if m["kind"] == "video"]
    if mode == "image_to_video" and len(images) != 1:
        raise StudioError("image_to_video exige exatamente 1 imagem (frame inicial).")
    if mode == "interpolate":
        roles = {m.get("role") for m in images}
        if len(images) != 2 or roles != {"first_frame", "last_frame"}:
            raise StudioError("interpolate exige 2 imagens, com papeis first_frame e last_frame.")
    if mode == "reference_to_video":
        if not media_refs:
            raise StudioError("reference_to_video exige ao menos 1 referencia.")
        if len(videos) > MAX_REFERENCE_VIDEOS:
            raise StudioError(f"Maximo de {MAX_REFERENCE_VIDEOS} referencias de video.")
        _validate_reference_videos(videos)


def _parent_for(mode: str, parent_id: str | None) -> dict | None:
    if mode not in ("extend", "upscale"):
        return None
    if not parent_id:
        raise StudioError(f"O modo {mode} exige parent_id.")
    parent = get_generation(parent_id)
    if parent["status"] != "completed":
        raise StudioError("O clipe de origem ainda nao esta pronto.")
    if not parent.get("interaction_id"):
        raise StudioError("O clipe de origem nao tem interaction_id para continuar a cena.")
    return parent


def create_generation(
    project_id: str,
    prompt: str,
    mode: str = "text_to_video",
    resolution: str = "720p",
    aspect_ratio: str = "16:9",
    duration_seconds: int = CLIP_SECONDS,
    parent_id: str | None = None,
    media: list[dict] | None = None,
    batch_id: str | None = None,
    label: str | None = None,
    seed: int | None = None,
    enqueue: bool = True,
) -> dict:
    """Valida, grava a geracao como `queued` e enfileira o job.

    Com `enqueue=False` a linha e criada mas nao entra na fila: quem chamou
    executa `run_generation` na ordem que precisar (usado pelo pipeline, onde
    cada extensao so pode comecar depois que a peca anterior termina).
    """
    get_project(project_id)
    media_refs = list(media or [])
    prompt = (prompt or "").strip()
    _validate_common(mode, resolution, aspect_ratio)
    _validate_media(mode, media_refs)
    for ref in media_refs:
        get_asset(ref["asset_id"])

    parent = _parent_for(mode, parent_id)
    root_id = None
    cumulative = duration_seconds

    if mode == "extend":
        duration_seconds = EXTENSION_SECONDS
        cumulative = parent["cumulative_seconds"] + EXTENSION_SECONDS
        if cumulative > MAX_CUMULATIVE_SECONDS:
            raise StudioError(
                f"A cena chegaria a {cumulative}s; o limite cumulativo e {MAX_CUMULATIVE_SECONDS}s."
            )
        root_id = parent["root_id"]
        resolution = resolution or parent["resolution"]
        aspect_ratio = parent["aspect_ratio"]
    elif mode == "upscale":
        if resolution not in UPSCALE_RESOLUTIONS:
            raise StudioError(f"Upscale aceita apenas {' ou '.join(UPSCALE_RESOLUTIONS)}.")
        duration_seconds = parent["duration_seconds"]
        cumulative = parent["cumulative_seconds"]
        root_id = parent["root_id"]
        aspect_ratio = parent["aspect_ratio"]
    elif not prompt:
        raise StudioError("O prompt e obrigatorio.")

    if duration_seconds <= 0:
        raise StudioError("duration_seconds deve ser positivo.")

    generation_id = new_id("gen")
    row = {
        "id": generation_id,
        "project_id": project_id,
        "parent_id": parent["id"] if parent else None,
        "root_id": root_id or generation_id,
        "batch_id": batch_id,
        "label": label,
        "mode": mode,
        "prompt": prompt,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "duration_seconds": duration_seconds,
        "cumulative_seconds": cumulative,
        "cost_units": cost_units(resolution, duration_seconds),
        "status": "queued",
        "provider": settings.effective_provider,
        "interaction_id": None,
        "error": None,
        "asset_path": None,
        "mime_type": None,
        "meta": json.dumps({"media": media_refs, "seed": seed}),
        "created_at": db.now(),
        "updated_at": db.now(),
    }
    db.insert("generations", row)
    if enqueue:
        EXECUTOR.submit(run_generation, generation_id)
    return db.loads_meta(row)


def create_draft_batch(
    project_id: str,
    prompts: list[str],
    aspect_ratio: str = "16:9",
    duration_seconds: int = CLIP_SECONDS,
    resolution: str = "360p",
) -> list[dict]:
    """Draft Room: varia um prompt por vez e compara lado a lado, barato, em 360p."""
    prompts = [p.strip() for p in prompts if p and p.strip()]
    if not prompts:
        raise StudioError("Informe ao menos uma variacao de prompt.")
    if len(prompts) > 6:
        raise StudioError("Maximo de 6 variacoes por lote.")
    batch_id = new_id("batch")
    return [
        create_generation(
            project_id=project_id,
            prompt=prompt,
            mode="text_to_video",
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            batch_id=batch_id,
            label=f"Variação {index + 1}",
        )
        for index, prompt in enumerate(prompts)
    ]


def _media_inputs(media_refs: list[dict]) -> list[MediaInput]:
    inputs: list[MediaInput] = []
    for ref in media_refs:
        asset = get_asset(ref["asset_id"])
        kind = asset["kind"]
        mime = asset.get("mime_type") or ("image/png" if kind == "image" else "video/mp4")
        role = ref.get("role", "reference")
        data = Path(asset["path"]).read_bytes()

        # Se o papel for frame estático mas o arquivo for vídeo, extrai o frame em PNG
        if role in ("first_frame", "last_frame") and (kind == "video" or mime.startswith("video/")):
            try:
                from . import postproduction
                if postproduction.available():
                    dest = settings.uploads_dir / f"{asset['id']}_{role}.png"
                    if role == "first_frame":
                        postproduction.extract_first_frame(asset["path"], dest)
                    else:
                        postproduction.extract_last_frame(asset["path"], dest)
                    if dest.exists():
                        data = dest.read_bytes()
                        kind = "image"
                        mime = "image/png"
            except Exception:
                pass

        inputs.append(
            MediaInput(
                kind=kind,
                data=data,
                mime_type=mime,
                role=role,
            )
        )
    return inputs


def run_generation(generation_id: str) -> None:
    """Executado na thread pool: chama o provider e persiste o resultado."""
    try:
        generation = get_generation(generation_id)
    except StudioError:
        return
    db.update("generations", generation_id, {"status": "running"})
    try:
        parent = get_generation(generation["parent_id"]) if generation["parent_id"] else None
        request = VideoRequest(
            prompt=generation["prompt"],
            mode=generation["mode"],
            resolution=generation["resolution"],
            aspect_ratio=generation["aspect_ratio"],
            duration_seconds=generation["duration_seconds"],
            previous_interaction_id=parent["interaction_id"] if parent else None,
            media=_media_inputs(generation["meta"].get("media", [])),
            seed=generation["meta"].get("seed"),
        )
        result = get_provider(generation["provider"]).generate(request)
        suffix = mimetypes.guess_extension(result.mime_type) or ".mp4"
        path = settings.media_dir / f"{generation_id}{suffix}"
        path.write_bytes(result.data)
        db.update(
            "generations",
            generation_id,
            {
                "status": "completed",
                "interaction_id": result.interaction_id,
                "asset_path": str(path),
                "mime_type": result.mime_type,
                "error": None,
            },
        )
    except (ProviderError, StudioError) as exc:
        db.update("generations", generation_id, {"status": "failed", "error": str(exc)})
    except Exception as exc:  # rede, disco, bug: registra sem derrubar o worker
        db.update(
            "generations",
            generation_id,
            {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
        )


def project_stats(project_id: str) -> dict:
    rows = list_generations(project_id)
    completed = [r for r in rows if r["status"] == "completed"]
    return {
        "total": len(rows),
        "completed": len(completed),
        "failed": len([r for r in rows if r["status"] == "failed"]),
        "pending": len([r for r in rows if r["status"] in ("queued", "running")]),
        "cost_units": round(sum(r["cost_units"] for r in completed), 2),
        "seconds_generated": sum(r["duration_seconds"] for r in completed),
    }
