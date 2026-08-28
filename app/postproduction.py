"""Passo 5 — pós-produção local com FFmpeg.

O que o modelo entrega é a tomada; o acabamento é determinístico e roda aqui:
legendas a partir da própria locução do storyboard, normalização de áudio,
fades e as versões de formato (16:9, 9:16, 1:1).

Nada disso é generativo — por isso é FFmpeg direto, e não uma chamada de modelo.
Edição exploratória ("corta os 4s mortos da peça 2") é o caso de uso do MCP; ver
a seção "Edição automática" no README.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

from . import db, studio
from .config import settings

FFMPEG = os.environ.get("VF_FFMPEG") or shutil.which("ffmpeg") or ""
FFPROBE = os.environ.get("VF_FFPROBE") or shutil.which("ffprobe") or ""

# rótulo -> (resolução de saída, filtro de recorte, filtro de encaixe)
#
# `crop` preenche a tela e perde as laterais; `pad` cabe o quadro inteiro e
# adiciona barras. Packshot com logo ou texto centralizado pede `pad`: o recorte
# central de um 16:9 corta as pontas da marca.
FORMATS: dict[str, tuple[str, str, str]] = {
    "16:9": (
        "1920x1080",
        "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080",
        "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
    ),
    "9:16": (
        "1080x1920",
        "crop=ih*9/16:ih,scale=1080:1920",
        "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
    ),
    "1:1": (
        "1080x1080",
        "crop=ih:ih,scale=1080:1080",
        "scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2",
    ),
}
FITS = ("crop", "pad")


def frame_filter(label: str, fit: str = "crop") -> str:
    resolution, crop, pad = FORMATS[label]
    return pad if fit == "pad" else crop

# O libass escala a fonte pela PlayResY do ASS (288 por padrao numa conversao de
# SRT), nao pela altura do video: FontSize=12 rende ~4% da altura em qualquer
# formato, e MarginV=18 deixa a legenda a ~6% da borda de baixo.
SUBTITLE_STYLE = (
    "FontName=DejaVu Sans,FontSize=12,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&HB0000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=18"
)

# quadro estreito comporta menos caracteres por linha antes de virar um paredao
LINE_CHARS_BY_FORMAT = {"16:9": 42, "9:16": 26, "1:1": 32}


class PostProductionUnavailable(studio.StudioError):
    """FFmpeg não encontrado no PATH."""


def available() -> bool:
    return bool(FFMPEG and FFPROBE)


def _require_ffmpeg() -> None:
    if not available():
        raise PostProductionUnavailable(
            "FFmpeg não encontrado. Instale (apt install ffmpeg / brew install ffmpeg) "
            "ou aponte VF_FFMPEG e VF_FFPROBE para os binários."
        )


# --------------------------------------------------------------------- legendas


def _srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


MAX_LINE_CHARS = 42
MAX_CUE_LINES = 2
MIN_CUE_SECONDS = 1.2  # abaixo disso a legenda pisca e ninguem le


def _split_cues(text: str, line_chars: int = MAX_LINE_CHARS) -> list[str]:
    """Quebra a locução em blocos de no máximo duas linhas legíveis."""
    lines = textwrap.wrap(text, width=line_chars)
    return ["\n".join(lines[i : i + MAX_CUE_LINES]) for i in range(0, len(lines), MAX_CUE_LINES)]


def build_srt(segments: list[dict], segment_seconds: int, line_chars: int = MAX_LINE_CHARS) -> str:
    """Legendas a partir da locução do storyboard.

    A locução já vem escrita e cronometrada, então não há transcrição: as marcas
    de tempo saem do próprio plano. Locução longa vira várias legendas dentro da
    janela da peça, repartidas pelo tamanho do texto — nada é truncado.
    """
    cues: list[str] = []
    for index, segment in enumerate(segments):
        text = " ".join((segment.get("vo") or "").split())
        if not text:
            continue
        blocks = _split_cues(text, line_chars)
        window_start = index * segment_seconds
        window_end = window_start + segment_seconds - 0.2
        window = window_end - window_start
        total_chars = sum(len(b) for b in blocks) or 1
        # o tempo acompanha o tamanho do texto, mas um bloco curto no fim da peca
        # ganharia uma legenda que pisca: nesse caso divide a janela por igual.
        shares = [window * len(b) / total_chars for b in blocks]
        if min(shares) < MIN_CUE_SECONDS:
            shares = [window / len(blocks)] * len(blocks)
        cursor = window_start
        for position, block in enumerate(blocks):
            share = shares[position]
            end = window_end if position == len(blocks) - 1 else min(cursor + share, window_end)
            cues.append(
                f"{len(cues) + 1}\n{_srt_timestamp(cursor)} --> {_srt_timestamp(end)}\n{block}\n"
            )
            cursor = end
    return "\n".join(cues)


# ------------------------------------------------------------------- comandos


def probe_duration(path: str | Path) -> float | None:
    _require_ffmpeg()
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def build_command(
    source: Path,
    destination: Path,
    frame_filter: str,
    duration: float | None = None,
    subtitles: Path | None = None,
    normalize_audio: bool = True,
    fade: bool = True,
) -> list[str]:
    """Monta o argv do FFmpeg. Isolado para poder ser testado sem executar nada."""
    video_chain = [frame_filter]
    if subtitles:
        # o caminho vai dentro do filtro: escapa ':' e '\' como o filtergraph espera
        escaped = str(subtitles).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        video_chain.append(f"subtitles='{escaped}':force_style='{SUBTITLE_STYLE}'")
    if fade:
        video_chain.append("fade=t=in:st=0:d=0.5")
        if duration and duration > 1.5:
            video_chain.append(f"fade=t=out:st={duration - 0.8:.2f}:d=0.8")

    audio_chain = []
    if normalize_audio:
        audio_chain.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    if fade:
        audio_chain.append("afade=t=in:st=0:d=0.4")
        if duration and duration > 1.5:
            audio_chain.append(f"afade=t=out:st={duration - 0.6:.2f}:d=0.6")

    command = [FFMPEG, "-y", "-loglevel", "error", "-i", str(source),
               "-vf", ",".join(video_chain)]
    if audio_chain:
        command += ["-af", ",".join(audio_chain)]
    command += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(destination),
    ]
    return command


# ------------------------------------------------------------------ execução


def _exports_dir() -> Path:
    path = settings.storage_dir / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _require_video(generation: dict) -> None:
    """O provider mock devolve um SVG animado: não há o que pós-produzir."""
    mime = generation.get("mime_type") or ""
    if not mime.startswith("video/"):
        raise studio.StudioError(
            f"A peça foi gerada pelo provider '{generation.get('provider')}' e não é vídeo "
            f"({mime or 'sem mime type'}). Configure GEMINI_API_KEY e renderize de novo "
            "para exportar."
        )


def list_exports(pipeline_id: str) -> list[dict]:
    rows = db.query(
        "SELECT * FROM exports WHERE pipeline_id = ? ORDER BY created_at ASC", [pipeline_id]
    )
    for row in rows:
        row["params"] = json.loads(row.get("params") or "{}")
    return rows


def get_export(export_id: str) -> dict:
    row = db.query_one("SELECT * FROM exports WHERE id = ?", [export_id])
    if not row:
        raise studio.StudioError(f"Export {export_id} não encontrado.")
    row["params"] = json.loads(row.get("params") or "{}")
    return row


def create_exports(
    pipeline_id: str,
    formats: list[str] | None = None,
    fit: str = "crop",
    burn_subtitles: bool = True,
    normalize_audio: bool = True,
    fade: bool = True,
) -> list[dict]:
    """Enfileira um export por formato, a partir do filme já renderizado."""
    from . import pipeline as pipeline_mod

    _require_ffmpeg()
    pipeline = pipeline_mod.get_pipeline(pipeline_id)
    completed = [r for r in pipeline["renders"] if r["status"] == "completed"]
    if not completed:
        raise studio.StudioError("Renderize o filme antes de exportar.")
    # cada extensão devolve o filme acumulado: a última peça pronta é o master
    master = completed[-1]
    _require_video(master)

    formats = formats or ["16:9"]
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        raise studio.StudioError(f"Formato desconhecido: {', '.join(unknown)}.")
    if fit not in FITS:
        raise studio.StudioError(f"Enquadramento inválido: {fit}. Use crop ou pad.")

    segments = pipeline["storyboard"].get("segments") or []
    subtitle_paths: dict[int, Path] = {}
    for line_chars in {LINE_CHARS_BY_FORMAT.get(label, MAX_LINE_CHARS) for label in formats}:
        srt = build_srt(segments, pipeline_mod.SEGMENT_SECONDS, line_chars)
        if not srt:
            continue
        path = _exports_dir() / f"{pipeline_id}.{line_chars}.srt"
        path.write_text(srt, encoding="utf-8")
        subtitle_paths[line_chars] = path

    created = []
    for label in formats:
        srt_path = subtitle_paths.get(LINE_CHARS_BY_FORMAT.get(label, MAX_LINE_CHARS))
        export_id = studio.new_id("exp")
        row = {
            "id": export_id,
            "pipeline_id": pipeline_id,
            "generation_id": master["id"],
            "format": label,
            "status": "queued",
            "path": None,
            "mime_type": "video/mp4",
            "size": 0,
            "error": None,
            "params": json.dumps(
                {
                    "burn_subtitles": burn_subtitles and bool(srt_path),
                    "normalize_audio": normalize_audio,
                    "fade": fade,
                    "fit": fit,
                    "resolution": FORMATS[label][0],
                    "subtitles_path": str(srt_path) if srt_path else None,
                }
            ),
            "created_at": db.now(),
            "updated_at": db.now(),
        }
        db.insert("exports", row)
        studio.EXECUTOR.submit(run_export, export_id)
        created.append(get_export(export_id))
    return created


def run_export(export_id: str) -> None:
    try:
        export = get_export(export_id)
    except studio.StudioError:
        return
    db.update("exports", export_id, {"status": "running", "updated_at": db.now()})
    try:
        generation = studio.get_generation(export["generation_id"])
        _require_video(generation)
        source = Path(generation["asset_path"] or "")
        if not source.exists():
            raise studio.StudioError("Arquivo do filme não está mais no disco.")

        params = export["params"]
        label = export["format"]
        destination = _exports_dir() / f"{export_id}_{label.replace(':', 'x')}.mp4"
        command = build_command(
            source=source,
            destination=destination,
            frame_filter=frame_filter(label, params.get("fit", "crop")),
            duration=probe_duration(source),
            subtitles=Path(params["subtitles_path"]) if params.get("burn_subtitles") and params.get("subtitles_path") else None,
            normalize_audio=params.get("normalize_audio", True),
            fade=params.get("fade", True),
        )
        result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0 or not destination.exists():
            raise studio.StudioError(
                f"FFmpeg falhou ({result.returncode}): {result.stderr.strip()[:400]}"
            )
        db.update(
            "exports",
            export_id,
            {
                "status": "completed",
                "path": str(destination),
                "size": destination.stat().st_size,
                "error": None,
                "updated_at": db.now(),
            },
        )
    except Exception as exc:
        db.update(
            "exports",
            export_id,
            {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "updated_at": db.now()},
        )
