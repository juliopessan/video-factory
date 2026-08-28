"""Testes do provider Sora-2 no Foundry, sem tocar na Azure.

Um transporte HTTP falso responde no lugar do serviço, então dá para verificar
caminho, cabeçalhos, corpo e o ciclo criar → consultar → baixar. A API do
sora-2 está em preview e o acesso é gated: nada aqui foi validado contra um
deployment real.

Roda offline: `python3 tests_azure.py`.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-azure-"))

import httpx  # noqa: E402

from app.providers.azure_sora import AzureSoraProvider, nearest_seconds  # noqa: E402
from app.providers.base import MediaInput, ProviderError, VideoRequest  # noqa: E402

failures: list[str] = []
calls: list[httpx.Request] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FALHA'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


def make_provider(**kwargs) -> AzureSoraProvider:
    return AzureSoraProvider(
        endpoint="https://contoso.cognitiveservices.azure.com/openai/",
        api_key="chave-secreta",
        **kwargs,
    )


# ----------------------------------------------------------------- montagem

p = make_provider()
check("endpoint normalizado", p.endpoint == "https://contoso.cognitiveservices.azure.com", p.endpoint)
check("estilo videos é o padrão", p.url().startswith(
    "https://contoso.cognitiveservices.azure.com/openai/v1/videos?api-version=preview"), p.url())
check("estilo antigo usa video/generations",
      "/openai/v1/video/generations?" in make_provider(api_style="generations").url(),
      make_provider(api_style="generations").url())
check("download aponta para /content", p.url("vid_1", "content").split("?")[0].endswith("/videos/vid_1/content"))
check("manda api-key e bearer", p.headers["api-key"] == "chave-secreta" and p.headers["Authorization"].endswith("chave-secreta"))

body = p.build_payload(VideoRequest(prompt="Fish swimming", mode="text_to_video", duration_seconds=10))
check("16:9 vira 1280x720", body["size"] == "1280x720", str(body))
check("duração cai no valor aceito mais próximo", body["seconds"] == "8", str(body))
check("model é o nome do deployment", body["model"] == "sora-2", str(body))
vertical = p.build_payload(VideoRequest(prompt="x", mode="text_to_video", aspect_ratio="9:16"))
check("9:16 vira 720x1280", vertical["size"] == "720x1280", str(vertical))
check("durações fora do catálogo são arredondadas", [nearest_seconds(n) for n in (1, 5, 7, 11, 30)] == [4, 4, 8, 12, 12])

try:
    p.build_payload(VideoRequest(prompt="x", mode="text_to_video", aspect_ratio="1:1"))
    check("proporção não suportada é recusada", False, "não levantou")
except ProviderError as exc:
    check("proporção não suportada é recusada", "1:1" in str(exc), str(exc))

try:
    p.generate(VideoRequest(prompt="x", mode="extend", previous_interaction_id="v1"))
    check("extend é recusado com orientação", False, "não levantou")
except ProviderError as exc:
    check("extend é recusado com orientação", "keyframe" in str(exc), str(exc))

remix = p.build_payload(VideoRequest(prompt="troque o fundo", mode="edit", previous_interaction_id="vid_7"))
check("edit vira remix do vídeo anterior", remix["remix_video_id"] == "vid_7", str(remix))

check("capacidades dizem que não estende", AzureSoraProvider.capabilities()["extend"] is False)

# --------------------------------------------------- ciclo completo (falso)

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def handler(request: httpx.Request) -> httpx.Response:
    calls.append(request)
    path = request.url.path
    if request.method == "POST":
        return httpx.Response(200, json={"id": "vid_42", "status": "queued"})
    if path.endswith("/content"):
        return httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"})
    poll = sum(1 for c in calls if c.method == "GET" and not c.url.path.endswith("/content"))
    return httpx.Response(200, json={"id": "vid_42", "status": "in_progress" if poll < 2 else "completed"})


original_client = httpx.Client
httpx.Client = lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs)
import app.providers.azure_sora as azure_module  # noqa: E402

azure_module.POLL_INTERVAL_SECONDS = 0

result = p.generate(
    VideoRequest(prompt="A little chipmunk", mode="image_to_video", duration_seconds=8,
                 media=[MediaInput("image", b"\x89PNG", "image/png", "first_frame")])
)
httpx.Client = original_client

check("devolve o mp4 baixado", result.data == MP4 and result.mime_type == "video/mp4")
check("guarda o id do vídeo", result.interaction_id == "vid_42", str(result.interaction_id))
check("esperou o job sair de in_progress", sum(1 for c in calls if c.method == "GET") >= 3, str(len(calls)))
check("frame de referência vai como multipart",
      calls[0].headers.get("content-type", "").startswith("multipart/form-data"),
      calls[0].headers.get("content-type", ""))
check("api-version em toda chamada", all("api-version=preview" in str(c.url) for c in calls),
      str([str(c.url) for c in calls]))

print("\n" + ("FALHAS: " + ", ".join(failures) if failures else "Tudo verde."))
raise SystemExit(1 if failures else 0)
