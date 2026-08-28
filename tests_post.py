"""Testes do passo 5 — legendas, argv do FFmpeg e um export real de ponta a ponta.

Roda offline: `python3 tests_post.py` (o export real é pulado sem FFmpeg).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("VF_PROVIDER", "mock")
os.environ.setdefault("VF_STORAGE_DIR", tempfile.mkdtemp(prefix="vf-post-"))

from app import postproduction as post  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FALHA'} {label}{'' if condition else ' -> ' + detail}")
    if not condition:
        failures.append(label)


# ------------------------------------------------------------------ legendas

segments = [
    {"vo": "Neste exato momento, nossa maior barreira para o crescimento não é o mercado."},
    {"vo": "Mudamos a abordagem: mapeamento determinístico antes de construir."},
    {"vo": ""},  # peça sem locução não vira legenda
    {"vo": "Vamos migrar."},
]
srt = post.build_srt(segments, 10)
check("primeira legenda começa em zero", srt.splitlines()[1].startswith("00:00:00,000"), srt.splitlines()[1])
check("peça vazia não desloca as seguintes", "00:00:30,000 --> 00:00:39,800" in srt, srt)
check("numeração é sequencial", srt.startswith("1\n") and "\n3\n" in srt)
check("nenhuma linha passa de 42 colunas", max(len(l) for l in srt.splitlines()) <= 42, srt)
check("no máximo 2 linhas por legenda",
      all(len(block.strip().splitlines()) <= 4 for block in srt.split("\n\n") if block.strip()), srt)

# locução longa vira varias legendas dentro da janela, sem perder texto
longa = "palavra " * 40
multi = post.build_srt([{"vo": longa}], 10)
check("locução longa vira várias legendas", multi.count("-->") >= 3, str(multi.count("-->")))
check("nada é truncado", multi.count("palavra") == 40, str(multi.count("palavra")))
check("as legendas não passam da janela da peça", "00:00:09,800" in multi and "00:00:10" not in multi, multi)


def cue_durations(text: str) -> list[float]:
    def seconds(stamp: str) -> float:
        h, m, rest = stamp.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

    return [
        seconds(line.split(" --> ")[1]) - seconds(line.split(" --> ")[0])
        for line in text.splitlines()
        if " --> " in line
    ]


rabicho = post.build_srt([{"vo": "a" * 80 + " fim"}], 10)
check("nenhuma legenda pisca abaixo de 1.2s", min(cue_durations(rabicho)) >= 1.2,
      str([round(d, 2) for d in cue_durations(rabicho)]))
check("legendas de um mesmo trecho não se sobrepõem",
      all(d > 0 for d in cue_durations(srt)), str(cue_durations(srt)))

# ------------------------------------------------------------ argv do FFmpeg

cmd = post.build_command(
    source=Path("/in.mp4"), destination=Path("/out.mp4"),
    frame_filter=post.frame_filter("9:16"), duration=30.0,
    subtitles=Path("/tmp/x.srt"), normalize_audio=True, fade=True,
)
vf = cmd[cmd.index("-vf") + 1]
af = cmd[cmd.index("-af") + 1]
check("9:16 recorta pelo centro", vf.startswith("crop=ih*9/16:ih,scale=1080:1920"), vf)
check("legenda entra no filtergraph", "subtitles='/tmp/x.srt'" in vf, vf)
check("fade de saída usa a duração real", "fade=t=out:st=29.20:d=0.8" in vf, vf)
check("áudio normalizado a -14 LUFS", "loudnorm=I=-14:TP=-1.5:LRA=11" in af, af)
check("saída é h264/aac com faststart", {"libx264", "aac", "+faststart"} <= set(cmd), " ".join(cmd))

plain = post.build_command(
    source=Path("/in.mp4"), destination=Path("/out.mp4"),
    frame_filter=post.frame_filter("16:9"), duration=None,
    subtitles=None, normalize_audio=False, fade=False,
)
check("sem opções não há -af", "-af" not in plain, " ".join(plain))
check("16:9 padrão preenche a tela", "crop=1920:1080" in plain[plain.index("-vf") + 1])
check("pad encaixa o quadro inteiro", "pad=1080:1920" in post.frame_filter("9:16", "pad"))
check("crop é o padrão", post.frame_filter("9:16") == post.FORMATS["9:16"][1])
check("sem duração não inventa fade de saída", "fade=t=out" not in plain[plain.index("-vf") + 1])

escaped = post.build_command(
    source=Path("/in.mp4"), destination=Path("/o.mp4"),
    frame_filter="null", subtitles=Path("C:\\v\\x.srt"), fade=False,
)
escaped_vf = escaped[escaped.index("-vf") + 1]
check("caminho de legenda com ':' e '\\' é escapado",
      "subtitles='C\\:\\\\v\\\\x.srt'" in escaped_vf, escaped_vf)

# ---------------------------------------------------- export real (se houver ffmpeg)

if post.available():
    tmp = Path(tempfile.mkdtemp(prefix="vf-ffmpeg-"))
    source, destination = tmp / "src.mp4", tmp / "out.mp4"
    subprocess.run(
        [post.FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-c:a", "aac",
         "-pix_fmt", "yuv420p", str(source)],
        check=True, timeout=120,
    )
    srt_file = tmp / "legenda.srt"
    srt_file.write_text(post.build_srt([{"vo": "Legenda de teste."}], 4), encoding="utf-8")
    command = post.build_command(source, destination, post.frame_filter("9:16"), 4.0, srt_file)
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    check("ffmpeg roda o comando montado", result.returncode == 0, result.stderr[:300])
    check("arquivo de saída existe", destination.exists() and destination.stat().st_size > 0)
    check("duração preservada", abs((post.probe_duration(destination) or 0) - 4) < 0.35,
          str(post.probe_duration(destination)))
    probe = subprocess.run(
        [post.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(destination)],
        capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    check("saída 9:16 tem 1080x1920", probe == "1080,1920", probe)
else:
    print("aviso  FFmpeg ausente: export real não exercitado")

# ------------------------------------------------ fonte que não é vídeo (mock)

try:
    post._require_video({"mime_type": "image/svg+xml", "provider": "mock"})
    check("clipe do mock é recusado no export", False, "não levantou erro")
except Exception as exc:
    check("clipe do mock é recusado no export", "não é vídeo" in str(exc), str(exc))
    check("erro diz como resolver", "GEMINI_API_KEY" in str(exc), str(exc))

post._require_video({"mime_type": "video/mp4", "provider": "gemini"})
check("clipe de vídeo passa", True)

print("\n" + ("FALHAS: " + ", ".join(failures) if failures else "Tudo verde."))
raise SystemExit(1 if failures else 0)
