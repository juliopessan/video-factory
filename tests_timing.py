"""Testes do tempo em ticks e das miniaturas.

Duas ideias trazidas do OpenCut (MIT): tempo como inteiro a 120.000 ticks por
segundo, e poster por clipe no lugar de um <video> por card.

Roda offline: `python3 tests_timing.py`.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("VF_PROVIDER", "mock")
os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-timing-"))

from app import postproduction as post  # noqa: E402
from app.timing import (  # noqa: E402
    FRAME_RATES,
    TICKS_PER_SECOND,
    FrameRate,
    seconds_to_ticks,
    snap_to_frame,
    split_ticks,
    srt_timestamp,
)

falhas: list[str] = []


def check(label: str, cond: bool, detalhe: str = "") -> None:
    print(f"{'ok  ' if cond else 'FALHA'} {label}{'' if cond else ' -> ' + str(detalhe)}")
    if not cond:
        falhas.append(label)


# ------------------------------------------------------------------- ticks

check("120.000 ticks por segundo", TICKS_PER_SECOND == 120_000)
esperado = {"23.976": 5005, "24": 5000, "25": 4800, "29.97": 4004, "30": 4000,
            "50": 2400, "59.94": 2002, "60": 2000}
for nome, ticks in esperado.items():
    check(f"{nome}fps divide exato ({ticks} ticks/frame)",
          FRAME_RATES[nome].ticks_per_frame == ticks, FRAME_RATES[nome].ticks_per_frame)

check("frame rate é racional, não float", str(FRAME_RATES["29.97"]) == "30000/1001fps",
      str(FRAME_RATES["29.97"]))
try:
    FrameRate(7).ticks_per_frame
    check("frame rate que não divide é recusado", False, "não levantou")
except ValueError as exc:
    check("frame rate que não divide é recusado", "exata" in str(exc), str(exc))

# a 30fps cada frame vale 4.000 ticks: 1,01s cai no frame 30, 1,02s já é o 31
check("snap alinha ao frame mais próximo",
      snap_to_frame(seconds_to_ticks(1.010), FRAME_RATES["30"]) == 30 * 4000,
      snap_to_frame(seconds_to_ticks(1.010), FRAME_RATES["30"]))
check("snap sobe quando passa da metade do frame",
      snap_to_frame(seconds_to_ticks(1.020), FRAME_RATES["30"]) == 31 * 4000,
      snap_to_frame(seconds_to_ticks(1.020), FRAME_RATES["30"]))
check("snap não deixa resto",
      snap_to_frame(seconds_to_ticks(7.3), FRAME_RATES["29.97"]) % 4004 == 0)

partes = split_ticks(1_000_000, [10, 7, 3])
check("repartição soma exatamente o total", sum(partes) == 1_000_000, sum(partes))
check("repartição respeita os pesos", partes[0] > partes[1] > partes[2], partes)
check("repartição com resto não perde tick", sum(split_ticks(100, [1, 1, 1])) == 100)
check("repartição sem pesos devolve vazio", split_ticks(100, []) == [])
check("timestamp do srt", srt_timestamp(seconds_to_ticks(3661.5)) == "01:01:01,500",
      srt_timestamp(seconds_to_ticks(3661.5)))

# ---------------------------------------------- legendas sem deriva em float


def marcas(srt: str) -> list[tuple[str, str]]:
    return [tuple(l.split(" --> ")) for l in srt.splitlines() if " --> " in l]


longa = post.build_srt([{"vo": "palavra " * 40}, {"vo": "curta."}, {"vo": "outra " * 30}], 10)
pares = marcas(longa)
check("nenhuma legenda passa da janela da peça",
      all(not m[1].startswith(("00:00:10", "00:00:20", "00:00:30")) or m[1].endswith("9,800")
          for m in pares), str(pares))
check("as peças começam exatamente no segundo cheio",
      {"00:00:00,000", "00:00:10,000", "00:00:20,000"} <= {m[0] for m in pares}, str([m[0] for m in pares]))
check("as janelas fecham exatamente em 9,8s da peça",
      {"00:00:09,800", "00:00:19,800", "00:00:29,800"} <= {m[1] for m in pares}, str([m[1] for m in pares]))

# a soma das legendas de uma peça tem que fechar a janela, sem sobra
def para_ticks(marca: str) -> int:
    h, m, resto = marca.split(":")
    s, ms = resto.split(",")
    return (int(h) * 3600 + int(m) * 60 + int(s)) * TICKS_PER_SECOND + int(ms) * (TICKS_PER_SECOND // 1000)


primeira = [m for m in pares if para_ticks(m[0]) < 10 * TICKS_PER_SECOND]
soma = sum(para_ticks(f) - para_ticks(i) for i, f in primeira)
check("as legendas da peça 1 cobrem a janela inteira",
      soma == seconds_to_ticks(9.8), f"{soma} vs {seconds_to_ticks(9.8)}")

# ------------------------------------------------------------- miniaturas

if post.available():
    tmp = Path(tempfile.mkdtemp(prefix="vf-poster-"))
    clipe = tmp / "clipe.mp4"
    subprocess.run(
        [post.FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=1280x720:rate=25:duration=6", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(clipe)], check=True, timeout=300)
    poster = post.extract_poster(clipe, tmp / "poster.jpg")
    check("poster é gerado", poster.exists() and poster.stat().st_size > 0)
    check("poster é bem menor que o vídeo", poster.stat().st_size < clipe.stat().st_size / 5,
          f"{poster.stat().st_size} vs {clipe.stat().st_size}")
    largura = subprocess.run(
        [post.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width", "-of", "csv=p=0", str(poster)],
        capture_output=True, text=True).stdout.strip()
    check("poster sai em 640px", largura == "640", largura)
else:
    print("aviso  FFmpeg ausente: miniatura não exercitada")

print("\n" + ("FALHAS: " + ", ".join(falhas) if falhas else "Tudo verde."))
raise SystemExit(1 if falhas else 0)
