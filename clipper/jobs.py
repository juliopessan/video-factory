"""Estado em disco: um job por gravação, com manifest.json e os cortes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from .media import ClipperError

BASE_DIR = Path(__file__).resolve().parent.parent  # raiz do video-factory

STATUSES = ("pending", "approved", "rejected", "error")
_lock = threading.Lock()


def storage_root() -> Path:
    raw = Path(os.environ.get("VF_STORAGE_DIR", "storage"))
    root = raw if raw.is_absolute() else BASE_DIR / raw
    return root / "clipper"


def job_id_for(source: Path) -> str:
    stat = source.stat()
    digest = hashlib.sha1(f"{source.resolve()}:{stat.st_size}:{int(stat.st_mtime)}".encode()).hexdigest()[:8]
    return f"{re.sub(r'[^a-z0-9]+', '-', source.stem.lower()).strip('-')[:32] or 'live'}-{digest}"


def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", job_id):
        raise ClipperError("id de job inválido")
    return storage_root() / job_id


def read_manifest(job_id: str) -> dict:
    path = job_dir(job_id) / "manifest.json"
    if not path.is_file():
        raise ClipperError(f"job não encontrado: {job_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(job_id: str, manifest: dict) -> None:
    path = job_dir(job_id) / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with _lock:
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def update_clip(job_id: str, clip_id: str, changes: dict) -> dict:
    with _lock:  # leitura-modificação-escrita sem corrida entre cliques da interface
        manifest = read_manifest(job_id)
        clip = next((c for c in manifest["clips"] if c["id"] == clip_id), None)
        if clip is None:
            raise ClipperError(f"corte não encontrado: {clip_id}")
        clip.update(changes)
        path = job_dir(job_id) / "manifest.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return clip


def list_jobs() -> list[dict]:
    root = storage_root()
    jobs = []
    for path in sorted(root.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        m = json.loads(path.read_text(encoding="utf-8"))
        counts = {s: sum(1 for c in m["clips"] if c["status"] == s) for s in STATUSES}
        jobs.append({"job_id": m["job_id"], "source_name": m["source_name"], "created_at": m["created_at"],
                     "clips": len(m["clips"]), **counts})
    return jobs


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:48] or "clipe"


def export_approved(job_id: str) -> Path:
    """Copia os aprovados para <job>/export com nome legível e escreve legendas.txt."""
    manifest = read_manifest(job_id)
    approved = [c for c in manifest["clips"] if c["status"] == "approved"]
    if not approved:
        raise ClipperError("nenhum corte aprovado para exportar")
    out = job_dir(job_id) / "export"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    posts = []
    for n, clip in enumerate(sorted(approved, key=lambda c: c["start"]), 1):
        name = f"{n:02d}-{_slug(clip['title'])}.mp4"
        shutil.copyfile(job_dir(job_id) / "clips" / f"{clip['id']}.mp4", out / name)
        posts.append(f"[{name}] ({clip['end'] - clip['start']:.0f}s)\n{clip['title']}\n{clip['hashtags']}\n")
    (out / "legendas.txt").write_text("\n".join(posts), encoding="utf-8")
    return out
