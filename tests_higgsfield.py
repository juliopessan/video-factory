"""Testes do provider Higgsfield (Wan 3.0 / Kling 3.0) sem tocar na rede.

Um transporte HTTP falso responde no lugar da API: verifica endpoint, cabeçalhos,
corpo, upload de referência, polling e tratamento de estados terminais.
Wan 3.0 segue a documentação pública; o endpoint do Kling 3.0 é configurável
porque a referência REST pública não o traz. Nada aqui foi validado contra a
API real. Roda offline: `python3 tests_higgsfield.py`.
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-hf-"))
for var in ("HF_API_KEY_ID", "HF_API_KEY_SECRET", "HF_KEY", "HIGGSFIELD_API_KEY",
            "VF_HIGGSFIELD_KLING_T2V", "VF_HIGGSFIELD_KLING_I2V"):
    os.environ.pop(var, None)

import httpx  # noqa: E402

from app.providers import PROVIDERS, capabilities  # noqa: E402
from app.providers.base import MediaInput, ProviderError, VideoRequest  # noqa: E402
from app.providers.higgsfield import HiggsfieldProvider, clamp_seconds  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FALHA'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


def provider(**kw) -> HiggsfieldProvider:
    return HiggsfieldProvider(credentials="kid:ksecret", sleep=lambda s: None, **kw)


class FakeApi:
    """Responde no lugar de api.higgsfield.ai e do CDN."""

    def __init__(self, final_status: str = "completed", polls: int = 2) -> None:
        self.requests: list[httpx.Request] = []
        self.final_status, self.polls_left = final_status, polls

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.method == "POST" and url.endswith("/files/generate-upload-url"):
            return httpx.Response(200, json={"upload_url": "https://store.test/put", "public_url": "https://cdn.test/ref.png",
                                             "upload_headers": {"Content-Type": "image/png"}})
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "POST":
            return httpx.Response(200, json={"status": "queued", "request_id": "req-1",
                                             "status_url": "https://api.higgsfield.ai/requests/req-1/status"})
        if url.endswith("/requests/req-1/status"):
            if self.polls_left > 0:
                self.polls_left -= 1
                return httpx.Response(200, json={"status": "in_progress", "request_id": "req-1"})
            body = {"status": self.final_status, "request_id": "req-1"}
            if self.final_status == "completed":
                body["video"] = {"url": "https://cdn.test/out.mp4"}
            return httpx.Response(200, json=body)
        if url == "https://cdn.test/out.mp4":
            return httpx.Response(200, content=b"MP4DATA", headers={"content-type": "video/mp4"})
        return httpx.Response(404)


def run(p: HiggsfieldProvider, api: FakeApi, request: VideoRequest):
    real = httpx.Client
    httpx.Client = lambda *a, **k: real(*a, transport=httpx.MockTransport(api), **{x: y for x, y in k.items() if x != "transport"})
    try:
        return p.generate(request)
    finally:
        httpx.Client = real


# ----------------------------------------------------------------- registro e config

check("provider registrado", "higgsfield" in PROVIDERS)
check("sem extend, encadeia por keyframe", capabilities("higgsfield")["extend"] is False)
check("duração é limitada a 2-30s", [clamp_seconds(n) for n in (0, 2, 10, 45)] == [2, 2, 10, 30])

try:
    HiggsfieldProvider(credentials="")
    check("sem credencial é recusado", False, "não levantou")
except ProviderError as exc:
    check("sem credencial é recusado", "HF_API_KEY_ID" in str(exc), str(exc))

try:
    HiggsfieldProvider(credentials="a:b", model="sora")
    check("modelo desconhecido é recusado", False, "não levantou")
except ProviderError as exc:
    check("modelo desconhecido é recusado", "wan3" in str(exc), str(exc))

# ----------------------------------------------------------------- Wan 3.0 texto → vídeo

wan = provider(model="wan3")
body = wan.build_payload(VideoRequest(prompt=" Plano aberto ", mode="text_to_video", resolution="360p", duration_seconds=10))
check("prompt é aparado", body["prompt"] == "Plano aberto", str(body))
check("resolução fora do catálogo cai para 720p", body["resolution"] == "720p", str(body))
check("áudio nativo ligado", body["generate_audio"] is True)
check("duração vai como inteiro", body["duration"] == 10 and isinstance(body["duration"], int))

api = FakeApi()
result = run(wan, api, VideoRequest(prompt="Hospital", mode="text_to_video", duration_seconds=10, aspect_ratio="16:9", seed=7))
submit = api.requests[0]
check("POST no endpoint documentado do Wan 3.0", str(submit.url) == "https://api.higgsfield.ai/alibaba/wan-3.0/text-to-video", str(submit.url))
check("autorização no formato Key id:secret", submit.headers["authorization"] == "Key kid:ksecret")
check("manda Idempotency-Key", bool(submit.headers.get("idempotency-key")))
sent = json.loads(submit.content)
check("seed e aspect_ratio no corpo", sent["seed"] == 7 and sent["aspect_ratio"] == "16:9", str(sent))
check("polling até concluir", sum(1 for r in api.requests if str(r.url).endswith("/status")) == 3)
check("baixa o MP4 sem credencial", all("authorization" not in r.headers for r in api.requests if str(r.url) == "https://cdn.test/out.mp4"))
check("devolve bytes e request_id", result.data == b"MP4DATA" and result.interaction_id == "req-1" and result.mime_type == "video/mp4")

# ----------------------------------------------------------------- referência por imagem

api = FakeApi(polls=0)
run(wan, api, VideoRequest(prompt="Continua", mode="image_to_video",
                           media=[MediaInput(kind="image", data=b"PNG", mime_type="image/png", role="first_frame")]))
urls = [str(r.url) for r in api.requests]
check("sobe a imagem antes de gerar", urls.index("https://api.higgsfield.ai/files/generate-upload-url") < urls.index("https://api.higgsfield.ai/alibaba/wan-3.0/reference-to-video"), str(urls))
gen = [r for r in api.requests if str(r.url).endswith("reference-to-video")][0]
check("image_urls leva a public_url", json.loads(gen.content)["image_urls"] == ["https://cdn.test/ref.png"])

# ----------------------------------------------------------------- Kling 3.0

kling = provider(model="kling3")
try:
    kling.endpoint_id(with_image=False)
    check("kling sem endpoint configurado instrui em vez de chutar", False, "não levantou")
except ProviderError as exc:
    check("kling sem endpoint configurado instrui em vez de chutar", "VF_HIGGSFIELD_KLING_T2V" in str(exc), str(exc))
os.environ["VF_HIGGSFIELD_KLING_T2V"] = "/kling/3.0/text-to-video/"
check("kling usa o endpoint do ambiente", kling.endpoint_id(with_image=False) == "kling/3.0/text-to-video")
api = FakeApi(polls=0)
run(kling, api, VideoRequest(prompt="x", mode="text_to_video"))
check("kling posta no endpoint configurado", str(api.requests[0].url) == "https://api.higgsfield.ai/kling/3.0/text-to-video", str(api.requests[0].url))
os.environ.pop("VF_HIGGSFIELD_KLING_T2V")

# ----------------------------------------------------------------- erros

for status, needle in (("nsfw", "nsfw"), ("failed", "failed"), ("canceled", "canceled")):
    try:
        run(wan, FakeApi(final_status=status, polls=0), VideoRequest(prompt="x", mode="text_to_video"))
        check(f"status {status} vira ProviderError", False, "não levantou")
    except ProviderError as exc:
        check(f"status {status} vira ProviderError", needle in str(exc), str(exc))

try:
    wan.generate(VideoRequest(prompt="x", mode="extend", previous_interaction_id="r"))
    check("extend é recusado com orientação", False, "não levantou")
except ProviderError as exc:
    check("extend é recusado com orientação", "keyframe" in str(exc), str(exc))

try:
    wan.build_payload(VideoRequest(prompt="x", mode="text_to_video", aspect_ratio="21:9"))
    check("proporção não suportada é recusada", False, "não levantou")
except ProviderError as exc:
    check("proporção não suportada é recusada", "21:9" in str(exc), str(exc))

try:
    provider()._json(httpx.Response(401, text="no"), "criar")
    check("401 vira erro de credencial sem vazar a chave", False, "não levantou")
except ProviderError as exc:
    check("401 vira erro de credencial sem vazar a chave", "401" in str(exc) and "ksecret" not in str(exc), str(exc))

print()
if failures:
    print(f"{len(failures)} falha(s): {failures}")
    raise SystemExit(1)
print("Todos os testes do provider Higgsfield passaram.")
