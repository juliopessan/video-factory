"""Provider alternativo: Sora-2 no Microsoft Foundry (Azure OpenAI).

Cinco endpoints, conforme a ficha do modelo no Foundry: criar vídeo, consultar
status, baixar o MP4, listar e apagar. A API está em preview e apareceu em duas
formas — `/openai/v1/videos` (estilo OpenAI v1) e `/openai/v1/video/generations`
(estilo antigo do Azure) — por isso o caminho é configurável em `VF_AZURE_API_STYLE`.

Diferenças de capacidade em relação ao Omni, tratadas em `capabilities()`:
- não estende cena por `previous_interaction_id`; a continuidade do pipeline sai
  do último frame da peça anterior (ver `pipeline._chain_media`);
- durações fixas (4, 8 ou 12 segundos) e dois tamanhos (1280x720, 720x1280);
- edita vídeo gerado por *remix*, o que aqui vira o modo `edit`.
"""
from __future__ import annotations

import os
import time
from typing import Any

from .base import ProviderError, VideoRequest, VideoResult

DEFAULT_API_VERSION = "preview"
DEFAULT_DEPLOYMENT = "sora-2"
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 1800

# Sora-2 aceita apenas estes tamanhos; a proporção decide qual usar.
SIZE_BY_ASPECT = {"16:9": "1280x720", "9:16": "720x1280"}
ALLOWED_SECONDS = (4, 8, 12)

DONE = {"completed", "succeeded"}
PENDING = {"queued", "in_progress", "preprocessing", "running", "processing"}


def nearest_seconds(seconds: int) -> int:
    """Sora-2 só gera 4, 8 ou 12 segundos: escolhe o mais próximo."""
    return min(ALLOWED_SECONDS, key=lambda option: (abs(option - seconds), option))


class AzureSoraProvider:
    name = "azure"

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        deployment: str | None = None,
        api_version: str | None = None,
        api_style: str | None = None,
    ) -> None:
        self.endpoint = (endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")).strip()
        self.endpoint = self.endpoint.rstrip("/").removesuffix("/openai").rstrip("/")
        self.api_key = (api_key or os.environ.get("AZURE_OPENAI_API_KEY", "")).strip()
        self.deployment = (deployment or os.environ.get("VF_AZURE_DEPLOYMENT") or DEFAULT_DEPLOYMENT).strip()
        self.api_version = (api_version or os.environ.get("VF_AZURE_API_VERSION") or DEFAULT_API_VERSION).strip()
        self.api_style = (api_style or os.environ.get("VF_AZURE_API_STYLE") or "videos").strip()
        if not self.endpoint or not self.api_key:
            raise ProviderError(
                "Configure AZURE_OPENAI_ENDPOINT e AZURE_OPENAI_API_KEY para usar o Sora-2 no Foundry."
            )

    # -- capacidades -----------------------------------------------------------

    @staticmethod
    def capabilities() -> dict[str, Any]:
        return {
            "extend": False,           # sem previous_interaction_id: encadeia por keyframe
            "reference_video": False,  # referência é imagem (input_reference)
            "upscale": False,
            "resolutions": ["720p"],
            "aspect_ratios": list(SIZE_BY_ASPECT),
            "durations": list(ALLOWED_SECONDS),
        }

    # -- montagem da requisicao ------------------------------------------------

    @property
    def base_path(self) -> str:
        suffix = "videos" if self.api_style == "videos" else "video/generations"
        return f"{self.endpoint}/openai/v1/{suffix}"

    def url(self, *parts: str) -> str:
        path = "/".join([self.base_path, *[p for p in parts if p]])
        return f"{path}?api-version={self.api_version}"

    @property
    def headers(self) -> dict[str, str]:
        # o gateway do Foundry aceita as duas formas; mandar as duas evita
        # depender de qual esta ativa no recurso
        return {"api-key": self.api_key, "Authorization": f"Bearer {self.api_key}"}

    def build_payload(self, request: VideoRequest) -> dict[str, Any]:
        size = SIZE_BY_ASPECT.get(request.aspect_ratio)
        if not size:
            raise ProviderError(
                f"Sora-2 aceita apenas {', '.join(SIZE_BY_ASPECT)}; recebeu {request.aspect_ratio}."
            )
        payload: dict[str, Any] = {
            "model": self.deployment,
            "prompt": request.prompt.strip(),
            "size": size,
            "seconds": str(nearest_seconds(request.duration_seconds)),
        }
        if request.mode == "edit" and request.previous_interaction_id:
            # "remixing": edita um vídeo já gerado
            payload["remix_video_id"] = request.previous_interaction_id
        return payload

    # -- execucao --------------------------------------------------------------

    def generate(self, request: VideoRequest) -> VideoResult:
        import httpx

        if request.mode == "extend":
            raise ProviderError(
                "Sora-2 não estende cena a partir de outra geração. Use o encadeamento por "
                "keyframe (o pipeline faz isso sozinho) ou o provider gemini."
            )
        payload = self.build_payload(request)
        images = [m for m in request.media if m.kind == "image"]

        with httpx.Client(timeout=120) as client:
            if images:
                files = {
                    "input_reference": (
                        "reference.png", images[0].data, images[0].mime_type or "image/png",
                    )
                }
                response = client.post(self.url(), headers=self.headers, data=payload, files=files)
            else:
                response = client.post(self.url(), headers=self.headers, json=payload)
            job = self._json(response, "criar o vídeo")
            job = self._await_completion(client, job)
            video_id = job.get("id") or (job.get("generations") or [{}])[0].get("id")
            if not video_id:
                raise ProviderError(f"Resposta sem id de vídeo: {str(job)[:200]}")
            content = client.get(self.url(video_id, "content"), headers=self.headers)
            if content.status_code >= 400:
                # o estilo antigo separa o conteúdo por tipo
                content = client.get(self.url(video_id, "content", "video"), headers=self.headers)
            if content.status_code >= 400:
                raise ProviderError(f"Falha ao baixar o vídeo: HTTP {content.status_code}")
            return VideoResult(
                interaction_id=video_id,
                data=content.content,
                mime_type=content.headers.get("content-type", "video/mp4").split(";")[0],
                raw_status=str(job.get("status", "completed")),
            )

    def _await_completion(self, client, job: dict) -> dict:
        job_id = job.get("id")
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        while str(job.get("status", "")).lower() in PENDING:
            if time.monotonic() > deadline:
                raise ProviderError(f"Tempo esgotado ({POLL_TIMEOUT_SECONDS}s) aguardando o Sora-2.")
            time.sleep(POLL_INTERVAL_SECONDS)
            job = self._json(client.get(self.url(job_id), headers=self.headers), "consultar o job")
        status = str(job.get("status", "")).lower()
        if status and status not in DONE:
            detail = job.get("error") or job.get("failure_reason") or ""
            raise ProviderError(f"Sora-2 terminou com status '{status}'. {detail}")
        return job

    @staticmethod
    def _json(response, action: str) -> dict:
        if response.status_code >= 400:
            raise ProviderError(f"Falha ao {action}: HTTP {response.status_code} — {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"Resposta não-JSON ao {action}: {response.text[:200]}") from exc

    # -- housekeeping (os outros dois endpoints da ficha do modelo) -----------

    def list_videos(self, limit: int = 20) -> list[dict]:
        import httpx

        with httpx.Client(timeout=60) as client:
            payload = self._json(
                client.get(f"{self.url()}&limit={limit}", headers=self.headers), "listar os vídeos"
            )
        return payload.get("data") or payload.get("videos") or []

    def delete_video(self, video_id: str) -> None:
        import httpx

        with httpx.Client(timeout=60) as client:
            response = client.delete(self.url(video_id), headers=self.headers)
        if response.status_code >= 400:
            raise ProviderError(f"Falha ao apagar {video_id}: HTTP {response.status_code}")
