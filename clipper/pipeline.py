"""Orquestra o fluxo: probe -> energia -> transcrição -> pontuação -> render -> manifest."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from . import MAX_SECONDS, MIN_SECONDS, jobs, media, scoring
from .render import Facecam, render_clip, detect_scene, scene_ref
from .transcribe import Segment, load_transcript, segments_from_json, segments_to_json, transcribe_whisper

Log = Callable[[str], None]


def _cached_rms(path: Path, source: Path, log: Log) -> list[float]:
    if path.is_file():
        return json.loads(path.read_text())
    log("medindo energia do áudio…")
    series = media.rms_series(source)
    path.write_text(json.dumps(series))
    return series


def _transcript(job: Path, source: Path, transcript: str | None, model: str, language: str | None,
                device: str, log: Log) -> list[Segment]:
    cache = job / "transcript.json"
    if transcript:
        segments = load_transcript(transcript)
    elif cache.is_file():
        log("transcrição em cache")
        return segments_from_json(json.loads(cache.read_text(encoding="utf-8")))
    else:
        log(f"transcrevendo com whisper '{model}' (a parte mais demorada)…")
        last = [-1]

        def progress(frac: float) -> None:
            pct = int(frac * 100) // 10 * 10
            if pct != last[0]:
                last[0] = pct
                log(f"  transcrição {pct}%")

        segments = transcribe_whisper(source, model=model, language=language, device=device, progress=progress)
    cache.write_text(json.dumps(segments_to_json(segments), ensure_ascii=False), encoding="utf-8")
    return segments


def run_job(
    source: str | Path,
    *,
    transcript: str | None = None,
    model: str = "small",
    language: str | None = "pt",
    device: str = "auto",
    count: int = 10,
    min_seconds: float = MIN_SECONDS,
    max_seconds: float = MAX_SECONDS,
    facecam: str | None = None,
    font: str = "Inter",
    fonts_dir: str | None = None,
    layout: str = "fill",
    talk_at: float | None = None,
    plan_only: bool = False,
    log: Log = print,
) -> dict:
    source = Path(source).expanduser()
    info = media.probe(source)
    if min_seconds < MIN_SECONDS:
        log(f"aviso: cortes abaixo de {MIN_SECONDS:.0f}s não entram no Creator Rewards")
    if not info.has_audio:
        raise media.ClipperError("a gravação não tem áudio: não há como achar os melhores momentos")
    cam = Facecam.parse(facecam) if facecam else None

    job_id = jobs.job_id_for(source)
    job = jobs.job_dir(job_id)
    job.mkdir(parents=True, exist_ok=True)
    log(f"job {job_id}: {info.duration / 60:.1f} min, {info.width}x{info.height}")

    rms = _cached_rms(job / "rms.json", source, log)
    segments = _transcript(job, source, transcript, model, language, device, log)
    log(f"{len(segments)} frases transcritas; pontuando janelas…")

    features = scoring.build_features(info.duration, rms, segments)
    windows = scoring.find_windows(features, segments, count=count, min_seconds=min_seconds, max_seconds=max_seconds)
    log(f"{len(windows)} candidatos")

    clips = []
    for rank, win in enumerate(sorted(windows, key=lambda w: -w.score), 1):
        clips.append({
            "id": f"clip-{len(clips) + 1:02d}",
            "rank": rank,
            "start": round(win.start, 2),
            "end": round(win.end, 2),
            "score": win.score,
            "mean_energy": win.mean_energy,
            "keyword_hits": win.keyword_hits,
            "title": scoring.make_title(win, segments),
            "hashtags": " ".join(scoring.DEFAULT_HASHTAGS),
            "status": "pending",
            "error": None,
        })
    clips.sort(key=lambda c: c["start"])
    for n, clip in enumerate(clips, 1):
        clip["id"] = f"clip-{n:02d}"

    manifest = {
        "job_id": job_id,
        "source": str(source.resolve()),
        "source_name": source.name,
        "created_at": jobs.now_iso(),
        "duration": round(info.duration, 2),
        "params": {"facecam": facecam, "font": font, "fonts_dir": fonts_dir, "layout": layout, "talk_at": talk_at,
                   "min_seconds": min_seconds, "max_seconds": max_seconds},
        "clips": clips,
    }
    jobs.write_manifest(job_id, manifest)

    if not plan_only:
        ref = scene_ref(source, talk_at)
        for clip in clips:
            clip["scene"] = detect_scene(source, clip["start"], clip["end"], ref)
            log(f"renderizando {clip['id']} ({clip['end'] - clip['start']:.0f}s, nota {clip['score']:.2f})…")
            try:
                render_clip(source, info, segments, clip["start"], clip["end"], job / "clips", clip["id"],
                            facecam=cam, font=font, fonts_dir=fonts_dir, layout=layout, scene=clip['scene'])
            except media.ClipperError as exc:
                clip["status"], clip["error"] = "error", str(exc)
                log(f"  falhou: {exc}")
            jobs.write_manifest(job_id, manifest)
    log(f"pronto: abra a fila com `python -m clipper serve` (job {job_id})")
    return manifest


def rerender(job_id: str, clip_id: str, start: float, end: float) -> dict:
    """Refaz um corte com novo início/fim (ajuste fino na revisão)."""
    manifest = jobs.read_manifest(job_id)
    job = jobs.job_dir(job_id)
    source = Path(manifest["source"])
    info = media.probe(source)
    start, end = max(0.0, start), min(info.duration, end)
    if end - start < 5:
        raise media.ClipperError("corte curto demais")
    params = manifest["params"]
    segments = segments_from_json(json.loads((job / "transcript.json").read_text(encoding="utf-8")))
    cam = Facecam.parse(params["facecam"]) if params.get("facecam") else None
    render_clip(source, info, segments, start, end, job / "clips", clip_id, facecam=cam,
                font=params["font"], fonts_dir=params.get("fonts_dir"), layout=params.get("layout", "fill"),
                scene=next((c.get("scene", "game") for c in manifest["clips"] if c["id"] == clip_id), "game"))
    return jobs.update_clip(job_id, clip_id, {"start": round(start, 2), "end": round(end, 2), "error": None,
                                              "status": "pending"})
