"""Transcrição com tempo por palavra.

`faster-whisper` é opcional (roda local, CPU ou GPU). Quem já tem legenda pode
passar `--transcript` com `.srt` ou `.json` e pular esta etapa.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .media import ClipperError


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


def segments_to_json(segments: list[Segment]) -> list[dict]:
    return [asdict(s) for s in segments]


def segments_from_json(data: list[dict]) -> list[Segment]:
    out: list[Segment] = []
    for item in data:
        words = [Word(float(w["start"]), float(w["end"]), str(w["text"])) for w in item.get("words", [])]
        seg = Segment(float(item["start"]), float(item["end"]), str(item["text"]).strip(), words)
        if not seg.words:
            seg.words = split_words(seg)
        out.append(seg)
    return out


def split_words(seg: Segment) -> list[Word]:
    """Distribui o tempo do segmento entre as palavras, proporcional ao tamanho."""
    tokens = seg.text.split()
    if not tokens:
        return []
    weights = [max(len(t), 2) for t in tokens]
    total = sum(weights)
    span = max(seg.end - seg.start, 0.01)
    cursor = seg.start
    words: list[Word] = []
    for token, weight in zip(tokens, weights):
        length = span * weight / total
        words.append(Word(round(cursor, 3), round(cursor + length, 3), token))
        cursor += length
    return words


_SRT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def _srt_seconds(text: str) -> float:
    m = _SRT_TIME.search(text)
    if not m:
        raise ClipperError(f"tempo de SRT inválido: {text!r}")
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def load_srt(path: Path) -> list[Segment]:
    segments: list[Segment] = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        timing = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if timing is None:
            continue
        left, _, right = lines[timing].partition("-->")
        text = " ".join(lines[timing + 1 :]).strip()
        if not text:
            continue
        seg = Segment(_srt_seconds(left), _srt_seconds(right), text)
        seg.words = split_words(seg)
        segments.append(seg)
    return segments


def load_transcript(path: str | Path) -> list[Segment]:
    path = Path(path)
    if not path.is_file():
        raise ClipperError(f"transcrição não encontrada: {path}")
    if path.suffix.lower() == ".srt":
        return load_srt(path)
    if path.suffix.lower() == ".json":
        return segments_from_json(json.loads(path.read_text(encoding="utf-8")))
    raise ClipperError("use uma transcrição .srt ou .json")


def transcribe_whisper(
    media: str | Path,
    *,
    model: str = "small",
    language: str | None = "pt",
    device: str = "auto",
    compute_type: str = "auto",
    progress: Callable[[float], None] | None = None,
) -> list[Segment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ClipperError(
            "faster-whisper não instalado. Rode: pip install -r requirements-clipper.txt "
            "(ou passe --transcript com um .srt/.json)."
        ) from exc
    whisper = WhisperModel(model, device=device, compute_type=compute_type)
    raw, info = whisper.transcribe(
        str(media), language=language, word_timestamps=True, vad_filter=True,
        condition_on_previous_text=False,
    )
    segments: list[Segment] = []
    for seg in raw:
        words = [Word(round(w.start, 3), round(w.end, 3), w.word.strip()) for w in (seg.words or []) if w.word.strip()]
        item = Segment(round(seg.start, 3), round(seg.end, 3), seg.text.strip(), words)
        if not item.words:
            item.words = split_words(item)
        segments.append(item)
        if progress and info.duration:
            progress(min(seg.end / info.duration, 1.0))
    return segments
