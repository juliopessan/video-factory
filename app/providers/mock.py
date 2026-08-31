"""Provider offline: nao chama a API, produz um clipe sintetico.

Serve para desenvolver a interface, testar o encadeamento de cenas e a fila de
jobs sem gastar cota. Com FFmpeg instalado gera um MP4 de verdade (cor solida,
o prompt escrito no quadro e um tom de audio), o que permite exercitar tambem a
pos-producao e o encadeamento por keyframe. Sem FFmpeg, cai num SVG animado.
"""
from __future__ import annotations

import hashlib
import html
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
import uuid
from pathlib import Path

from .base import DEFAULT_CAPABILITIES, VideoRequest, VideoResult

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


def _find_font() -> str | None:
    env_font = os.environ.get("VF_FONTFILE")
    if env_font and Path(env_font).exists():
        if env_font.lower().startswith("c:/windows"):
            return "/Windows/" + env_font[11:].replace("\\", "/")
        return Path(env_font).as_posix()
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSText.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            if c.lower().startswith("c:/windows"):
                return "/Windows/" + c[11:].replace("\\", "/")
            return Path(c).as_posix()
    return None


def render_clip_mp4(request: VideoRequest, clip_id: str, ffmpeg: str) -> bytes | None:
    """Clipe real, para a pós-produção ter o que processar offline."""
    bg, accent, _ = _palette(request.prompt + request.mode)
    width, height = (1280, 720) if request.aspect_ratio == "16:9" else (720, 1280)
    legend = " ".join(_wrap(request.prompt, width=28, max_lines=3))
    legend = legend.replace("'", "").replace(":", " ").replace("\\", " ")
    font = _find_font()
    font_arg = f"fontfile={font}:" if font else ""
    vf = (
        f"drawbox=x=0:y=ih-12:w=iw*t/{request.duration_seconds}:h=12:color={accent}:t=fill,"
        f"drawtext={font_arg}text='{legend}':fontcolor=white:fontsize={height // 22}:x=(w-tw)/2:y=(h-th)/2,"
        f"drawtext={font_arg}text='{clip_id[:8]} %{{eif\\:t\\:d}}s':fontcolor=white@0.6:fontsize={height // 34}:x=40:y=h-80"
    )
    with tempfile.TemporaryDirectory() as tmp:
        destination = Path(tmp) / "clip.mp4"
        command = [
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={bg}:s={width}x{height}:d={request.duration_seconds}:r=25",
            "-f", "lavfi", "-i", f"sine=frequency=320:duration={request.duration_seconds}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(destination),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not destination.exists():
            # Fallback sem drawtext caso haja restrição de fonte ou fontconfig
            fallback_command = [
                ffmpeg, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c={bg}:s={width}x{height}:d={request.duration_seconds}:r=25",
                "-f", "lavfi", "-i", f"sine=frequency=320:duration={request.duration_seconds}",
                "-vf", f"drawbox=x=0:y=ih-12:w=iw*t/{request.duration_seconds}:h=12:color={accent}:t=fill",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(destination),
            ]
            result = subprocess.run(fallback_command, capture_output=True, text=True, timeout=600)
            if result.returncode != 0 or not destination.exists():
                return None
        return destination.read_bytes()


class MockProvider:
    name = "mock"

    def __init__(self, latency_seconds: float = 1.5) -> None:
        self.latency_seconds = latency_seconds

    @staticmethod
    def capabilities() -> dict:
        capabilities = dict(DEFAULT_CAPABILITIES)
        # permite exercitar o encadeamento por keyframe sem provider real
        if os.environ.get("VF_MOCK_NO_EXTEND"):
            capabilities["extend"] = False
        return capabilities

    def generate(self, request: VideoRequest) -> VideoResult:
        time.sleep(self.latency_seconds)
        clip_id = uuid.uuid4().hex
        ffmpeg = os.environ.get("VF_FFMPEG") or shutil.which("ffmpeg")
        if ffmpeg:
            data = render_clip_mp4(request, clip_id, ffmpeg)
            if data:
                return VideoResult(
                    interaction_id=f"mock-{clip_id}", data=data, mime_type="video/mp4"
                )
        return VideoResult(
            interaction_id=f"mock-{clip_id}",
            data=render_clip_svg(request, clip_id),
            mime_type="image/svg+xml",
        )
