"""Offline tests for the Higgsfield video provider."""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-higgsfield-"))

import httpx  # noqa: E402

from app import config as config_module, providers  # noqa: E402
from app.main import read_config  # noqa: E402
from app.providers.base import MediaInput, ProviderError, VideoRequest  # noqa: E402
from app.providers.higgsfield import HiggsfieldProvider  # noqa: E402

failures: list[str] = []
calls: list[httpx.Request] = []
polls: dict[str, int] = {}
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


def make_provider() -> HiggsfieldProvider:
    return HiggsfieldProvider(key_id="test-id", key_secret="test-secret")


provider = make_provider()
url, payload = provider.build_request(
    VideoRequest(prompt="A calm ocean", mode="text_to_video", duration_seconds=10)
)
check("text endpoint uses Wan 2.6", url.endswith("/wan/v2.6/text-to-video"), url)
check("text body follows documented schema", payload == {
    "prompt": "A calm ocean", "duration": 10, "resolution": "720p",
    "multi_shots": False, "prompt_extend": False,
}, str(payload))

image = MediaInput("image", b"image-data", "image/png", "first_frame")
url, payload = provider.build_request(
    VideoRequest(prompt="Continue", mode="image_to_video", duration_seconds=10, media=[image]),
    image_url="https://cdn.higgsfield.test/frame.png",
)
check("image endpoint uses Hailuo and uploaded public URL",
    url.endswith("/minimax/hailuo-2.3/standard/image-to-video")
    and payload.get("image_url") == "https://cdn.higgsfield.test/frame.png"
    and payload.get("prompt_optimizer") is True, str(payload))
check("provider signals keyframe chaining", HiggsfieldProvider.capabilities()["extend"] is False)
check("provider exposes model-specific durations",
    HiggsfieldProvider.capabilities()["durations_by_mode"] == {
        "text_to_video": [5, 10, 15], "image_to_video": [6, 10]
    })
check("official status URL is accepted",
    provider._validated_status_url("https://api.higgsfield.ai/requests/test/status")
    == "https://api.higgsfield.ai/requests/test/status")
check("relative status URL is normalized",
    provider._validated_status_url("/requests/test/status")
    == "https://api.higgsfield.ai/requests/test/status")
check("default HTTPS port is accepted",
    provider._validated_status_url("https://api.higgsfield.ai:443/requests/test/status")
    == "https://api.higgsfield.ai:443/requests/test/status")
try:
    provider._validated_status_url("https://evil.example/requests/test/status")
    check("external status host is rejected", False, "no error")
except ProviderError:
    check("external status host is rejected", True)

environment_names = (
    "VF_PROVIDER", "GEMINI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
    "HF_API_KEY_ID", "HF_API_KEY_SECRET", "VF_HIGGSFIELD_TEXT_MODEL",
    "VF_HIGGSFIELD_IMAGE_MODEL",
)
original_environment = {name: os.environ.get(name) for name in environment_names}
original_settings = config_module.settings
original_cache = providers._cache.copy()
try:
    os.environ.update({
        "VF_PROVIDER": "auto",
        "GEMINI_API_KEY": "",
        "AZURE_OPENAI_ENDPOINT": "",
        "AZURE_OPENAI_API_KEY": "",
        "HF_API_KEY_ID": "test-id",
        "HF_API_KEY_SECRET": "test-secret",
        "VF_HIGGSFIELD_TEXT_MODEL": "wan/v2.6/text-to-video",
        "VF_HIGGSFIELD_IMAGE_MODEL": "minimax/hailuo-2.3/standard/image-to-video",
    })
    config_module.settings = config_module.get_settings()
    providers._cache.clear()
    config_response = read_config()
    check("auto seleciona Higgsfield quando só ele tem credenciais",
          config_module.settings.effective_provider == "higgsfield")
    check("registry instancia o provider Higgsfield",
          isinstance(providers.get_provider(), HiggsfieldProvider))
    check("API anuncia o alvo de resolução comum",
          config_response["resolutions"] == ["720p"], str(config_response["resolutions"]))
    check("API expõe os dois endpoint IDs",
          config_response["higgsfield_text_model"] == "wan/v2.6/text-to-video"
          and config_response["higgsfield_image_model"] == "minimax/hailuo-2.3/standard/image-to-video")
    check("API não devolve credenciais Higgsfield",
          "higgsfield_key_id" not in config_response and "higgsfield_key_secret" not in config_response)
finally:
    config_module.settings = original_settings
    providers._cache.clear()
    providers._cache.update(original_cache)
    for name, value in original_environment.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

try:
    provider.build_request(VideoRequest(prompt="x", mode="text_to_video", duration_seconds=20))
    check("unsupported duration is rejected before API call", False, "no error")
except ProviderError:
    check("unsupported duration is rejected before API call", True)

    try:
        HiggsfieldProvider(
            key_id="test-id", key_secret="test-secret",
            text_model="../../other-host/text-to-video",
        )
        check("model path traversal is rejected", False, "no error")
    except ProviderError:
        check("model path traversal is rejected", True)

    try:
        provider.build_request(VideoRequest(prompt="Continue", mode="image_to_video", duration_seconds=8,
                                            media=[image]),
                               image_url="https://cdn.higgsfield.test/frame.png")
        check("unsupported Hailuo duration is rejected", False, "no error")
    except ProviderError:
        check("unsupported Hailuo duration is rejected", True)

    try:
        provider.build_request(
            VideoRequest(prompt="Continue", mode="image_to_video", duration_seconds=10,
                         media=[MediaInput("image", b"image-data", "image/bmp")]),
            image_url="https://cdn.higgsfield.test/frame.bmp",
        )
        check("unsupported image MIME is rejected", False, "no error")
    except ProviderError:
        check("unsupported image MIME is rejected", True)


def handler(request: httpx.Request) -> httpx.Response:
    calls.append(request)
    path = request.url.path
    if request.method == "POST" and path == "/files/generate-upload-url":
        return httpx.Response(200, json={
            "public_url": "https://cdn.higgsfield.test/frame.png",
            "upload_url": "https://storage.higgsfield.test/upload",
            "upload_headers": {"Content-Type": "image/png"},
        })
    if request.method == "PUT" and path == "/upload":
        return httpx.Response(200)
    if request.method == "POST" and path == "/wan/v2.6/text-to-video":
        return httpx.Response(200, json={
            "status": "queued", "request_id": "text-1",
            "status_url": "https://api.higgsfield.ai/requests/text-1/status",
        })
    if request.method == "POST" and path == "/minimax/hailuo-2.3/standard/image-to-video":
        return httpx.Response(200, json={
            "status": "queued", "request_id": "image-1",
            "status_url": "https://api.higgsfield.ai/requests/image-1/status",
        })
    if request.method == "GET" and path.endswith("/status"):
        request_id = path.split("/")[-2]
        polls[request_id] = polls.get(request_id, 0) + 1
        if polls[request_id] == 1:
            return httpx.Response(200, json={"status": "in_progress", "request_id": request_id})
        return httpx.Response(200, json={
            "status": "completed", "request_id": request_id,
            "video": {"url": f"https://cdn.higgsfield.test/{request_id}.mp4"},
        })
    if request.method == "GET" and request.url.host == "cdn.higgsfield.test":
        return httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"})
    return httpx.Response(404)


original_client = httpx.Client
import app.providers.higgsfield as higgsfield_module  # noqa: E402

original_sleep = higgsfield_module.time.sleep
httpx.Client = lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs)
higgsfield_module.time.sleep = lambda _seconds: None

text_result = provider.generate(
    VideoRequest(prompt="A calm ocean", mode="text_to_video", duration_seconds=10)
)
check("text generation polls and downloads MP4", text_result.data == MP4
      and text_result.interaction_id == "text-1", str(text_result.interaction_id))

image_result = provider.generate(
    VideoRequest(prompt="Continue the scene", mode="image_to_video", duration_seconds=10,
                 media=[image])
)
httpx.Client = original_client
higgsfield_module.time.sleep = original_sleep

upload_call = next(call for call in calls if call.method == "PUT")
image_call = next(call for call in calls if call.method == "POST" and call.url.path.endswith("/image-to-video"))
body = json.loads(image_call.content)
check("reference upload succeeds", image_result.data == MP4 and image_result.interaction_id == "image-1")
check("API key is not sent to presigned storage", "authorization" not in upload_call.headers)
check("image request uses returned public URL", body.get("image_url") == "https://cdn.higgsfield.test/frame.png")
check("generation requests include idempotency key", all(
    "idempotency-key" in call.headers for call in calls if call.method == "POST" and call.url.path.endswith(("text-to-video", "image-to-video"))
))

print("\n" + ("FAILURES: " + ", ".join(failures) if failures else "All checks passed."))
raise SystemExit(1 if failures else 0)