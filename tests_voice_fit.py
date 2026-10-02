"""Sugestão de locução que cabe na peça (ícone "Ajustar" da advertência).

Roda offline: `python3 tests_voice_fit.py`.
"""
from __future__ import annotations

import os
import tempfile

os.environ["VF_PROVIDER"] = "mock"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-fit-"))

from fastapi.testclient import TestClient  # noqa: E402

from app import textgen, voice_fit  # noqa: E402
from app.main import app  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FALHA'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


PIECE2 = ("Artificial Intelligence is already part of the workplace, but concepts such as Generative AI, "
          "LLMs, and Copilot may still seem complex and disconnected from everyday work. Copilot transforms "
          "advanced AI models into a simple interface connected to the user's tools and work context.")
PIECE3 = ("Understanding of AI fundamentals, greater confidence in using Copilot, and readiness for more "
          "productive collaboration between people and agents. Understand what powers Copilot and begin "
          "exploring new possibilities for your work.")

# --------------------------------------------------------------- local
for label, text in (("peça 2 ABECE", PIECE2), ("peça 3 ABECE", PIECE3)):
    r = voice_fit.fit_voiceover(text, 25, "en-US")
    check(f"{label}: cabe no limite", r["fits"] and r["words_after"] <= 25, str(r["words_after"]))
    check(f"{label}: origem local sinalizada", r["source"] == "local")
    check(f"{label}: termina em frase completa", r["suggestion"].endswith(".") and ".." not in r["suggestion"])
    check(f"{label}: não termina em palavra solta",
          r["suggestion"].rstrip(".").split()[-1].lower() not in voice_fit._DANGLING, r["suggestion"])

r2 = voice_fit.fit_voiceover(PIECE2, 25, "en-US")
check("peça 2: as duas ideias sobrevivem (problema e virada)",
      "workplace" in r2["suggestion"] and "Copilot transforms" in r2["suggestion"], r2["suggestion"])
check("peça 2: sem exemplos cortados pela metade", "such as" not in r2["suggestion"], r2["suggestion"])
r3 = voice_fit.fit_voiceover(PIECE3, 25, "en-US")
check("peça 3: o CTA continua inteiro", "begin exploring new possibilities for your work" in r3["suggestion"], r3["suggestion"])

short = voice_fit.fit_voiceover("Frase curta que já cabe.", 25)
check("texto que já cabe volta intacto, sem sugestão", short["source"] == "none" and short["fits"]
      and short["suggestion"] == short["original"])

pt = voice_fit.fit_voiceover(
    "Migrar sistemas legados manualmente leva meses e estoura prazo e orçamento, e a decisão fica baseada "
    "em suposição. Então mudamos a abordagem: mapeamento determinístico antes de construir, com custo exato por carga.",
    25, "pt-BR")
check("pt-BR também cabe", pt["fits"] and "Então mudamos a abordagem" in pt["suggestion"], pt["suggestion"])

noisy = voice_fit.fit_voiceover("palavra " * 200, 25)
check("texto sem pontuação cai no corte por palavra e cabe", noisy["fits"], str(noisy["words_after"]))

# ---------------------------------------------------------------- modelo
original_available, original_generate = textgen.available, textgen.generate_json
textgen.available = lambda: True
try:
    textgen.generate_json = lambda *a, **k: {"voiceover": "AI is already at work, and Copilot makes it simple."}
    r = voice_fit.fit_voiceover(PIECE2, 25, "en-US")
    check("sugestão do modelo válida é usada", r["source"] == "model" and r["fits"], str(r))

    textgen.generate_json = lambda *a, **k: {"voiceover": "word " * 40}
    r = voice_fit.fit_voiceover(PIECE2, 25, "en-US")
    check("modelo que estoura o limite é descartado (cai no local)", r["source"] == "local" and r["fits"], str(r))

    with_number = "Hoje 3 times levam 90 dias para migrar cada carga de trabalho, e isso " + "atrasa tudo " * 12
    textgen.generate_json = lambda *a, **k: {"voiceover": "Migrar leva muito tempo hoje."}
    r = voice_fit.fit_voiceover(with_number, 20, "pt-BR")
    check("modelo que perde um número do original é descartado", r["source"] == "local", str(r))

    def boom(*a, **k):
        raise textgen.TextGenError("sem cota")

    textgen.generate_json = boom
    r = voice_fit.fit_voiceover(PIECE2, 25, "en-US")
    check("falha do modelo cai no local sem quebrar", r["source"] == "local" and r["fits"], str(r))
finally:
    textgen.available, textgen.generate_json = original_available, original_generate

# ------------------------------------------------------------------- API
client = TestClient(app)
with client:
    project = client.get("/api/projects").json()[0]["id"]
    ctx = dict(product="Copilot", value="valor", problem=PIECE2.split(". ")[0], turning_point=PIECE2.split(". ")[1],
               cta="Explore.", audience="funcionários", voiceover_language="en-US", duration_seconds=30)
    pipeline = client.post(f"/api/projects/{project}/pipelines", json=ctx).json()
    pid = pipeline["id"]
    over = pipeline["storyboard"]["vo_overflow"]
    check("storyboard traz a advertência estruturada", over["limit_words"] == 25, str(over))
    config = client.get("/api/config").json()
    check("config expõe o limite de palavras para a UI", config["vo_limit_words"] == 25)

    answer = client.post(f"/api/pipelines/{pid}/fit-voiceover", json={"segment_index": 2, "text": PIECE2})
    body = answer.json()
    check("endpoint devolve a sugestão", answer.status_code == 200 and body["fits"] and body["words_before"] == 43, str(body))
    unsaved = client.post(f"/api/pipelines/{pid}/fit-voiceover", json={"segment_index": 1, "text": "Curta."}).json()
    check("usa o texto da tela (ainda não salvo), não o do banco", unsaved["original"] == "Curta.")
    check("não grava nada: a locução salva continua a mesma",
          client.get(f"/api/pipelines/{pid}").json()["storyboard"]["segments"][1]["vo"]
          == pipeline["storyboard"]["segments"][1]["vo"])
    check("peça inexistente devolve 404",
          client.post(f"/api/pipelines/{pid}/fit-voiceover", json={"segment_index": 9}).status_code == 404)

    # aplicar a sugestão (o que o botão "Aplicar" faz): salva e a advertência some
    board = client.get(f"/api/pipelines/{pid}").json()["storyboard"]
    board["segments"][1]["vo"] = body["suggestion"]
    for segment in board["segments"]:
        segment["prompt"] = ""
    saved = client.patch(f"/api/pipelines/{pid}", json={"storyboard": board}).json()
    check("aplicada, a advertência da peça 2 some",
          2 not in [p["index"] for p in saved["storyboard"]["vo_overflow"]["pieces"]], str(saved["storyboard"]["vo_overflow"]))
    check("o prompt recompilado leva a locução ajustada", body["suggestion"] in saved["storyboard"]["segments"][1]["prompt"])

print(f"\n{'Tudo verde.' if not failures else str(len(failures)) + ' FALHA(S)'}")
raise SystemExit(1 if failures else 0)
