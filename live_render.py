#!/usr/bin/env python3
"""Renderização ao vivo no terminal: contexto → storyboard → render real.

Usa o pipeline de verdade (o mesmo da UI) com o provider escolhido e mostra cada
etapa conforme acontece. Credenciais só por variável de ambiente, nunca em arquivo:

    export HF_CREDENTIALS='KEY_ID:KEY_SECRET'           # ou HF_API_KEY_ID / HF_API_KEY_SECRET
    python3 live_render.py --provider higgsfield --duration 10
    python3 live_render.py --provider higgsfield --model kling-video/v3.0/std/text-to-video

Sem argumentos de provider roda no mock (offline, sem gastar nada).
Um render real GASTA créditos: o script mostra o plano e pede confirmação (use --yes
para pular).
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

# Preset do input de validação (ABECE · Copilot). Troque pelos seus campos.
PRESET = dict(
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
    voiceover_language="en-US",
)

CLEAR = "\r\x1b[2K"


def say(text: str = "") -> None:
    print(text, flush=True)


def step(label: str) -> None:
    say(f"\n\x1b[1m▸ {label}\x1b[0m")


def parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", default="mock", choices=("mock", "higgsfield", "gemini", "azure"))
    ap.add_argument("--model", default="", help="higgsfield: caminho do modelo de texto→vídeo "
                    "(padrão do .env: wan/v2.6/text-to-video; ex.: kling-video/v3.0/std/text-to-video)")
    ap.add_argument("--image-model", default="", help="higgsfield: modelo de imagem→vídeo usado no encadeamento")
    ap.add_argument("--duration", type=int, default=10, help="segundos do filme (múltiplos de 10, até 40)")
    ap.add_argument("--resolution", default="720p")
    ap.add_argument("--aspect", default="16:9")
    ap.add_argument("--yes", action="store_true", help="não pedir confirmação antes de gastar créditos")
    ap.add_argument("--out", default="", help="pasta de saída (padrão: ./live-output)")
    return ap.parse_args()


def main() -> int:
    args = parse()

    creds = os.environ.get("HF_CREDENTIALS", "")
    if creds and ":" in creds:
        os.environ.setdefault("HF_API_KEY_ID", creds.split(":", 1)[0])
        os.environ.setdefault("HF_API_KEY_SECRET", creds.split(":", 1)[1])
    os.environ["VF_PROVIDER"] = args.provider
    if args.model:
        os.environ["VF_HIGGSFIELD_TEXT_MODEL"] = args.model
    if args.image_model:
        os.environ["VF_HIGGSFIELD_IMAGE_MODEL"] = args.image_model
    os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-live-"))

    from app import db, pipeline as P, studio
    from app.providers import ProviderError, get_provider

    db.init_db()
    real = args.provider != "mock"
    if real:
        try:
            provider = get_provider(args.provider)
        except ProviderError as exc:
            say(f"\x1b[31m✗ {exc}\x1b[0m")
            return 2
        label = (f"{args.provider} · {provider.text_model} (+ {provider.image_model})"
                 if args.provider == "higgsfield" else args.provider)
    else:
        label = "mock (offline)"

    step(f"1/4 Contexto  ·  {PRESET['title']}")
    say(f"   {PRESET['brand']} · {PRESET['product']}")
    context = {**PRESET, "duration_seconds": args.duration, "aspect_ratio": args.aspect,
               "resolution": args.resolution}
    project = studio.create_project("render ao vivo")

    t0 = time.time()
    pipe = P.create_pipeline(project["id"], context)
    board, story = pipe["storyboard"], pipe["story"]
    step("2/4 Storytelling e storyboard")
    say(f"   fonte: {story.get('source')} / {board.get('source')}  ({time.time() - t0:.1f}s)")
    for act in story["acts"]:
        say(f"   Ato {act['n']} {act['name']:<20} {act['timecode']}  {len(act['vo'].split()):>2} palavras")
    for seg in board["segments"]:
        say(f"   Peça {seg['index']} {seg['timecode']}  atos {seg['acts']}  {seg['mode']}")
        say(f"      VO: {seg['vo'][:110]}{'…' if len(seg['vo']) > 110 else ''}")
    over = (board.get("vo_overflow") or {}).get("pieces") or []
    if over:
        limit = board["vo_overflow"]["limit_words"]
        say("\n\x1b[33m   ⚠ ADVERTÊNCIA: a locução não cabe na peça "
            + "; ".join(f"peça {p['index']} tem {p['words']} palavras" for p in over)
            + f" (cabem ~{limit}). O modelo vai acelerar ou cortar a fala.\x1b[0m")

    pieces = len(board["segments"])
    say(f"\n   provider: \x1b[1m{label}\x1b[0m · {pieces} peça(s) de 10s · {args.resolution} · {args.aspect}")
    if real and not args.yes:
        say("\x1b[33m   Este render GASTA créditos reais.\x1b[0m")
        if input("   Continuar? [s/N] ").strip().lower() not in ("s", "sim", "y", "yes"):
            say("   Cancelado. Nada foi enviado.")
            return 1

    step("3/4 Renderizando")
    P.render(pipe["id"])
    started = time.time()
    shown: set[str] = set()
    status = "rendering"
    while status == "rendering":
        current = P.get_pipeline(pipe["id"])
        status = current["status"]
        for r in current["renders"]:
            line = f"   Peça {r['segment_index']}: {r['status']}"
            if r["status"] in ("completed", "failed") and r["id"] not in shown:
                shown.add(r["id"])
                detail = r.get("error") or ""
                say(f"{CLEAR}{line} \x1b[{'32' if r['status'] == 'completed' else '31'}m"
                    f"{'✓' if r['status'] == 'completed' else '✗'}\x1b[0m {detail}")
        working = [r for r in current["renders"] if r["status"] not in ("completed", "failed")]
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.time() * 8) % 10]
        waiting = f"peça {working[0]['segment_index']} ({working[0]['status']})" if working else "iniciando"
        print(f"{CLEAR}   {spinner} {waiting} · {time.time() - started:5.0f}s", end="", flush=True)
        if status == "rendering":
            time.sleep(0.5)
    print(CLEAR, end="")

    final = P.get_pipeline(pipe["id"])
    if final["status"] != "completed":
        say(f"\x1b[31m✗ Render terminou como '{final['status']}': {final.get('error')}\x1b[0m")
        say("   Rodar de novo retoma das peças já concluídas (não paga duas vezes).")
        return 3

    step("4/4 Resultado")
    out = Path(args.out or "live-output").resolve()
    out.mkdir(parents=True, exist_ok=True)
    for r in final["renders"]:
        src = Path(r["asset_path"])
        dest = out / f"peca-{r['segment_index']:02d}{src.suffix or '.mp4'}"
        dest.write_bytes(src.read_bytes())
        say(f"   ✓ {dest}  ({dest.stat().st_size / 1e6:.2f} MB)  interaction_id={r.get('interaction_id')}")
    say(f"\n   total {time.time() - started:.0f}s · pasta: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
