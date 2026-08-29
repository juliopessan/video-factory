"""Testes da camada de marca (Remotion) e da composição sobre o filme.

O render do Remotion e a composição só rodam se o projeto estiver instalado
(`npm install` em brand-overlays/) e o FFmpeg presente; senão o teste avisa e
sai limpo.

Roda offline: `python3 tests_overlays.py`.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-overlay-"))

from app import overlays  # noqa: E402
from app import postproduction as post  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FALHA'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


# ------------------------------------------------------------------- props

contexto = {"brand": "Contoso", "product": "fábrica de migração", "cta": "Vamos migrar."}
lower = post.overlay_props(contexto, "LowerThird")
check("lower third usa marca e produto",
      lower["title"] == "Contoso" and lower["subtitle"] == "fábrica de migração", str(lower))
packshot = post.overlay_props(contexto, "Packshot")
check("packshot usa marca e CTA",
      packshot["brand"] == "Contoso" and packshot["claim"] == "Vamos migrar.", str(packshot))
check("sem marca cai no produto",
      post.overlay_props({"product": "x"}, "LowerThird")["title"] == "x")

try:
    overlays.render("NaoExiste")
    check("composição desconhecida é recusada", False, "não levantou")
except RuntimeError as exc:
    check("composição desconhecida é recusada", "NaoExiste" in str(exc), str(exc))

if not (overlays.available() and post.available()):
    print("aviso  Remotion ou FFmpeg ausentes: render e composição não exercitados")
    print("\n" + ("FALHAS: " + ", ".join(failures) if failures else "Tudo verde."))
    raise SystemExit(1 if failures else 0)

# ------------------------------------------------------- render com alfa

layer = overlays.render("LowerThird", {"title": "Teste", "subtitle": "camada", "accent": "#0f6cbd"})
check("remotion renderizou a camada", layer.exists() and layer.stat().st_size > 0, str(layer))
check("render é cacheado por props",
      overlays.render("LowerThird", {"title": "Teste", "subtitle": "camada", "accent": "#0f6cbd"}) == layer)

alpha = subprocess.run(
    [post.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
     "-of", "csv=p=0", str(layer)], capture_output=True, text=True).stdout.strip()
check("camada é vp8 (alfa em webm)", alpha == "vp8", alpha)

# ----------------------------------------------- composição sobre o filme

tmp = Path(tempfile.mkdtemp(prefix="vf-composite-"))
base = tmp / "base.mp4"
subprocess.run(
    [post.FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360:d=4:r=25",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-c:a", "aac",
     "-pix_fmt", "yuv420p", str(base)], check=True, timeout=300)

out = post.composite(base, layer, tmp / "com_camada.mp4", start_seconds=0)
check("composição gerou arquivo", out.exists() and out.stat().st_size > 0)
check("duração é a do filme, não a da camada", abs((post.probe_duration(out) or 0) - 4) < 0.4,
      str(post.probe_duration(out)))
check("áudio do filme é preservado",
      "audio" in subprocess.run(
          [post.FFPROBE, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
          capture_output=True, text=True).stdout)

# a legenda embutida antes da composição não pode sumir por causa da camada
com_legenda = tmp / "com_legenda.mp4"
srt = tmp / "legenda.srt"
srt.write_text(post.build_srt([{"vo": "Legenda de teste."}], 4), encoding="utf-8")
subprocess.run(post.build_command(base, com_legenda, post.frame_filter("16:9"), 4.0, srt),
               check=True, timeout=600)
composto = post.composite(com_legenda, layer, tmp / "camada_e_legenda.mp4")
tipos = subprocess.run(
    [post.FFPROBE, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(composto)],
    capture_output=True, text=True).stdout.split()
check("a faixa de legenda sobrevive à camada de marca",
      tipos == ["video", "audio", "subtitle"], str(tipos))

# a camada é 1920x1080 e o filme 640x360: sem scale2ref ela cairia fora do quadro
frame = tmp / "frame.png"
subprocess.run([post.FFMPEG, "-y", "-loglevel", "error", "-ss", "2", "-i", str(out),
                "-frames:v", "1", str(frame)], check=True, timeout=120)
from PIL import Image  # noqa: E402

pixels = Image.open(frame).convert("RGB")
size = pixels.size
claros = sum(
    1
    for y in range(size[1] * 2 // 3, size[1], 2)
    for x in range(0, size[0], 2)
    if min(pixels.getpixel((x, y))) > 180
)
check("camada aparece sobre o filme (texto claro no terço inferior)", claros > 50, f"{claros} pixels")
check("resolução do filme é preservada", size == (640, 360), str(size))

print("\n" + ("FALHAS: " + ", ".join(failures) if failures else "Tudo verde."))
raise SystemExit(1 if failures else 0)
