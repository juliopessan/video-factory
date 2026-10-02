"""Provider Higgsfield: Wan 3.0 e Kling 3.0 pela API REST (https://api.higgsfield.ai).

Ciclo assíncrono da API: `POST /<endpoint-id>` devolve `request_id` + `status_url`;
consulta-se o status até um estado terminal (`completed`, `failed`, `nsfw`,
`canceled`) e o MP4 está em `video.url`. Imagem de referência sobe antes por URL
pré-assinada (`/files/generate-upload-url`).

O que foi confirmado na documentação pública e o que não foi:
- Wan 3.0: os endpoints `alibaba/wan-3.0/text-to-video` e `alibaba/wan-3.0/reference-to-video`
  e os campos do corpo (`prompt`, `duration`, `resolution`, `aspect_ratio`,
  `generate_audio`, `image_urls`) estão documentados.
- Kling 3.0: o catálogo do Higgsfield o lista, mas a referência REST pública não traz o
  endpoint id. Por isso ele é configurável (`VF_HIGGSFIELD_KLING_T2V` e
  `VF_HIGGSFIELD_KLING_I2V`) e, sem isso, o provider recusa com instrução clara em vez
  de chutar um caminho. Pegue o id em https://console.higgsfield.ai.
- O campo de tipo na criação da URL de upload (`content_type`) não foi confirmado.

Credenciais, na ordem: `HF_API_KEY_ID` + `HF_API_KEY_SECRET`, `HF_KEY` ("id:secret") ou
`HIGGSFIELD_API_KEY` ("id:secret"). Nunca vão para log nem para mensagem de erro.

Capacidades: não estende cena; a continuidade do pipeline sai do último frame da peça
anterior (ver `pipeline._chain_media`), por referência de imagem.
"""
from __future__ import annotations

import os
import random
import time
import uuid
from typing import Any

from .base import ProviderError, VideoRequest, VideoResult

BASE_URL = "https://api.higgsfield.ai"
POLL_START_SECONDS = 2.0
POLL_MAX_SECONDS = 10.0
POLL_TIMEOUT_SECONDS = 1800

TERMINAL = {"completed", "failed", "nsfw", "canceled"}

ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4")
RESOLUTIONS = ("480p", "720p", "1080p")
MIN_SECONDS, MAX_SECONDS = 2, 30

# Modelos expostos. `t2v`/`i2v` são endpoint ids; `None` = vem do ambiente.
MODELS: dict[str, dict[str, Any]] = {
    "wan3": {
        "label": "Wan 3.0",
        "t2v": "alibaba/wan-3.0/text-to-video",
        "i2v": "alibaba/wan-3.0/reference-to-video",
        "env_t2v": "VF_HIGGSFIELD_WAN_T2V",
        "env_i2v": "VF_HIGGSFIELD_WAN_I2V",
        "extra": {"enable_thinking": False},
    },
    "kling3": {
        "label": "Kling 3.0",
        "t2v": None,
        "i2v": None,
        "env_t2v": "VF_HIGGSFIELD_KLING_T2V",
        "env_i2v": "VF_HIGGSFIELD_KLING_I2V",
        "extra": {},
    },
}
DEFAULT_MODEL = "wan3"


def credentials_from_env() -> str:
    """Devolve `id:secret` ou string vazia."""
    key_id = os.environ.get("HF_API_KEY_ID", "").strip()
    secret = os.environ.get("HF_API_KEY_SECRET", "").strip()
    if key_id and secret:
        return f"{key_id}:{secret}"
    return (os.environ.get("HF_KEY") or os.environ.get("HIGGSFIELD_API_KEY") or "").strip()


def clamp_seconds(seconds: int) -> int:
    return max(MIN_SECONDS, min(MAX_SECONDS, int(seconds)))


class HiggsfieldProvider:
    name = "higgsfield"

    def __init__(
        self,
        credentials: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        sleep=time.sleep,
    ) -> None:
        self.credentials = (credentials or credentials_from_env()).strip()
        if ":" not in self.credentials:
            raise ProviderError(
                "Configure HF_API_KEY_ID e HF_API_KEY_SECRET (ou HIGGSFIELD_API_KEY=\"id:secret\") "
                "para usar o Higgsfield. As chaves ficam em https://cloud.higgsfield.ai."
            )
        self.model = (model or os.environ.get("VF_HIGGSFIELD_MODEL") or DEFAULT_MODEL).strip().lower()
        if self.model not in MODELS:
            raise ProviderError(
                f"Modelo Higgsfield desconhecido: {self.model}. Use {', '.join(MODELS)}."
            )
        self.base_url = (base_url or os.environ.get("VF_HIGGSFIELD_BASE_URL") or BASE_URL).rstrip("/")
        self._sleep = sleep

    # -- capacidades -----------------------------------------------------------

    @staticmethod
    def capabilities() -> dict[str, Any]:
        return {
            "extend": False,           # sem previous_interaction_id: encadeia por keyframe
            "reference_video": False,  # a referência usada aqui é imagem
            "upscale": False,
            "resolutions": list(RESOLUTIONS),
            "aspect_ratios": list(ASPECT_RATIOS),
            "durations": [],           # 2 a 30s, duração livre
        }

    # -- montagem da requisição ------------------------------------------------

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Key {self.credentials}", "Content-Type": "application/json"}

    def endpoint_id(self, with_image: bool) -> str:
        spec = MODELS[self.model]
        kind = "i2v" if with_image else "t2v"
        endpoint = os.environ.get(spec[f"env_{kind}"], "").strip() or spec[kind]
        if not endpoint:
            raise ProviderError(
                f"O endpoint {kind} do {spec['label']} não está configurado. Copie o endpoint id "
                f"do modelo em https://console.higgsfield.ai e defina {spec[f'env_{kind}']}."
            )
        return endpoint.strip("/")

    def build_payload(self, request: VideoRequest, image_urls: list[str] | None = None) -> dict[str, Any]:
        if request.aspect_ratio not in ASPECT_RATIOS:
            raise ProviderError(
                f"{MODELS[self.model]['label']} aceita {', '.join(ASPECT_RATIOS)}; recebeu {request.aspect_ratio}."
            )
        resolution = request.resolution if request.resolution in RESOLUTIONS else "720p"
        payload: dict[str, Any] = {
            "prompt": request.prompt.strip(),
            "duration": clamp_seconds(request.duration_seconds),
            "resolution": resolution,
            "aspect_ratio": request.aspect_ratio,
            "generate_audio": True,
            **MODELS[self.model]["extra"],
        }
        if request.seed is not None:
            payload["seed"] = int(request.seed)
        if image_urls:
            payload["image_urls"] = image_urls
        return payload

    # -- execução --------------------------------------------------------------

    def generate(self, request: VideoRequest) -> VideoResult:
        import httpx

        if request.mode == "extend":
            raise ProviderError(
                "O Higgsfield não estende cena a partir de outra geração. Use o encadeamento por "
                "keyframe (o pipeline faz isso sozinho) ou o provider gemini."
            )
        images = [m for m in request.media if m.kind == "image"]
        # Kling/Wan aqui só recebem imagem: vídeo de referência é descartado de propósito.
        endpoint = self.endpoint_id(with_image=bool(images))

        with httpx.Client(timeout=120) as client:
            image_urls = [self._upload(client, m.data, m.mime_type or "image/png") for m in images]
            payload = self.build_payload(request, image_urls)
            headers = {**self.headers, "Idempotency-Key": str(uuid.uuid4())}
            job = self._json(
                client.post(f"{self.base_url}/{endpoint}", headers=headers, json=payload),
                "criar o vídeo",
            )
            job = self._await_completion(client, job)
            url = (job.get("video") or {}).get("url")
            if not url:
                raise ProviderError(f"Resposta concluída sem video.url: {str(job)[:200]}")
            # a URL do CDN é pública/assinada: sem o cabeçalho de autorização
            content = client.get(url, follow_redirects=True)
            if content.status_code >= 400:
                raise ProviderError(f"Falha ao baixar o vídeo: HTTP {content.status_code}")
            return VideoResult(
                interaction_id=job.get("request_id"),
                data=content.content,
                mime_type=content.headers.get("content-type", "video/mp4").split(";")[0],
                raw_status=str(job.get("status", "completed")),
            )

    def _upload(self, client, data: bytes, mime_type: str) -> str:
        """Sobe uma imagem por URL pré-assinada e devolve a `public_url`."""
        ticket = self._json(
            client.post(
                f"{self.base_url}/files/generate-upload-url",
                headers=self.headers,
                json={"content_type": mime_type},
            ),
            "preparar o upload",
        )
        upload_url, public_url = ticket.get("upload_url"), ticket.get("public_url")
        if not upload_url or not public_url:
            raise ProviderError(f"Resposta de upload sem upload_url/public_url: {str(ticket)[:200]}")
        put = client.put(upload_url, headers=ticket.get("upload_headers") or {"Content-Type": mime_type}, content=data)
        if put.status_code >= 400:
            raise ProviderError(f"Falha no upload da imagem: HTTP {put.status_code}")
        return public_url

    def _await_completion(self, client, job: dict) -> dict:
        status_url = job.get("status_url")
        if not status_url:
            raise ProviderError(f"Resposta sem status_url: {str(job)[:200]}")
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        interval = POLL_START_SECONDS
        while str(job.get("status", "")).lower() not in TERMINAL:
            if time.monotonic() > deadline:
                raise ProviderError(f"Tempo esgotado ({POLL_TIMEOUT_SECONDS}s) aguardando o Higgsfield.")
            # 2s crescendo até 10s, com jitter para não sincronizar workers
            self._sleep(interval + random.uniform(0, 0.5))
            interval = min(POLL_MAX_SECONDS, interval * 1.5)
            job = self._json(client.get(status_url, headers=self.headers), "consultar o job")
        status = str(job.get("status", "")).lower()
        if status == "nsfw":
            raise ProviderError("Higgsfield recusou o conteúdo (nsfw). Reescreva o prompt da peça.")
        if status in {"failed", "canceled"}:
            raise ProviderError(f"Higgsfield terminou com status '{status}'. {job.get('error') or ''}".strip())
        return job

    @staticmethod
    def _json(response, action: str) -> dict:
        if response.status_code == 401:
            raise ProviderError(f"Falha ao {action}: credenciais recusadas (HTTP 401).")
        if response.status_code >= 400:
            # o corpo pode explicar plano/crédito; a chave nunca está nele
            raise ProviderError(f"Falha ao {action}: HTTP {response.status_code} — {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"Resposta não-JSON ao {action}: {response.text[:200]}") from exc
