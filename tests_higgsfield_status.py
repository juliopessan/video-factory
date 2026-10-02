"""Resolução da URL de status do job no provider Higgsfield (offline).

Reproduz o erro visto no primeiro render real: "Resposta do Higgsfield sem
status_url HTTPS válida". Roda: `python3 tests_higgsfield_status.py`.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-hf-status-"))

import httpx  # noqa: E402

import app.providers.higgsfield as hf  # noqa: E402
from app.providers.base import ProviderError, VideoRequest  # noqa: E402
from app.providers.higgsfield import HiggsfieldProvider  # noqa: E402

failures: list[str] = []
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


resolve = HiggsfieldProvider._status_url_for
canonical = "https://api.higgsfield.ai/requests/abc12345-req/status"
check("status_url oficial é usada", resolve({"request_id": "x", "status_url": canonical}) == canonical)
check("sem status_url, monta pelo request_id", resolve({"request_id": "abc12345-req"}) == canonical)
check("usa `id` quando não há request_id", resolve({"id": "abc12345-req"}) == canonical)
check("status_url de outro host é ignorada (não é seguida)",
      resolve({"request_id": "abc12345-req", "status_url": "https://evil.example/requests/abc12345-req/status"}) == canonical)
check("status_url http é ignorada", resolve({"request_id": "abc12345-req", "status_url": "http://api.higgsfield.ai/requests/x/status"}) == canonical)
check("status_url relativa de /requests/ é aceita",
      resolve({"status_url": "/requests/abc12345-req/status"}) == canonical)
try:
    resolve({"request_id": "../../x", "token": "SEGREDO"})
    check("request_id fora do padrão é recusado", False, "não levantou")
except ProviderError as exc:
    text = str(exc)
    check("request_id fora do padrão é recusado, listando só nomes de campos",
          "request_id, token" in text and "SEGREDO" not in text, text)
try:
    resolve({})
    check("resposta vazia gera erro claro", False, "não levantou")
except ProviderError as exc:
    check("resposta vazia gera erro claro", "campos recebidos: nenhum" in str(exc), str(exc))

# ---- ciclo completo: envio sem status_url (o caso do erro real)
calls: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    calls.append(request)
    if request.method == "POST":
        return httpx.Response(200, json={"request_id": "abc12345-req", "status": "queued"})
    if request.url.host == "cdn.example":
        return httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"})
    polls = sum(1 for c in calls if c.url.path.endswith("/status"))
    if polls < 2:
        return httpx.Response(200, json={"status": "in_progress"})
    return httpx.Response(200, json={"status": "completed", "video": {"url": "https://cdn.example/v.mp4"}})


original_client = httpx.Client
httpx.Client = lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw)
hf.POLL_INITIAL_SECONDS = 0
hf.POLL_MAX_SECONDS = 0
try:
    provider = HiggsfieldProvider(key_id="i", key_secret="s")
    result = provider.generate(VideoRequest(prompt="x", mode="text_to_video", duration_seconds=10))
finally:
    httpx.Client = original_client

check("render conclui mesmo sem status_url na resposta", result.data == MP4 and result.interaction_id == "abc12345-req")
check("consultou /requests/{id}/status", any(c.url.path == "/requests/abc12345-req/status" for c in calls))
check("só falou com api.higgsfield.ai e o CDN do vídeo", {c.url.host for c in calls} <= {"api.higgsfield.ai", "cdn.example"})

print(f"\n{'All checks passed.' if not failures else str(len(failures)) + ' FAILURE(S)'}")
raise SystemExit(1 if failures else 0)
