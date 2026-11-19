"""Testes do clipador, offline.

Gera uma "live" sintética de 4 min (640x360, tom baixo com um trecho barulhento
entre 120 s e 200 s) e uma transcrição com reações plantadas nesse trecho.
Roda: `python3 tests_clipper.py`.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

STORAGE = tempfile.mkdtemp(prefix="vf-clipper-")
os.environ.setdefault("VF_PROVIDER", "mock")
os.environ["VF_STORAGE_DIR"] = STORAGE

from clipper import MAX_SECONDS, MIN_SECONDS, jobs, media, pipeline, scoring  # noqa: E402
from clipper.render import Facecam, build_ass, render_clip  # noqa: E402
from clipper.transcribe import Segment, Word, load_srt, segments_from_json, segments_to_json, split_words  # noqa: E402

falhas: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        falhas.append(msg)
        print(f"FALHA: {msg}")


WORK = Path(tempfile.mkdtemp(prefix="vf-clipper-work-"))
LIVE = WORK / "live.mp4"
DURATION = 240


def make_live() -> None:
    subprocess.run(
        [media.ffmpeg_bin(), "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={DURATION}",
         "-f", "lavfi", "-i", f"sine=f=300:sample_rate=44100:duration={DURATION}",
         "-af", "volume='if(between(t,120,200),1.0,0.04)':eval=frame",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(LIVE)],
        check=True,
    )


def make_segments() -> list[Segment]:
    segs: list[Segment] = []
    calm = ["então a gente vai seguir pela estrada", "deixa eu ver o mapa aqui", "vou pegar essa missão agora",
            "tá tranquilo por enquanto", "vou guardar o carro na garagem"]
    hot = ["Meu deus, não acredito nisso!", "Caraca, a polícia tá atrás de mim!", "Socorro, olha isso, explodiu!",
           "Consegui escapar, que loucura!", "Cuidado, corre, corre!"]
    t, i = 2.0, 0
    while t < DURATION - 6:
        text = hot[i % len(hot)] if 120 <= t < 200 else calm[i % len(calm)]
        seg = Segment(t, t + 4.5, text)
        seg.words = split_words(seg)
        segs.append(seg)
        t += 5.0
        i += 1
    return segs


# ---------------------------------------------------------------- unitários

srt = WORK / "t.srt"
srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nOlá pessoal tudo bem\n\n2\n00:00:04,500 --> 00:00:06,000\nBora!\n", encoding="utf-8")
loaded = load_srt(srt)
check(len(loaded) == 2 and loaded[0].start == 1.0 and loaded[1].end == 6.0, "SRT lido com tempos corretos")
check(len(loaded[0].words) == 4 and abs(loaded[0].words[-1].end - 3.0) < 0.01, "palavras do SRT distribuídas até o fim do segmento")
check(segments_from_json(segments_to_json(loaded))[0].text == loaded[0].text, "JSON de transcrição faz ida e volta")

check(scoring.keyword_hits("Meu deus, não acredito!") >= 2.0, "léxico reconhece frases com acento")
check(scoring.keyword_hits("vou guardar o carro") == 0, "texto neutro não pontua")
check(scoring.keyword_hits("a polícia pegou o helicóptero") >= 1.5, "léxico reconhece ação do GTA")

# legenda: nenhum bloco passa de 18 caracteres por linha (cabe na tela em caixa alta)
long_words = [Word(i * 0.4, i * 0.4 + 0.35, w) for i, w in enumerate("helicóptero perseguição incrível caramba nossa".split())]
for ln in build_ass([Segment(0, 3, "x", long_words)], 0.0, 3.0).splitlines():
    if ln.startswith("Dialogue"):
        visible = ln.split(",", 9)[9]  # o texto é o 10º campo do evento ASS
        for tag in ("{\\c&H0000D7FF&}", "{\\c&H00FFFFFF&}"):
            visible = visible.replace(tag, "")
        check(len(visible) <= 18 or " " not in visible, f"bloco de legenda curto: {visible!r}")

ass = build_ass([Segment(10, 12, "oi gente tudo bem", split_words(Segment(10, 12, "oi gente tudo bem")))], 9.0, 20.0)
check("Dialogue: 0,0:00:01.00" in ass, "ASS tem tempo relativo ao início do corte (10 s - 9 s = 1 s)")
check("&H0000D7FF&" in ass and "OI" in ass, "ASS destaca a palavra ativa em maiúsculas")
check("PlayResX: 1080" in ass and "PlayResY: 1920" in ass, "ASS em 1080x1920")

try:
    Facecam.parse("1:2:3")
    check(False, "facecam inválida deveria falhar")
except media.ClipperError:
    pass
fc = Facecam.parse("1500:800:400:280")
check((fc.x, fc.y, fc.w, fc.h) == (1500, 800, 400, 280), "facecam x:y:w:h lida")

# pontuação sem FFmpeg: energia plantada em 120–200
rms = [-45.0] * (DURATION * 2)
for k in range(240, 400):
    rms[k] = -12.0
segments = make_segments()
feat = scoring.build_features(DURATION, rms, segments)
wins = scoring.find_windows(feat, segments, count=3)
check(len(wins) >= 1, "achou ao menos um candidato")
check(all(MIN_SECONDS <= w.length <= MAX_SECONDS + 2 for w in wins), f"duração dentro de 61–92 s: {[round(w.length, 1) for w in wins]}")
ordered = sorted(wins, key=lambda w: w.start)
check(all(ordered[i].end <= ordered[i + 1].start for i in range(len(ordered) - 1)), "janelas sem sobreposição")
best = max(wins, key=lambda w: w.score)
overlap = max(0.0, min(best.end, 200) - max(best.start, 120))
check(overlap >= 0.8 * best.length, f"melhor janela cai no trecho quente ({best.start:.0f}–{best.end:.0f})")
check(any(abs(best.start + scoring.PRE_ROLL - s.start) < 0.02 for s in segments), "início da janela cai em limite de frase")
check(any(abs(best.end - scoring.POST_ROLL - s.end) < 0.02 for s in segments), "fim da janela cai em limite de frase")
check(scoring.find_windows(scoring.build_features(30, [-30.0] * 60, []), [], count=3) == [], "gravação curta demais não gera corte")
check(scoring.make_title(best, segments) != "", "título gerado")

# adaptador do whisper com modelo simulado (o download do modelo real não roda aqui:
# valida o mapeamento de frases/palavras e o progresso, não a qualidade da transcrição)
import sys  # noqa: E402
import types  # noqa: E402


class _W:
    def __init__(self, start, end, word):
        self.start, self.end, self.word = start, end, word


class _S:
    def __init__(self, start, end, text, words):
        self.start, self.end, self.text, self.words = start, end, text, words


class _FakeModel:
    def __init__(self, name, device="auto", compute_type="auto"):
        _FakeModel.args = (name, device, compute_type)

    def transcribe(self, path, **kw):
        _FakeModel.kw = kw
        segs = [_S(0.0, 2.0, " Meu deus!", [_W(0.0, 0.8, " Meu"), _W(0.9, 1.6, " deus!")]),
                _S(3.0, 5.0, " sem palavras", None)]
        return iter(segs), types.SimpleNamespace(duration=10.0)


fake = types.ModuleType("faster_whisper")
fake.WhisperModel = _FakeModel
sys.modules["faster_whisper"] = fake
from clipper.transcribe import transcribe_whisper  # noqa: E402

seen: list[float] = []
out = transcribe_whisper("x.mp4", model="base", device="cpu", progress=seen.append)
check(_FakeModel.args == ("base", "cpu", "auto"), "modelo e dispositivo repassados ao whisper")
check(_FakeModel.kw["word_timestamps"] is True and _FakeModel.kw["vad_filter"] is True and _FakeModel.kw["language"] == "pt",
      "whisper pede palavras com tempo, VAD e idioma pt")
check([w.text for w in out[0].words] == ["Meu", "deus!"] and out[0].text == "Meu deus!", "palavras e texto sem espaços sobrando")
check(len(out[1].words) == 2 and out[1].words[0].start == 3.0, "segmento sem palavras ganha tempo proporcional")
check(seen == [0.2, 0.5], f"progresso proporcional à duração ({seen})")
del sys.modules["faster_whisper"]

# ------------------------------------------------------------- ponta a ponta

make_live()
info = media.probe(LIVE)
check(abs(info.duration - DURATION) < 1 and (info.width, info.height) == (640, 360) and info.has_audio, "probe da live sintética")

series = media.rms_series(LIVE)
check(abs(len(series) - DURATION * 2) <= 4, f"curva de energia tem ~2 pontos/s ({len(series)})")
check(sum(series[250:390]) / 140 > sum(series[20:200]) / 180 + 15, "energia do trecho barulhento é bem maior")

transcript = WORK / "live.json"
transcript.write_text(json.dumps(segments_to_json(make_segments()), ensure_ascii=False), encoding="utf-8")
logs: list[str] = []
manifest = pipeline.run_job(LIVE, transcript=str(transcript), count=2, log=logs.append)
job_id = manifest["job_id"]
clips = manifest["clips"]
check(len(clips) == 2, f"2 cortes pedidos, {len(clips)} gerados")
check(all(c["status"] == "pending" for c in clips), "cortes nascem pendentes")
top = min(clips, key=lambda c: c["rank"])
check(top["start"] >= 115 and top["end"] <= 205, f"corte #1 está no trecho quente ({top['start']}–{top['end']})")

for clip in clips:
    mp4 = jobs.job_dir(job_id) / "clips" / f"{clip['id']}.mp4"
    check(mp4.is_file() and clip["error"] is None, f"{clip['id']} renderizado")
    if not mp4.is_file():
        continue
    meta = json.loads(subprocess.run(
        [media.ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(mp4)],
        capture_output=True, text=True, check=True).stdout)
    v = next(s for s in meta["streams"] if s["codec_type"] == "video")
    dur = float(meta["format"]["duration"])
    check((v["width"], v["height"]) == (1080, 1920), f"{clip['id']} é 1080x1920 ({v['width']}x{v['height']})")
    check(MIN_SECONDS <= dur <= MAX_SECONDS + 2.5, f"{clip['id']} dura {dur:.1f}s (61–92)")
    check(any(s["codec_type"] == "audio" for s in meta["streams"]), f"{clip['id']} tem áudio")
    check((mp4.with_suffix(".jpg")).is_file(), f"{clip['id']} tem poster")
    check((mp4.with_suffix(".ass")).read_text().count("Dialogue:") > 20, f"{clip['id']} tem legenda")

# layout empilhado com facecam
stack_dir = WORK / "stack"
render_clip(LIVE, info, make_segments(), 125.0, 187.0, stack_dir, "stack", facecam=Facecam.parse("400:200:200:140"))
check((stack_dir / "stack.mp4").is_file(), "layout com facecam renderiza")

# rerun reaproveita cache (mesmo job, sem refazer a medição)
again = pipeline.run_job(LIVE, transcript=str(transcript), count=2, plan_only=True, log=logs.append)
check(again["job_id"] == job_id, "mesmo arquivo -> mesmo job")

# -------------------------------------------------------------------- servidor

try:
    from fastapi.testclient import TestClient
except Exception:  # httpx ausente
    TestClient = None
    print("aviso: httpx ausente, pulando testes do servidor")

if TestClient:
    from clipper.server import app

    api = TestClient(app)
    check(api.get("/").status_code == 200, "página da fila abre")
    listed = api.get("/api/jobs").json()
    check(any(j["job_id"] == job_id for j in listed), "job aparece na lista")
    detail = api.get(f"/api/jobs/{job_id}").json()
    check(all("monetizable" in c for c in detail["clips"]), "cortes trazem flag de monetização")
    cid = detail["clips"][0]["id"]
    r = api.patch(f"/api/jobs/{job_id}/clips/{cid}", json={"status": "approved", "title": "  Escapei da polícia!  "})
    check(r.status_code == 200 and r.json()["status"] == "approved" and r.json()["title"] == "Escapei da polícia!", "aprovar e renomear")
    check(api.patch(f"/api/jobs/{job_id}/clips/{cid}", json={"status": "xyz"}).status_code == 400, "status inválido é recusado")
    check(api.get(f"/media/{job_id}/{cid}.mp4").status_code == 200, "serve o vídeo")
    check(api.get(f"/media/{job_id}/..%2Fmanifest.json").status_code in (400, 404), "path traversal bloqueado")
    check(api.get(f"/media/{job_id}/manifest.json").status_code == 400, "só serve mp4 e jpg")
    check(api.get("/api/jobs/nao-existe").status_code == 404, "job inexistente dá 404")
    short = api.post(f"/api/jobs/{job_id}/clips/{cid}/trim", json={"start": 130, "end": 175})
    check(short.status_code == 200 and short.json()["monetizable"] is False, "refazer corte curto sinaliza que não monetiza")
    longer = api.post(f"/api/jobs/{job_id}/clips/{cid}/trim", json={"start": 125, "end": 190})
    check(longer.status_code == 200 and longer.json()["monetizable"] is True, "refazer corte de 65 s monetiza")
    api.patch(f"/api/jobs/{job_id}/clips/{cid}", json={"status": "approved"})
    exp = api.post(f"/api/jobs/{job_id}/export")
    check(exp.status_code == 200, "exporta aprovados")
    out = Path(exp.json()["path"])
    check(len(list(out.glob("*.mp4"))) == 1 and "#gta6" in (out / "legendas.txt").read_text(), "exportação tem mp4 e legendas.txt")

# nenhum aprovado -> erro claro
other = [c for c in jobs.read_manifest(job_id)["clips"] if c["id"] != clips[0]["id"]]
for c in jobs.read_manifest(job_id)["clips"]:
    jobs.update_clip(job_id, c["id"], {"status": "pending"})
try:
    jobs.export_approved(job_id)
    check(False, "exportar sem aprovados deveria falhar")
except media.ClipperError:
    pass

if falhas:
    print(f"\n{len(falhas)} falha(s)")
    raise SystemExit(1)
print("clipper: todos os testes passaram")
