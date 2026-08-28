"""Provider offline: nao chama a API, produz um clipe sintetico (SVG animado).

Serve para desenvolver a interface, testar o encadeamento de cenas e a fila de
jobs sem gastar cota. O arquivo gerado nao e um MP4 - a UI o exibe como imagem
animada e o marca como rascunho local.
"""
from __future__ import annotations

import hashlib
import html
import textwrap
import time
import uuid

from .base import VideoRequest, VideoResult

PALETTES = [
    ("#0f172a", "#6366f1", "#22d3ee"),
    ("#1c1917", "#f97316", "#fbbf24"),
    ("#0b1120", "#db2777", "#a855f7"),
    ("#052e2b", "#10b981", "#a3e635"),
]


def _palette(seed: str) -> tuple[str, str, str]:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return PALETTES[digest[0] % len(PALETTES)]


def _wrap(text: str, width: int = 42, max_lines: int = 4) -> list[str]:
    lines = textwrap.wrap(text.strip() or "(sem prompt)", width=width)[:max_lines]
    if not lines:
        lines = ["(sem prompt)"]
    return lines


def render_clip_svg(request: VideoRequest, clip_id: str) -> bytes:
    bg, accent, glow = _palette(request.prompt + request.mode)
    width, height = (1280, 720) if request.aspect_ratio == "16:9" else (720, 1280)
    lines = _wrap(request.prompt)
    text_spans = "".join(
        f'<tspan x="64" dy="{0 if i == 0 else 52}">{html.escape(line)}</tspan>'
        for i, line in enumerate(lines)
    )
    badge = f"{request.mode} · {request.resolution} · {request.duration_seconds}s"
    dur = max(request.duration_seconds, 1)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{bg}"/>
      <stop offset="60%" stop-color="{accent}" stop-opacity="0.55"/>
      <stop offset="100%" stop-color="{glow}" stop-opacity="0.35"/>
    </linearGradient>
  </defs>
  <rect width="{width}" height="{height}" fill="{bg}"/>
  <rect width="{width}" height="{height}" fill="url(#g)">
    <animate attributeName="opacity" values="0.55;1;0.55" dur="{dur}s" repeatCount="indefinite"/>
  </rect>
  <circle cx="{width * 0.75:.0f}" cy="{height * 0.3:.0f}" r="{height * 0.18:.0f}" fill="{glow}" opacity="0.25">
    <animate attributeName="r" values="{height * 0.14:.0f};{height * 0.24:.0f};{height * 0.14:.0f}" dur="{dur}s" repeatCount="indefinite"/>
  </circle>
  <rect x="0" y="{height - 8}" width="{width}" height="8" fill="{glow}">
    <animate attributeName="width" values="0;{width}" dur="{dur}s" repeatCount="indefinite"/>
  </rect>
  <text x="64" y="{height * 0.18:.0f}" font-family="Inter, Segoe UI, sans-serif" font-size="26" fill="{glow}" letter-spacing="4">{html.escape(badge.upper())}</text>
  <text x="64" y="{height * 0.34:.0f}" font-family="Inter, Segoe UI, sans-serif" font-size="44" font-weight="600" fill="#ffffff">{text_spans}</text>
  <text x="64" y="{height - 48}" font-family="ui-monospace, monospace" font-size="22" fill="#ffffff" opacity="0.6">MOCK CLIP · {clip_id[:8]}</text>
</svg>""".encode("utf-8")


class MockProvider:
    name = "mock"

    def __init__(self, latency_seconds: float = 1.5) -> None:
        self.latency_seconds = latency_seconds

    def generate(self, request: VideoRequest) -> VideoResult:
        time.sleep(self.latency_seconds)
        clip_id = uuid.uuid4().hex
        return VideoResult(
            interaction_id=f"mock-{clip_id}",
            data=render_clip_svg(request, clip_id),
            mime_type="image/svg+xml",
        )
