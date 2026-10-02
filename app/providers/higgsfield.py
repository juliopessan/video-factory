from __future__ import annotations

import random
import re
import time
import uuid
from urllib.parse import urljoin, urlparse

import httpx

from .. import config
from .base import DEFAULT_CAPABILITIES, ProviderError, VideoRequest, VideoResult

API_ROOT = "https://api.higgsfield.ai"
DEFAULT_TEXT_MODEL = "wan/v2.6/text-to-video"
DEFAULT_IMAGE_MODEL = "minimax/hailuo-2.3/standard/image-to-video"
WAN_DURATIONS = (5, 10, 15)
HAILUO_DURATIONS = (6, 10)
POLL_TIMEOUT_SECONDS = 900
POLL_INITIAL_SECONDS = 2.0
POLL_MAX_SECONDS = 10.0
TERMINAL_STATUSES = {"completed", "failed", "nsfw", "canceled"}

# Famílias 3.0, escolhidas pelo caminho do modelo (VF_HIGGSFIELD_TEXT_MODEL /
# VF_HIGGSFIELD_IMAGE_MODEL). Duração, resolução e proporções vêm do catálogo
# de modelos do Higgsfield. O caminho do Kling 3.0 aparece na documentação
# pública; o corpo da requisição destas famílias é INFERIDO (prompt, duration,
# aspect_ratio, resolution, image_url) e ainda não foi exercitado contra a API:
# o primeiro render real confirma. Qualquer outro modelo segue o contrato
# estrito do Wan 2.6 / Hailuo 2.3 acima.
MODEL_PROFILES = {
    "kling-video/v3.0/": {
        "label": "Kling 3.0", "durations": range(3, 16), "resolutions": None,
        "aspects": ("16:9", "9:16", "1:1"),
    },
    "wan/v3.0/": {
        "label": "Wan 3.0", "durations": range(2, 31), "resolutions": ("480p", "720p", "1080p"),
        "aspects": ("16:9", "9:16", "1:1", "4:3", "3:4"),
    },
}


def model_profile(model: str) -> dict | None:
    return next((p for prefix, p in MODEL_PROFILES.items() if model.startswith(prefix)), None)


class HiggsfieldProvider:
    name = "higgsfield"

    def __init__(
        self,
        key_id: str | None = None,
        key_secret: str | None = None,
        text_model: str | None = None,
        image_model: str | None = None,
    ) -> None:
        self.key_id = (key_id if key_id is not None else config.settings.higgsfield_key_id).strip()
        self.key_secret = (
            key_secret if key_secret is not None else config.settings.higgsfield_key_secret
        ).strip()
        self.text_model = (
            text_model or config.settings.higgsfield_text_model or DEFAULT_TEXT_MODEL
        ).strip().strip("/")
        self.image_model = (
            image_model or config.settings.higgsfield_image_model or DEFAULT_IMAGE_MODEL
        ).strip().strip("/")
        if not self.key_id or not self.key_secret:
            raise ProviderError("Configure HF_API_KEY_ID e HF_API_KEY_SECRET para usar o Higgsfield.")
        self._validate_model_endpoint(self.text_model, "text-to-video")
        self._validate_model_endpoint(self.image_model, "image-to-video")

    @staticmethod
    def _validate_model_endpoint(model: str, workflow: str) -> None:
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", model)
            or any(part in {"", ".", ".."} for part in model.split("/"))
            or not model.endswith(f"/{workflow}")
        ):
            raise ProviderError(f"Endpoint Higgsfield inválido para {workflow}.")

    @staticmethod
    def capabilities() -> dict:
        return {
            **DEFAULT_CAPABILITIES,
            "extend": False,
            "reference_video": False,
            "upscale": False,
            "modes": ["text_to_video", "image_to_video"],
            "resolutions": ["720p"],
            "aspect_ratios": ["16:9"],
            "durations": [10],
            "durations_by_mode": {
                "text_to_video": list(WAN_DURATIONS),
                "image_to_video": list(HAILUO_DURATIONS),
            },
        }

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Key {self.key_id}:{self.key_secret}"}

    def build_request(self, request: VideoRequest, image_url: str | None = None) -> tuple[str, dict]:
        if request.mode not in {"text_to_video", "image_to_video"}:
            raise ProviderError(
                "Higgsfield Wan/Hailuo suporta text_to_video e image_to_video; "
                "o encadeamento usa keyframes."
            )
        prompt = request.prompt.strip()
        if not prompt:
            raise ProviderError("O prompt é obrigatório para gerar vídeo no Higgsfield.")
        images = [media for media in request.media if media.kind == "image"]
        model = self.image_model if request.mode == "image_to_video" else self.text_model
        profile = model_profile(model)
        if profile:
            return self._build_profile_request(request, model, profile, prompt, image_url, images)
        if request.aspect_ratio != "16:9":
            raise ProviderError("O workflow Wan/Hailuo configurado aceita apenas 16:9.")

        if request.mode == "image_to_video":
            if len(request.media) != 1 or len(images) != 1 or not image_url:
                raise ProviderError("image_to_video exige exatamente uma imagem de referência.")
            if request.duration_seconds not in HAILUO_DURATIONS:
                raise ProviderError("Hailuo 2.3 Standard aceita duração de 6 ou 10 segundos.")
            if request.resolution != "720p":
                raise ProviderError("Hailuo 2.3 Standard gera em 768P fixo; use 720p no app.")
            if images[0].mime_type.lower() not in {
                "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
            }:
                raise ProviderError("Tipo de imagem não suportado pelo upload do Higgsfield.")
            url = f"{API_ROOT}/{self.image_model}"
            payload = {
                "prompt": prompt,
                "duration": request.duration_seconds,
                "image_url": image_url,
                "prompt_optimizer": True,
            }
        else:
            if request.duration_seconds not in WAN_DURATIONS:
                raise ProviderError("Wan 2.6 aceita duração de 5, 10 ou 15 segundos.")
            if request.resolution not in {"720p", "1080p"}:
                raise ProviderError("Wan 2.6 aceita resolução 720p ou 1080p.")
            if request.media:
                raise ProviderError("text_to_video não aceita mídia de referência.")
            url = f"{API_ROOT}/{self.text_model}"
            payload = {
                "prompt": prompt,
                "duration": request.duration_seconds,
                "resolution": request.resolution,
                "multi_shots": False,
                "prompt_extend": False,
            }
            if request.seed is not None:
                payload["seed"] = request.seed
        return url, payload

    @staticmethod
    def _build_profile_request(request, model, profile, prompt, image_url, images) -> tuple[str, dict]:
        """Corpo das famílias 3.0 (ver MODEL_PROFILES: campos inferidos)."""
        label = profile["label"]
        if request.duration_seconds not in profile["durations"]:
            low, high = profile["durations"][0], profile["durations"][-1]
            raise ProviderError(f"{label} aceita duração de {low} a {high} segundos.")
        if request.aspect_ratio not in profile["aspects"]:
            raise ProviderError(
                f"{label} aceita as proporções {', '.join(profile['aspects'])}; recebeu {request.aspect_ratio}."
            )
        payload = {"prompt": prompt, "duration": request.duration_seconds, "aspect_ratio": request.aspect_ratio}
        if profile["resolutions"]:
            if request.resolution not in profile["resolutions"]:
                raise ProviderError(f"{label} aceita as resoluções {', '.join(profile['resolutions'])}.")
            payload["resolution"] = request.resolution
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.mode == "image_to_video":
            if len(images) != 1 or not image_url:
                raise ProviderError("image_to_video exige exatamente uma imagem de referência.")
            payload["image_url"] = image_url
        elif request.media:
            raise ProviderError("text_to_video não aceita mídia de referência.")
        return f"{API_ROOT}/{model}", payload

    def _upload_image(self, client: httpx.Client, image) -> str:
        content_type = image.mime_type or "image/jpeg"
        upload = self._json(
            client.post(
                f"{API_ROOT}/files/generate-upload-url",
                headers={**self.headers, "Content-Type": "application/json"},
                json={"content_type": content_type},
            ),
            "solicitar URL de upload",
        )
        upload_url = str(upload.get("upload_url") or "")
        public_url = str(upload.get("public_url") or "")
        upload_headers = upload.get("upload_headers")
        if not upload_url or not public_url or not isinstance(upload_headers, dict):
            raise ProviderError("Resposta de upload do Higgsfield incompleta.")
        if urlparse(upload_url).scheme != "https":
            raise ProviderError("O Higgsfield retornou uma URL de upload que não usa HTTPS.")

        response = client.put(upload_url, headers=upload_headers, content=image.data)
        if response.status_code >= 400:
            raise ProviderError(f"Upload de referência falhou: HTTP {response.status_code}.")
        return public_url

    def _await_completion(self, client: httpx.Client, submitted: dict) -> dict:
        status_url = str(submitted.get("status_url") or "")
        status_url = self._validated_status_url(str(submitted.get("status_url") or ""))
        status = str(submitted.get("status") or "queued").lower()
        result = submitted
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        delay = POLL_INITIAL_SECONDS
        while status not in TERMINAL_STATUSES:
            if time.monotonic() >= deadline:
                raise ProviderError(f"Tempo esgotado ({POLL_TIMEOUT_SECONDS}s) aguardando o Higgsfield.")
            time.sleep(delay + random.uniform(0, 0.5))
            result = self._json(
                client.get(status_url, headers=self.headers),
                "consultar status",
            )
            status = str(result.get("status") or "").lower()
            delay = min(delay * 1.5, POLL_MAX_SECONDS)

        if status != "completed":
            if status == "nsfw":
                raise ProviderError("Higgsfield recusou a geração pela moderação de conteúdo (nsfw).")
            detail = result.get("error")
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("detail") or detail.get("code")
            suffix = f": {str(detail)[:240]}" if detail else "."
            raise ProviderError(f"Higgsfield terminou com status '{status}'{suffix}")
        return result

    @staticmethod
    def _validated_status_url(value: str) -> str:
        parsed = urlparse(value)
        if not parsed.scheme and not parsed.netloc and parsed.path.startswith("/requests/"):
            value = urljoin(API_ROOT, value)
            parsed = urlparse(value)
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.higgsfield.ai"
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or not parsed.path.startswith("/requests/")
        ):
            raise ProviderError("Resposta do Higgsfield sem status_url HTTPS válida.")
        return value

    @staticmethod
    def _json(response: httpx.Response, action: str) -> dict:
        if response.status_code >= 400:
            if response.status_code == 401:
                detail = "Verifique HF_API_KEY_ID e HF_API_KEY_SECRET."
            elif response.status_code == 403:
                detail = "A conta não tem créditos suficientes ou acesso a este modelo."
            else:
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                message = body.get("detail") or body.get("error") if isinstance(body, dict) else None
                detail = f" {str(message)[:240]}" if message else ""
            raise ProviderError(f"Higgsfield: falha ao {action} (HTTP {response.status_code}).{detail}")
        try:
            result = response.json()
        except ValueError as exc:
            raise ProviderError(f"Higgsfield: resposta não-JSON ao {action}.") from exc
        if not isinstance(result, dict):
            raise ProviderError(f"Higgsfield: resposta inválida ao {action}.")
        return result

    def generate(self, request: VideoRequest) -> VideoResult:
        try:
            with httpx.Client(timeout=120, follow_redirects=False) as client:
                images = [media for media in request.media if media.kind == "image"]
                image_url = (
                    self._upload_image(client, images[0])
                    if request.mode == "image_to_video" and images
                    else None
                )
                url, payload = self.build_request(request, image_url=image_url)
                submitted = self._json(
                    client.post(
                        url,
                        headers={
                            **self.headers,
                            "Content-Type": "application/json",
                            "Idempotency-Key": str(uuid.uuid4()),
                        },
                        json=payload,
                    ),
                    "enviar geração",
                )
                result = self._await_completion(client, submitted)
                video = result.get("video")
                video_url = str(video.get("url") or "") if isinstance(video, dict) else ""
                if urlparse(video_url).scheme != "https":
                    raise ProviderError("Resposta concluída do Higgsfield sem URL HTTPS do vídeo.")

                content = client.get(video_url, timeout=120)
                if content.status_code >= 400:
                    raise ProviderError(f"Falha ao baixar vídeo do Higgsfield: HTTP {content.status_code}.")
                return VideoResult(
                    interaction_id=str(result.get("request_id") or submitted.get("request_id") or "") or None,
                    data=content.content,
                    mime_type=content.headers.get("content-type", "video/mp4").split(";")[0],
                    raw_status="completed",
                )
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError(f"Falha de rede ao chamar Higgsfield: {type(exc).__name__}.") from exc