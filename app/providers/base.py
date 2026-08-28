"""Contrato comum entre o provider real (Gemini Omni) e o mock offline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

Mode = Literal["text_to_video", "image_to_video", "interpolate", "reference_to_video", "extend", "upscale"]

# Mapeia o modo da UI para o `generation_config.video_config.task` da API.
# `None` = nao enviar `video_config`: a API entao determina o modo pelo texto e
# pela midia de entrada. E o caso do upscale, que a documentacao anuncia como
# recurso mas sem nomear uma task propria — aqui ele e um novo render da mesma
# interacao (`previous_interaction_id`) pedindo uma resolucao maior.
TASK_BY_MODE: dict[str, str | None] = {
    "text_to_video": "text_to_video",
    "image_to_video": "image_to_video",
    "interpolate": "image_to_video",
    "reference_to_video": "reference_to_video",
    "extend": "extend",
    "upscale": None,
}


@dataclass
class MediaInput:
    """Uma imagem ou video anexado ao prompt."""

    kind: Literal["image", "video"]
    data: bytes
    mime_type: str
    role: str = "reference"  # first_frame | last_frame | reference


@dataclass
class VideoRequest:
    prompt: str
    mode: Mode
    resolution: str = "720p"
    aspect_ratio: str = "16:9"
    duration_seconds: int = 8
    previous_interaction_id: str | None = None
    media: list[MediaInput] = field(default_factory=list)
    seed: int | None = None


@dataclass
class VideoResult:
    interaction_id: str | None
    data: bytes
    mime_type: str
    raw_status: str = "completed"


class ProviderError(RuntimeError):
    """Falha vinda do provider (rede, quota, conteudo recusado)."""


class VideoProvider(Protocol):
    name: str

    def generate(self, request: VideoRequest) -> VideoResult: ...
