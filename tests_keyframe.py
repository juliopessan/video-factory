"""Jornada com provider que NÃO estende cena — o caso do Sora-2 no Foundry.

Usa o mock com `VF_MOCK_NO_EXTEND`, que declara a mesma limitação: as peças saem
independentes, cada uma partindo do último frame da anterior, e o passo 5 emenda
tudo num filme só.

Roda offline (precisa de FFmpeg): `python3 tests_keyframe.py`.
"""
from __future__ import annotations

import os
import tempfile
import time

os.environ["VF_PROVIDER"] = "mock"
os.environ["VF_MOCK_NO_EXTEND"] = "1"
os.environ["VF_STORAGE_DIR"] = tempfile.mkdtemp(prefix="vf-keyframe-")
os.environ.pop("GEMINI_API_KEY", None)  # storytelling pelo template local

from fastapi.testclient import TestClient  # noqa: E402

from app import postproduction as post  # noqa: E402
from app.main import app  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FALHA'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


def wait_for(fn, timeout=300.0, interval=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


if not post.available():
    print("FFmpeg ausente: este teste precisa dele.")
    raise SystemExit(0)

client = TestClient(app)
with client:
    config = client.get("/api/config").json()
    check("provider sem extend encadeia por keyframe", config["chaining"] == "keyframe", str(config["chaining"]))

    project = client.post("/api/projects", json={"name": "Keyframe"}).json()
    pipeline = client.post(
        f"/api/projects/{project['id']}/pipelines",
        json={"product": "fábrica de migração", "value": "governança e custo exato",
              "duration_seconds": 30, "resolution": "720p"},
    ).json()
    check("storyboard com 3 peças", len(pipeline["storyboard"]["segments"]) == 3)

    check("render aceito", client.post(f"/api/pipelines/{pipeline['id']}/render", json={}).status_code == 202)
    final = wait_for(
        lambda: (lambda p: p if p["status"] in ("completed", "failed") else None)(
            client.get(f"/api/pipelines/{pipeline['id']}").json()
        )
    )
    check("pipeline concluiu", final and final["status"] == "completed", str(final and final.get("error")))
    renders = final["renders"] if final else []
    check("3 peças renderizadas", len(renders) == 3, str(len(renders)))
    check("peça 1 abre a cena", renders and renders[0]["mode"] == "text_to_video")
    check("peças seguintes partem de um frame",
          [r["mode"] for r in renders[1:]] == ["image_to_video", "image_to_video"],
          str([r["mode"] for r in renders[1:]]))
    check("sem parent: as peças são independentes", all(not r["parent_id"] for r in renders))
    check("cada peça recebeu o keyframe da anterior",
          all(r["meta"]["media"] and r["meta"]["media"][0]["role"] == "first_frame" for r in renders[1:]),
          str([r["meta"]["media"] for r in renders[1:]]))
    check("pipeline registra a estratégia", final["chaining"] == "keyframe", str(final.get("chaining")))

    # passo 5: emenda as peças antes de exportar
    check("export aceito", client.post(
        f"/api/pipelines/{pipeline['id']}/exports", json={"formats": ["16:9"]}
    ).status_code == 202)
    exports = wait_for(
        lambda: (lambda e: e if e and all(x["status"] in ("completed", "failed") for x in e) else None)(
            client.get(f"/api/pipelines/{pipeline['id']}/exports").json()["exports"]
        )
    )
    check("export concluiu", exports and exports[0]["status"] == "completed",
          str(exports and exports[0].get("error")))
    if exports and exports[0]["status"] == "completed":
        duracao = post.probe_duration(exports[0]["path"]) or 0
        check("filme final soma as 3 peças", abs(duracao - 30) < 1.5, f"{duracao}s")

print("\n" + ("FALHAS: " + ", ".join(failures) if failures else "Tudo verde."))
raise SystemExit(1 if failures else 0)
