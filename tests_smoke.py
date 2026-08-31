"""Teste de fumaca: percorre projeto -> pipeline -> storyboard -> render -> midia.

Roda offline no provider mock: `python3 tests_smoke.py`.
"""
from __future__ import annotations

import json
import os
import struct
import tempfile
import time
from pathlib import Path

os.environ.setdefault("VF_PROVIDER", "mock")
os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-smoke-"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
failures: list[str] = []


def mp4_bytes(timescale: int, duration: int) -> bytes:
    """MP4 minimo com moov>mvhd, so para exercitar a leitura de duracao."""
    box = lambda kind, payload: struct.pack(">I", len(payload) + 8) + kind + payload  # noqa: E731
    mvhd = b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, timescale, duration) + b"\x00" * 80
    return box(b"ftyp", b"isom" + b"\x00" * 8) + box(b"moov", box(b"mvhd", mvhd))


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FALHA'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


def wait_for(fn, timeout: float = 60.0, interval: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


with client:
    config = client.get("/api/config").json()
    check("config expoe o provider", config["provider"] == "mock", str(config))

    project = client.post("/api/projects", json={"name": "Smoke"}).json()
    check("cria projeto", bool(project["id"]))

    # 1) contexto -> 2) storytelling -> 3) storyboard
    context = {
        "brand": "Contoso",
        "product": "fabrica de migracao para Azure",
        "audience": "CIOs de grandes empresas",
        "problem": "entender sistemas legados manualmente leva meses",
        "turning_point": "mapeamento deterministico antes de construir",
        "value": "governanca ponta a ponta e custo otimizado",
        "cta": "Vamos migrar.",
        "duration_seconds": 30,
        "resolution": "360p",
    }
    pipeline = client.post(f"/api/projects/{project['id']}/pipelines", json=context).json()
    check("pipeline criado", bool(pipeline.get("id")), str(pipeline)[:200])
    check("storytelling em 5 atos", len(pipeline["story"]["acts"]) == 5)
    beats = [a.get("script_beat") for a in pipeline["story"]["acts"]]
    check("padrão de script intro-hook-meat-cta nos atos",
          beats == ["hook", "meat", "meat", "meat", "cta"], str(beats))
    check("Ato 1 abre com intro e hook",
          pipeline["storyboard"]["segments"][0]["script_beats"][:2] == ["intro", "hook"],
          str(pipeline["storyboard"]["segments"][0]["script_beats"]))
    check("a última peça carrega o cta",
          "cta" in pipeline["storyboard"]["segments"][-1]["script_beats"],
          str(pipeline["storyboard"]["segments"][-1]["script_beats"]))
    check("o beat entra no prompt compilado",
          "SCRIPT BEAT" in pipeline["storyboard"]["segments"][0]["prompt"])
    check("locucao em portugues", all(a["vo"] for a in pipeline["story"]["acts"]))
    segments = pipeline["storyboard"]["segments"]
    check("storyboard com 3 pecas de 10s", len(segments) == 3, f"{len(segments)} pecas")
    check("peca 1 abre a cena", segments[0]["mode"] == "text_to_video")
    check("pecas seguintes estendem", [s["mode"] for s in segments[1:]] == ["extend", "extend"])
    check("prompt tem blocos do template", "ACTION AND CAMERA SEQUENCE" in segments[0]["prompt"])
    check("continuacao explicita", segments[1]["prompt"].startswith("CONTINUATION"))
    # o modelo já queimou "DIRECT CUT" e "50mm 40°" dentro do quadro: toda peça
    # precisa carregar a restrição, e antes da direção técnica
    check("toda peça proíbe texto na tela",
          all("ON-SCREEN TEXT" in s["prompt"] for s in segments))
    check("a restrição vem antes da direção de câmera",
          all(s["prompt"].index("ON-SCREEN TEXT") < s["prompt"].index("ACTION AND CAMERA SEQUENCE")
              for s in segments))
    check("a restrição nomeia a notação que vazava",
          all(t in segments[0]["prompt"] for t in ("HARD CUT", "50mm", "84°", "timecodes")),
          segments[0]["prompt"][segments[0]["prompt"].index("ON-SCREEN TEXT"):][:200])

    # editar a locução de um ato precisa chegar na peça e no prompt
    story_editada = json.loads(json.dumps(pipeline["story"]))
    story_editada["acts"][0]["vo"] = "Locução editada à mão no passo 2."
    depois = client.patch(f"/api/pipelines/{pipeline['id']}", json={"story": story_editada}).json()
    check("edição do ato chega na locução da peça",
          "Locução editada à mão no passo 2." in depois["storyboard"]["segments"][0]["vo"],
          depois["storyboard"]["segments"][0]["vo"][:80])
    check("edição do ato chega no prompt compilado",
          "Locução editada à mão no passo 2." in depois["storyboard"]["segments"][0]["prompt"])
    check("direção de câmera da peça não é tocada",
          depois["storyboard"]["segments"][0]["shot_sequence"] == pipeline["storyboard"]["segments"][0]["shot_sequence"])
    pipeline = depois

    # edicao manual do storyboard e regeracao de prompts
    board = pipeline["storyboard"]
    board["segments"][0]["shot_sequence"] = "OPENING SHOT — 107° wide rectilinear view, tabletop push-in."
    client.patch(f"/api/pipelines/{pipeline['id']}", json={"storyboard": board})
    edited = client.post(f"/api/pipelines/{pipeline['id']}/prompts").json()
    check("prompt reflete a edicao", "tabletop push-in" in edited["storyboard"]["segments"][0]["prompt"])

    # 4) resultado final
    accepted = client.post(f"/api/pipelines/{pipeline['id']}/render", json={"resolution": "360p"})
    check("render aceito", accepted.status_code == 202, accepted.text)
    final = wait_for(
        lambda: (lambda p: p if p["status"] in ("completed", "failed") else None)(
            client.get(f"/api/pipelines/{pipeline['id']}").json()
        )
    )
    check("pipeline concluiu", final and final["status"] == "completed", str(final and final.get("error")))
    renders = final["renders"] if final else []
    check("3 pecas renderizadas", len(renders) == 3, f"{len(renders)}")
    check("cena chega a 30s", renders and renders[-1]["cumulative_seconds"] == 30)
    check("encadeamento por parent_id", all(r["parent_id"] for r in renders[1:]))
    if renders:
        media = client.get(f"/api/generations/{renders[0]['id']}/media")
        check("midia servida", media.status_code == 200 and len(media.content) > 0)

    # limite cumulativo de 40s
    too_long = client.post(
        f"/api/projects/{project['id']}/pipelines", json={**context, "duration_seconds": 60}
    ).json()
    check("duracao normalizada ao teto de 40s", too_long["context"]["duration_seconds"] == 40)

    # draft room
    batch = client.post(
        f"/api/projects/{project['id']}/draft-batches",
        json={"prompts": ["variacao A", "variacao B", "variacao C"]},
    ).json()
    check("draft room cria 3 rascunhos em 360p", len(batch) == 3 and batch[0]["resolution"] == "360p")
    done = wait_for(
        lambda: all(
            client.get(f"/api/generations/{g['id']}").json()["status"] == "completed" for g in batch
        )
    )
    check("rascunhos concluem", bool(done))

    # referencias de video: limite de 3s medido no cabecalho do arquivo
    fixture = Path(__file__).parent / "tests" / "fixtures" / "sample.webm"
    with fixture.open("rb") as handle:
        short_ref = client.post(
            f"/api/projects/{project['id']}/assets",
            files={"file": ("sample.webm", handle, "video/webm")},
            data={"kind": "video"},
        ).json()
    check("duracao medida no upload", 2.3 < (short_ref.get("duration_seconds") or 0) < 2.6, str(short_ref))
    ok_ref = client.post(
        f"/api/projects/{project['id']}/generations",
        json={
            "prompt": "use a referencia",
            "mode": "reference_to_video",
            "media": [{"asset_id": short_ref["id"], "kind": "video", "role": "reference"}],
        },
    )
    check("referencia de 2.4s e aceita", ok_ref.status_code == 202, ok_ref.text)

    long_ref = client.post(
        f"/api/projects/{project['id']}/assets",
        files={"file": ("longa.mp4", mp4_bytes(600, 4350), "video/mp4")},
        data={"kind": "video"},
    ).json()
    rejected = client.post(
        f"/api/projects/{project['id']}/generations",
        json={
            "prompt": "use a referencia",
            "mode": "reference_to_video",
            "media": [{"asset_id": long_ref["id"], "kind": "video", "role": "reference"}],
        },
    )
    check("referencia de 7.25s e recusada", rejected.status_code == 400, rejected.text)

    # validacoes de dominio
    bad = client.post(
        f"/api/projects/{project['id']}/generations",
        json={"prompt": "x", "mode": "interpolate"},
    )
    check("interpolate exige 2 frames", bad.status_code == 400, bad.text)
    # 30s + 10s = 40s ainda cabe; a extensao seguinte estouraria o teto
    fourth = client.post(
        f"/api/projects/{project['id']}/generations",
        json={"prompt": "x", "mode": "extend", "parent_id": renders[-1]["id"], "resolution": "360p"},
    )
    check("quarta peca chega a 40s", fourth.status_code == 202, fourth.text)
    fourth_id = fourth.json()["id"]
    wait_for(lambda: client.get(f"/api/generations/{fourth_id}").json()["status"] == "completed")
    bad = client.post(
        f"/api/projects/{project['id']}/generations",
        json={"prompt": "x", "mode": "extend", "parent_id": fourth_id, "resolution": "360p"},
    )
    check("extensao alem de 40s e recusada", bad.status_code == 400, bad.text)

    # pipeline com locução em inglês (en-US)
    pipe_en = client.post(
        f"/api/projects/{project['id']}/pipelines",
        json={**context, "voiceover_language": "en-US"},
    ).json()
    check("pipeline com en-US configurado", pipe_en["context"]["voiceover_language"] == "en-US")
    check("prompt em en-US especifica American English",
          "Voiceover in American English" in pipe_en["storyboard"]["segments"][0]["prompt"],
          pipe_en["storyboard"]["segments"][0]["prompt"][-100:])

print("\n" + ("FALHAS: " + ", ".join(failures) if failures else "Tudo verde."))
raise SystemExit(1 if failures else 0)
