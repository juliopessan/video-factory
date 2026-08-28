"""Testes das duas regras que dependem de detalhes de formato:

1. leitura da duracao de um video sem ffmpeg (MP4/MOV e WebM);
2. montagem do corpo enviado a API por modo — em especial o upscale, que nao
   forca `video_config.task`.

Roda offline: `python3 tests_media.py`.
"""
from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path

os.environ.setdefault("VF_PROVIDER", "mock")
os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-media-"))

from app.mediainfo import probe_duration_seconds  # noqa: E402
from app.providers.base import MediaInput, VideoRequest  # noqa: E402
from app.providers.gemini import GeminiOmniProvider  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="vf-fixtures-"))
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FALHA'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


def close(a, b, tol=0.01) -> bool:
    return a is not None and abs(a - b) < tol


# --------------------------------------------------------------- fixtures MP4


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


def mp4(timescale: int, duration: int, version: int = 0) -> bytes:
    """MP4 minimo com moov>mvhd, conforme ISO/IEC 14496-12."""
    if version == 0:
        mvhd = b"\x00" + b"\x00\x00\x00" + struct.pack(">IIII", 0, 0, timescale, duration)
    else:
        mvhd = b"\x01" + b"\x00\x00\x00" + struct.pack(">QQIQ", 0, 0, timescale, duration)
    mvhd += b"\x00" * 80  # resto do atomo (matriz, rate, volume...) nao e lido
    return box(b"ftyp", b"isom" + b"\x00" * 8) + box(b"moov", box(b"mvhd", mvhd))


def write(name: str, data: bytes) -> Path:
    path = TMP / name
    path.write_bytes(data)
    return path


check("mp4 v0: 2.5s", close(probe_duration_seconds(write("v0.mp4", mp4(1000, 2500))), 2.5))
check("mp4 v1 (64 bits): 7.25s", close(probe_duration_seconds(write("v1.mp4", mp4(600, 4350, 1))), 7.25))
check("mp4 sem moov", probe_duration_seconds(write("x.mp4", box(b"ftyp", b"isom"))) is None)
check("arquivo que nao e video", probe_duration_seconds(write("t.txt", b"nao sou um video")) is None)
check("arquivo vazio", probe_duration_seconds(write("e.mp4", b"")) is None)

# WebM real, gravado pelo Chromium (MediaRecorder) e versionado como fixture.
sample = Path(__file__).parent / "tests" / "fixtures" / "sample.webm"
if sample.exists():
    check("webm real do chromium ~2.4s", close(probe_duration_seconds(sample), 2.44, 0.1),
          str(probe_duration_seconds(sample)))
else:
    check("fixture webm presente", False, f"faltando {sample}")

# ------------------------------------------------------- corpo enviado a API

provider = GeminiOmniProvider.__new__(GeminiOmniProvider)
provider.model = "gemini-omni-1.1-flash"


def body_for(mode: str, **kwargs) -> dict:
    return provider.build_body(VideoRequest(prompt=kwargs.pop("prompt", "x"), mode=mode, **kwargs))


upscale = body_for("upscale", prompt="", resolution="4k", previous_interaction_id="int_1")
check("upscale nao envia video_config", "generation_config" not in upscale, str(upscale.get("generation_config")))
check("upscale mantem a interacao de origem", upscale["previous_interaction_id"] == "int_1")
check("upscale pede a resolucao maior", upscale["response_format"]["resolution"] == "4k")
check("upscale explica a tarefa no texto", "Upscale this video" in upscale["input"][-1]["text"])

extend = body_for("extend", prompt="", previous_interaction_id="int_1")
check("extend usa task extend", extend["generation_config"]["video_config"]["task"] == "extend")
check("extend tem prompt padrao", extend["input"][-1]["text"] == "Continue the scene.")

interp = body_for(
    "interpolate",
    media=[
        MediaInput("image", b"last", "image/png", "last_frame"),
        MediaInput("image", b"first", "image/png", "first_frame"),
    ],
)
check("interpolate usa task image_to_video", interp["generation_config"]["video_config"]["task"] == "image_to_video")
check("interpolate ordena first antes de last", interp["input"][0]["data"] == "Zmlyc3Q=", interp["input"][0]["data"])

seeded = body_for("text_to_video", seed=7)
check("seed vai no generation_config", seeded["generation_config"]["seed"] == 7)

print("\n" + ("FALHAS: " + ", ".join(failures) if failures else "Tudo verde."))
raise SystemExit(1 if failures else 0)
