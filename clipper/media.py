"""Leitura de mídia com FFmpeg/FFprobe: metadados e curva de energia do áudio."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ClipperError(RuntimeError):
    """Erro esperado do clipador (entrada inválida, dependência ausente)."""


def _find_binary(name: str, env_var: str) -> str:
    """VF_FFMPEG / VF_FFPROBE (mesmas variáveis do resto do video-factory) ou o PATH."""
    return os.environ.get(env_var, "").strip() or shutil.which(name) or ""


def ffmpeg_bin() -> str:
    path = _find_binary("ffmpeg", "VF_FFMPEG")
    if not path:
        raise ClipperError(
            "FFmpeg não encontrado. Instale (brew install ffmpeg-full / apt install ffmpeg) "
            "ou aponte VF_FFMPEG e VF_FFPROBE para os binários. A legenda queimada exige "
            "um FFmpeg com libass (o `ffmpeg-full` do Homebrew tem)."
        )
    return path


def ffprobe_bin() -> str:
    path = _find_binary("ffprobe", "VF_FFPROBE")
    if not path:
        raise ClipperError("ffprobe não encontrado. Instale o FFmpeg ou aponte VF_FFPROBE para o binário.")
    return path


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def _fraction(value: str) -> float:
    try:
        num, _, den = value.partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str | Path) -> MediaInfo:
    path = Path(path)
    if not path.is_file():
        raise ClipperError(f"arquivo não encontrado: {path}")
    proc = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ClipperError(f"ffprobe não conseguiu ler {path.name}: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ClipperError(f"{path.name} não tem faixa de vídeo")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ClipperError(f"duração de {path.name} desconhecida")
    return MediaInfo(
        duration=duration,
        width=int(video["width"]),
        height=int(video["height"]),
        fps=_fraction(video.get("avg_frame_rate", "0/1")) or 30.0,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


_RMS_LINE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+|-?inf)")
SILENCE_DB = -90.0


def rms_series(path: str | Path, step: float = 0.5, sample_rate: int = 16000) -> list[float]:
    """Nível RMS (dB) de cada janela de `step` segundos, na ordem do arquivo.

    Usa o `astats` do próprio FFmpeg: roda em velocidade de decodificação, sem
    numpy e sem carregar o áudio na memória.
    """
    n = int(sample_rate * step)
    af = (
        f"aresample={sample_rate},aformat=channel_layouts=mono,"
        f"asetnsamples=n={n}:p=0,astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
    )
    proc = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-nostdin", "-i", str(path), "-vn", "-af", af, "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ClipperError(f"falha ao medir o áudio: {proc.stderr.strip()[:300]}")
    values: list[float] = []
    for match in _RMS_LINE.finditer(proc.stdout):
        raw = match.group(1)
        db = SILENCE_DB if "inf" in raw else float(raw)
        values.append(max(db, SILENCE_DB) if not math.isnan(db) else SILENCE_DB)
    return values
