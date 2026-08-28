"""Provider real: Gemini Omni 1.1 Flash via `client.interactions`."""
from __future__ import annotations

import base64
import time

from ..config import settings
from .base import DEFAULT_CAPABILITIES, TASK_BY_MODE, ProviderError, VideoRequest, VideoResult

POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 900
PENDING = {"queued", "in_progress", "requires_action"}


class GeminiOmniProvider:
    name = "gemini"

    @staticmethod
    def capabilities() -> dict:
        return dict(DEFAULT_CAPABILITIES)

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.api_key
        self.model = model or settings.model
        if not self.api_key:
            raise ProviderError("GEMINI_API_KEY nao configurada.")
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - dependencia declarada
            raise ProviderError("Pacote google-genai nao instalado.") from exc
        self._client = genai.Client(api_key=self.api_key)

    # -- construcao do payload -------------------------------------------------

    def build_input(self, request: VideoRequest) -> list[dict]:
        """Ordem importa: frames/refs primeiro, texto por ultimo.

        Para interpolacao o primeiro frame vem antes do ultimo e o prompt e
        prefixado para deixar o papel de cada imagem explicito ao modelo.
        """
        blocks: list[dict] = []
        ordered = sorted(
            request.media,
            key=lambda m: {"first_frame": 0, "last_frame": 1}.get(m.role, 2),
        )
        for item in ordered:
            blocks.append(
                {
                    "type": item.kind,
                    "data": base64.b64encode(item.data).decode("ascii"),
                    "mime_type": item.mime_type,
                }
            )

        prompt = request.prompt.strip()
        if request.mode == "interpolate":
            prompt = (
                "A primeira imagem e o frame inicial e a segunda e o frame final. "
                "Gere uma unica tomada continua entre os dois frames. " + prompt
            )
        elif request.mode == "extend" and not prompt:
            prompt = "Continue the scene."
        elif request.mode == "upscale":
            prompt = prompt or (
                "Upscale this video to the requested output resolution. Keep the same scene, "
                "framing, motion and duration; only increase detail and sharpness."
            )
        blocks.append({"type": "text", "text": prompt})
        return blocks

    def build_body(self, request: VideoRequest) -> dict:
        response_format = {
            "type": "video",
            "resolution": request.resolution,
            "aspect_ratio": request.aspect_ratio,
            "duration": f"{request.duration_seconds}s",
            "delivery": "inline",
        }
        generation_config: dict = {}
        task = TASK_BY_MODE[request.mode]
        # A API recusa `video_config.task` junto de `previous_interaction_id`.
        if task is not None and not request.previous_interaction_id:
            generation_config["video_config"] = {"task": task}
        body: dict = {
            "model": self.model,
            "input": self.build_input(request),
            "response_format": response_format,
        }
        if generation_config:
            body["generation_config"] = generation_config
        if request.previous_interaction_id:
            body["previous_interaction_id"] = request.previous_interaction_id
        if request.seed is not None:
            body.setdefault("generation_config", {})["seed"] = request.seed
        return body

    # -- execucao --------------------------------------------------------------

    def generate(self, request: VideoRequest) -> VideoResult:
        try:
            interaction = self._client.interactions.create(**self.build_body(request))
        except Exception as exc:  # a SDK levanta tipos variados de erro HTTP
            raise ProviderError(f"Falha ao criar a interacao: {exc}") from exc

        interaction = self._await_completion(interaction)
        status = str(getattr(interaction, "status", "") or "")
        if status != "completed":
            raise ProviderError(self._describe_failure(interaction, status))

        video = getattr(interaction, "output_video", None)
        if video is None:
            raise ProviderError("A interacao terminou sem video no output_video.")
        data = self._extract_bytes(video)
        return VideoResult(
            interaction_id=getattr(interaction, "id", None),
            data=data,
            mime_type=str(getattr(video, "mime_type", None) or "video/mp4"),
            raw_status=status,
        )

    def _await_completion(self, interaction):
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        while str(getattr(interaction, "status", "")) in PENDING:
            if time.monotonic() > deadline:
                raise ProviderError(
                    f"Tempo esgotado ({POLL_TIMEOUT_SECONDS}s) aguardando a geracao."
                )
            time.sleep(POLL_INTERVAL_SECONDS)
            try:
                interaction = self._client.interactions.get(getattr(interaction, "id"))
            except Exception as exc:
                raise ProviderError(f"Falha ao consultar a interacao: {exc}") from exc
        return interaction

    @staticmethod
    def _describe_failure(interaction, status: str) -> str:
        errors = getattr(interaction, "errors", None) or []
        detail = "; ".join(str(getattr(e, "message", e)) for e in errors)
        return f"Geracao terminou com status '{status}'" + (f": {detail}" if detail else ".")

    @staticmethod
    def _extract_bytes(video) -> bytes:
        data = getattr(video, "data", None)
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return base64.b64decode(data)
        uri = getattr(video, "uri", None)
        if uri:
            import urllib.request

            with urllib.request.urlopen(uri) as response:  # noqa: S310 - URI do proprio provider
                return response.read()
        raise ProviderError("Resposta sem bytes de video nem URI.")
