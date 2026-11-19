"""Aderência do storyboard às entradas e robustez do render.

Valida o input real "Video 1 | How AI and Copilot Work" (ABECE) e varia
duração e idioma. Não precisa de rede nem de FFmpeg: provider mock.

Roda offline: `python3 tests_storyboard.py`.
"""
from __future__ import annotations

import os
import tempfile
import time

os.environ["VF_PROVIDER"] = "mock"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-sb-"))

from app import db, pipeline as P, studio  # noqa: E402
from app.postproduction import build_srt  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FALHA'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


norm = lambda t: " ".join(t.split()).rstrip(" .;:,!?…").lower()  # noqa: E731

ABECE = dict(
    title="Video 1 | How AI and Copilot Work",
    source_reference="Reference slides: 3 to 10",
    brand="ABECE",
    product="Microsoft 365 Copilot Premium Adoption Program",
    audience="Client employees who are beginning to use Microsoft 365 Copilot",
    problem="Artificial Intelligence is already part of the workplace, but concepts such as Generative AI, "
            "LLMs, and Copilot may still seem complex and disconnected from everyday work.",
    turning_point="Copilot transforms advanced AI models into a simple interface connected to the user's "
                  "tools and work context.",
    value="Understanding of AI fundamentals, greater confidence in using Copilot, and readiness for more "
          "productive collaboration between people and agents.",
    cta="Understand what powers Copilot and begin exploring new possibilities for your work.",
    characters="Diverse corporate professionals in a modern healthcare environment, wearing elegant "
               "business-casual clothing in neutral tones with subtle orange and blue accents.",
    aesthetic="Premium photorealistic corporate footage, cinematic lighting, modern healthcare environment, "
              "subtle futuristic interfaces, clean compositions, smooth camera movement and sophisticated "
              "Microsoft-inspired visual language.",
    reference_note="Preserve the visual relationship between the human professional, the Copilot interface "
                   "and the Microsoft 365 ecosystem. Maintain the transition from individual productivity "
                   "to human-agent collaboration.",
    voiceover_language="en-US", duration_seconds=30, aspect_ratio="16:9", resolution="720p",
)

# ------------------------------------------------- aderência (input ABECE)
for duration in (20, 30, 40):
    ctx = P.normalize_context({**ABECE, "duration_seconds": duration})
    story = P.build_story(ctx)
    board = P.build_storyboard(ctx, story)
    segs = board["segments"]
    tag = f"[{duration}s]"
    vo = " ".join(s["vo"] for s in segs).lower()
    check(f"{tag} {duration // 10} peças de 10s", len(segs) == duration // 10 and all(s["duration_seconds"] == 10 for s in segs))
    acts = [a for s in segs for a in s["acts"]]
    check(f"{tag} os 5 atos aparecem uma vez, em ordem", acts == [1, 2, 3, 4, 5], str(acts))
    for key in ("problem", "turning_point", "value", "cta"):
        check(f"{tag} {key} dito na locução", norm(ctx[key]) in vo)
    check(f"{tag} sem frase inventada", "months" not in vo and "budgets" not in vo and "barrier to growth" not in vo)
    check(f"{tag} sem pontuação dupla", ".." not in vo and ". ." not in vo)
    check(f"{tag} sem 'legacy' no prompt", all("legacy" not in s["prompt"].lower() for s in segs))
    first = segs[0]["prompt"]
    check(f"{tag} elenco, estética e referência literais no prompt 1",
          ctx["characters"] in first and ctx["aesthetic"] in first and ctx["reference_note"] in first)
    check(f"{tag} sem <<<image_1>>> quando não há imagem", all("<<<image_1>>>" not in s["prompt"] for s in segs))
    check(f"{tag} peças de continuação repetem elenco e referência (keyframe não perde identidade)",
          all(ctx["characters"] in s["prompt"] and ctx["reference_note"] in s["prompt"] for s in segs[1:]))
    check(f"{tag} beats do script em inglês", "Situe" not in first and "State in one sentence" in first)
    check(f"{tag} locução em American English", "American English" in first)
    check(f"{tag} título do usuário vira o título do filme", story["title"] == ABECE["title"], story["title"])

ctx = P.normalize_context(ABECE)
board = P.build_storyboard(ctx, P.build_story(ctx))
report = board.get("vo_overflow") or {}
check("locução longa gera advertência estruturada (não é cortada em silêncio)",
      report.get("limit_words") == 25 and [p["index"] for p in report["pieces"]] == [2, 3]
      and [p["words"] for p in report["pieces"]] == [43, 31], str(report))
check("a fala do usuário segue intacta junto com a advertência",
      norm(ctx["problem"]) in " ".join(s["vo"] for s in board["segments"]).lower())
short = P.normalize_context({**ABECE, "problem": "IA ainda parece distante", "value": "mais confiança no Copilot",
                             "turning_point": "o Copilot liga a IA ao seu trabalho", "duration_seconds": 30})
check("locução curta não gera advertência",
      P.build_storyboard(short, P.build_story(short))["vo_overflow"]["pieces"] == [])
check("source_reference chega ao contexto do modelo", ctx["source_reference"] == "Reference slides: 3 to 10")

# 40s: 5 atos em 4 peças, sem repetir o CTA
ctx40 = P.normalize_context({**ABECE, "duration_seconds": 40})
segs40 = P.build_storyboard(ctx40, P.build_story(ctx40))["segments"]
check("40s não repete o ato do CTA", sum(1 for s in segs40 if 5 in s["acts"]) == 1, str([s["acts"] for s in segs40]))
check("nenhuma peça fica sem locução", all(s["vo"].strip() for s in segs40))

# com imagem de referência: cita image_1 e usa image_to_video
ctx_img = P.normalize_context({**ABECE, "reference_asset_id": None})
ctx_img["reference_asset_id"] = "ast_x"
block = P._reference_block(ctx_img)
check("com imagem, ACTIVE REFERENCE aponta <<<image_1>>>", "<<<image_1>>>" in block and ctx_img["reference_note"] in block)
check("nota e frase final separadas por espaço", ". Keep" in block, block)

# pontuação: com e sem ponto final vira a mesma frase
check("_sentence normaliza pontuação", P._sentence("  valor  claro. ") == "Valor claro." == P._sentence("valor claro"))

# português: sem as frases fixas do template antigo
ctx_pt = P.normalize_context({**ABECE, "voiceover_language": "pt-BR", "problem": "migrar sistemas leva meses"})
vo_pt = " ".join(s["vo"] for s in P.build_storyboard(ctx_pt, P.build_story(ctx_pt))["segments"])
check("pt-BR usa o problema do usuário sem completar a frase", "Migrar sistemas leva meses." in vo_pt and "maior barreira" not in vo_pt, vo_pt)

# --------------------------------------- saída do modelo com número errado de peças
original_available = P.textgen.available
original_generate = P.textgen.generate_json
P.textgen.available = lambda: True
P.textgen.generate_json = lambda *a, **k: {"segments": [{"vo": "só uma", "acts": [1]}], "scene_context": "x"}
try:
    ctx = P.normalize_context(ABECE)
    story = P._fallback_story(ctx)
    board = P.build_storyboard(ctx, story)
    check("modelo com peças a menos cai no template com aviso",
          len(board["segments"]) == 3 and "devolveu 1 peças" in board["warning"], str(board.get("warning")))
finally:
    P.textgen.available = original_available
    P.textgen.generate_json = original_generate

# ------------------------------------------------ legendas com durações reais
segs = [{"vo": "Primeira fala."}, {"vo": "Segunda fala."}]
nominal = build_srt(segs, 10)
real = build_srt(segs, 10, durations=[8.0, 12.0])
check("sem durações, janelas nominais de 10s", "00:00:10,000" in nominal.split("\n\n")[1], nominal)
check("com durações reais, a 2ª fala começa em 8s", "00:00:08,000 -->" in real and "00:00:19,800" in real, real)

# -------------------------------------------- render: retomada e recuperação
db.init_db()
project = studio.create_project("auditoria")
pipe = P.create_pipeline(project["id"], {**ABECE, "voiceover_language": "pt-BR"})
pid = pipe["id"]
real_run = studio.run_generation
state = {"fail_piece": 2, "calls": 0}


def flaky(generation_id: str) -> None:
    state["calls"] += 1
    gen = studio.get_generation(generation_id)
    if state["fail_piece"] and gen["label"].startswith(f"Peça {state['fail_piece']}"):
        db.update("generations", generation_id, {"status": "failed", "error": "falha simulada do provider"})
        return
    real_run(generation_id)


studio.run_generation = flaky
P.studio.run_generation = flaky


def wait_status(want: set[str], timeout: float = 60.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = P.get_pipeline(pid)["status"]
        if status in want:
            return status
        time.sleep(0.3)
    return P.get_pipeline(pid)["status"]


P.render(pid)
check("render falha na peça 2", wait_status({"failed", "completed"}) == "failed")
first_ids = [r["id"] for r in P.get_pipeline(pid)["renders"]]
state["fail_piece"] = 0
state["calls"] = 0
info = P.render(pid)
check("retomada reaproveita a peça 1", info["resumed_from"] == 1, str(info))
check("retomada termina", wait_status({"failed", "completed"}) == "completed", P.get_pipeline(pid).get("error") or "")
after = P.get_pipeline(pid)["renders"]
check("peça 1 é a mesma geração (não pagou de novo)", after[0]["id"] == first_ids[0])
check("só 2 peças novas foram geradas", state["calls"] == 2, str(state["calls"]))
check("peça 2 estende a peça 1", after[1]["parent_id"] == after[0]["id"])

info = P.render(pid)
check("pipeline concluído refaz tudo (re-roll)", info["resumed_from"] == 0, str(info))
wait_status({"completed", "failed"})

# prompt editado invalida a cadeia dali em diante
state["fail_piece"] = 3
P.render(pid, force=True)
wait_status({"failed", "completed"})
board = P.get_pipeline(pid)["storyboard"]
board["segments"][0]["prompt"] += " Extra."
P.update_pipeline(pid, storyboard=board)
check("falha na peça 3 deixa pipeline failed", P.get_pipeline(pid)["status"] == "failed")
state["fail_piece"] = 0
info = P.render(pid)
check("prompt da peça 1 editado: nada é reaproveitado", info["resumed_from"] == 0, str(info))
wait_status({"completed", "failed"})

studio.run_generation = real_run
P.studio.run_generation = real_run

# reinício do servidor no meio do render
db.update("pipelines", pid, {"status": "rendering"})
gid = P.get_pipeline(pid)["renders"][0]["id"]
db.update("generations", gid, {"status": "running"})
recovered = studio.recover_interrupted()
check("reinício libera pipeline preso em 'rendering'", P.get_pipeline(pid)["status"] == "failed" and recovered >= 1)
check("mensagem explica o motivo", "reiniciado" in (P.get_pipeline(pid)["error"] or ""))
check("geração presa vira failed", studio.get_generation(gid)["status"] == "failed")
check("pipeline recuperado pode renderizar de novo", P.render(pid, force=True)["status"] == "rendering")
wait_status({"completed", "failed"})

print(f"\n{'Tudo verde.' if not failures else str(len(failures)) + ' FALHA(S)'}")
raise SystemExit(1 if failures else 0)
