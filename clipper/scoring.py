"""Pontuação de janelas: onde estão os melhores 61–90 s da live.

Sem modelo e sem custo. Quatro sinais por segundo, combinados:

- energia do áudio, normalizada pela própria live (p50 -> 0, p95 -> 1);
- léxico de reação e de ação em PT-BR (e algumas interjeições em inglês);
- exclamações e interrogações na fala transcrita;
- densidade de fala (quanto do segundo tem palavra).

A janela vale 60% da média e 40% do pico suavizado, com penalidade para
trechos muito silenciosos. Início e fim são sempre limites de frase.
"""
from __future__ import annotations

import bisect
import math
import re
import unicodedata
from dataclasses import dataclass, field

from . import MAX_SECONDS, MIN_SECONDS
from .transcribe import Segment

W_ENERGY, W_KEYWORD, W_EXCLAIM, W_SPEECH = 0.50, 0.25, 0.10, 0.15
PRE_ROLL, POST_ROLL = 1.0, 0.8
MIN_SCORE = 0.12


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in folded if not unicodedata.combining(c))


# peso por ocorrência; frases primeiro (casadas por regex com limite de palavra)
KEYWORDS: dict[str, float] = {
    "meu deus": 1.2, "nao acredito": 1.2, "que isso": 0.9, "olha isso": 1.0, "oh my god": 1.2,
    "caraca": 1.0, "caramba": 0.8, "nossa": 0.7, "socorro": 1.0, "omg": 1.0, "wow": 0.8,
    "insano": 1.0, "absurdo": 0.9, "loucura": 0.9, "maluco": 0.6, "incrivel": 0.8,
    "consegui": 0.9, "ganhei": 0.8, "perdi": 0.7, "morri": 0.9, "morreu": 0.8,
    "explodiu": 1.0, "explosao": 1.0, "bati": 0.8, "capotou": 1.0, "caiu": 0.5,
    "perseguicao": 1.0, "policia": 0.8, "helicoptero": 0.8, "estrelas": 0.6, "fugi": 0.9,
    "escapei": 0.9, "corre": 0.7, "cuidado": 0.6, "calma": 0.5, "pula": 0.5,
    "tiro": 0.7, "tiros": 0.8, "assalto": 0.9, "roubo": 0.7, "banco": 0.4,
    "kkkk": 0.8, "hahaha": 0.8, "risada": 0.6, "rindo": 0.6,
}
_KEYWORD_RE = [(re.compile(rf"\b{re.escape(k)}\b"), w) for k, w in sorted(KEYWORDS.items(), key=lambda kv: -len(kv[0]))]


def keyword_hits(text: str) -> float:
    folded = _norm(text)
    return sum(weight * len(rx.findall(folded)) for rx, weight in _KEYWORD_RE)


def exclaim_hits(text: str) -> float:
    return 0.5 * text.count("!") + 0.3 * text.count("?")


@dataclass
class Features:
    duration: float
    energy: list[float]
    keyword: list[float]
    exclaim: list[float]
    speech: list[float]
    base: list[float] = field(default_factory=list)  # nota por segundo, 0..1

    @property
    def bins(self) -> int:
        return len(self.base)


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(q * (len(ordered) - 1) + 0.5), len(ordered) - 1)]


def build_features(duration: float, rms_db: list[float], segments: list[Segment], rms_step: float = 0.5) -> Features:
    nbins = max(int(math.ceil(duration)), 1)
    per_bin = max(int(round(1.0 / rms_step)), 1)

    # energia: dB por segundo -> 0..1 relativo à própria live
    sec_db = []
    for i in range(nbins):
        chunk = rms_db[i * per_bin : (i + 1) * per_bin]
        sec_db.append(max(chunk) if chunk else -90.0)
    voiced = [d for d in sec_db if d > -60]
    p50, p95 = _percentile(voiced, 0.50), _percentile(voiced, 0.95)
    spread = max(p95 - p50, 3.0)
    energy = [_clamp((d - p50) / spread, 0.0, 1.5) / 1.5 if d > -60 else 0.0 for d in sec_db]

    keyword = [0.0] * nbins
    exclaim = [0.0] * nbins
    speech = [0.0] * nbins
    for seg in segments:
        span = max(seg.end - seg.start, 0.5)
        first, last = int(seg.start), min(int(seg.end), nbins - 1)
        if first >= nbins:
            continue
        kw_rate = _clamp(keyword_hits(seg.text) / span, 0.0, 1.5) / 1.5
        ex_rate = _clamp(exclaim_hits(seg.text) / span, 0.0, 1.0)
        for b in range(first, last + 1):
            keyword[b] = max(keyword[b], kw_rate)
            exclaim[b] = max(exclaim[b], ex_rate)
        for word in seg.words:
            lo, hi = word.start, max(word.end, word.start + 0.05)
            for b in range(int(lo), min(int(hi), nbins - 1) + 1):
                speech[b] += max(0.0, min(hi, b + 1) - max(lo, b))
    speech = [min(s, 1.0) for s in speech]

    base = [
        W_ENERGY * energy[i] + W_KEYWORD * keyword[i] + W_EXCLAIM * exclaim[i] + W_SPEECH * speech[i]
        for i in range(nbins)
    ]
    return Features(duration, energy, keyword, exclaim, speech, base)


@dataclass
class Window:
    start: float
    end: float
    score: float
    mean_energy: float
    keyword_hits: float
    text: str = ""

    @property
    def length(self) -> float:
        return self.end - self.start


def _prefix(values: list[float]) -> list[float]:
    acc = [0.0]
    for v in values:
        acc.append(acc[-1] + v)
    return acc


def score_window(feat: Features, pre_base: list[float], smooth: list[float], start: float, end: float) -> float:
    lo, hi = int(start), min(int(math.ceil(end)), feat.bins)
    if hi <= lo:
        return 0.0
    mean = (pre_base[hi] - pre_base[lo]) / (hi - lo)
    peak = max(smooth[lo:hi])
    quiet = sum(1 for i in range(lo, hi) if feat.speech[i] < 0.1 and feat.energy[i] < 0.2) / (hi - lo)
    penalty = 1.0 - 0.8 * max(0.0, quiet - 0.3)
    return (0.6 * mean + 0.4 * peak) * penalty


def find_windows(
    feat: Features,
    segments: list[Segment],
    *,
    count: int = 10,
    min_seconds: float = MIN_SECONDS,
    max_seconds: float = MAX_SECONDS,
    min_gap: float = 5.0,
) -> list[Window]:
    """Os `count` melhores trechos sem sobreposição, em ordem cronológica."""
    if feat.duration < min_seconds + PRE_ROLL:
        return []
    pre_base = _prefix(feat.base)
    smooth = [
        sum(feat.base[max(0, i - 1) : i + 2]) / len(feat.base[max(0, i - 1) : i + 2])
        for i in range(feat.bins)
    ]
    ends = sorted(seg.end for seg in segments)
    starts = sorted({round(seg.start, 2) for seg in segments})
    if not starts:  # sem fala: grade fixa
        starts = [float(t) for t in range(0, int(feat.duration - min_seconds), 5)]
    lengths = sorted({min_seconds, (min_seconds + max_seconds) / 2, max_seconds,
                      min_seconds + (max_seconds - min_seconds) / 4})

    candidates: list[tuple[float, float, float]] = []
    last_start = -1e9
    for start in starts:
        if start - last_start < 4.0:
            continue
        last_start = start
        seen: set[float] = set()
        for length in lengths:
            target = start + length
            if target > feat.duration:
                continue
            if ends:
                idx = bisect.bisect_right(ends, target) - 1
                end = ends[idx] if idx >= 0 else None
                if end is None or end - start < min_seconds:
                    continue
            else:
                end = target
            if end in seen:
                continue
            seen.add(end)
            candidates.append((score_window(feat, pre_base, smooth, start, end), start, end))

    candidates.sort(reverse=True)
    chosen: list[tuple[float, float, float]] = []
    for score, start, end in candidates:
        if score < MIN_SCORE or len(chosen) >= count:
            break
        if all(end + min_gap <= s or start >= e + min_gap for _, s, e in chosen):
            chosen.append((score, start, end))

    windows: list[Window] = []
    for score, start, end in sorted(chosen, key=lambda c: c[1]):
        lo, hi = int(start), min(int(math.ceil(end)), feat.bins)
        text = " ".join(seg.text for seg in segments if seg.end > start and seg.start < end)
        windows.append(Window(
            start=max(0.0, start - PRE_ROLL),
            end=min(feat.duration, end + POST_ROLL),
            score=round(score, 4),
            mean_energy=round(sum(feat.energy[lo:hi]) / max(hi - lo, 1), 3),
            keyword_hits=round(keyword_hits(text), 2),
            text=text,
        ))
    return windows


DEFAULT_HASHTAGS = ["#gta6", "#gtavi", "#gameplay", "#fyp"]


def make_title(window: Window, segments: list[Segment]) -> str:
    """Frase da janela com mais reação (léxico + exclamação), cortada em ~60 caracteres."""
    best, best_score = "", -1.0
    for seg in segments:
        if seg.end <= window.start or seg.start >= window.end:
            continue
        text = seg.text.strip()
        if not 12 <= len(text) <= 160:
            continue
        s = keyword_hits(text) + exclaim_hits(text) + min(len(text), 60) / 120
        if s > best_score:
            best, best_score = text, s
    if not best:
        return "Momento insano no GTA VI"
    best = re.sub(r"\s+", " ", best)
    if len(best) > 60:
        best = best[:60].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return best[0].upper() + best[1:]
