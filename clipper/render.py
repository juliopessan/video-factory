"""Render 9:16 com legenda queimada (ASS) e poster.

Layout padrão ("blur"): gameplay em largura total sobre fundo borrado, legenda
abaixo do gameplay. Com facecam ("stack"): gameplay no alto, facecam embaixo.
A legenda fica acima de ~22% do rodapé: a interface do TikTok cobre o fim do quadro.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .media import ClipperError, MediaInfo, ffmpeg_bin
from .transcribe import Segment, Word

OUT_W, OUT_H = 1080, 1920
CAPTION_MARGIN_V = 430          # px do rodapé até a base da legenda
CHUNK_WORDS, CHUNK_CHARS = 3, 18  # 18 caracteres em caixa alta cabem com folga nos 940 px úteis
HIGHLIGHT = "&H0000D7FF&"       # amarelo (ASS usa BGR)
WHITE = "&H00FFFFFF&"
PORTRAIT_RATIO = 0.7            # fonte já vertical: preenche a tela em vez de empilhar


@dataclass(frozen=True)
class Facecam:
    x: int
    y: int
    w: int
    h: int

    @classmethod
    def parse(cls, text: str) -> "Facecam":
        try:
            x, y, w, h = (int(p) for p in text.split(":"))
        except ValueError as exc:
            raise ClipperError("facecam deve ser x:y:w:h em pixels da fonte, ex.: 1500:800:400:280") from exc
        if min(w, h) <= 0 or min(x, y) < 0:
            raise ClipperError("facecam com valores inválidos")
        return cls(x, y, w, h)


# ------------------------------------------------------------------- legenda


def _ass_time(seconds: float) -> str:
    cs = max(int(round(seconds * 100)), 0)
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, c = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _escape(text: str) -> str:
    return re.sub(r"[{}\\]", "", text).upper()


def _chunks(words: list[Word]) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        projected = sum(len(w.text) + 1 for w in current) + len(word.text)
        gap = word.start - current[-1].end if current else 0.0
        if current and (len(current) >= CHUNK_WORDS or projected > CHUNK_CHARS or gap > 0.9):
            chunks.append(current)
            current = []
        current.append(word)
    if current:
        chunks.append(current)
    return chunks


def build_ass(
    segments: list[Segment],
    clip_start: float,
    clip_end: float,
    *,
    font: str = "Inter",
    size: int = 84,
) -> str:
    """ASS com tempos relativos ao início do corte; a palavra falada fica amarela."""
    words = [
        w for seg in segments for w in seg.words
        if w.end > clip_start and w.start < clip_end and w.text.strip()
    ]
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {OUT_W}\nPlayResY: {OUT_H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font},{size},{WHITE},{WHITE},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,2,2,"
        f"70,70,{CAPTION_MARGIN_V},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines: list[str] = []
    chunks = _chunks(words)
    for ci, chunk in enumerate(chunks):
        next_chunk_start = chunks[ci + 1][0].start if ci + 1 < len(chunks) else clip_end + 1
        for wi, active in enumerate(chunk):
            start = max(active.start, clip_start) - clip_start
            if wi + 1 < len(chunk):
                end = chunk[wi + 1].start
            else:
                end = min(active.end + 0.25, next_chunk_start)
            end = min(end, clip_end) - clip_start
            if end - start < 0.04:
                continue
            parts = [
                f"{{\\c{HIGHLIGHT}}}{_escape(w.text)}{{\\c{WHITE}}}" if w is active else _escape(w.text)
                for w in chunk
            ]
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"


# ------------------------------------------------------------------- vídeo

_BLUR_BG = (
    "scale=108:192:force_original_aspect_ratio=increase,crop=108:192,boxblur=3:2,"
    f"scale={OUT_W}:{OUT_H}:flags=bicubic,eq=brightness=-0.10"
)


# Cena "talk": a live mostra um painel (chat + câmera grande). Cena "game": gameplay com a facecam pequena no canto.
TALK_CAM = Facecam(452, 24, 632, 442)   # câmera grande da cena de conversa (px, fonte 1280x720)
_SCENE_REGION = "crop=iw*0.4375:ih*0.347:iw*0.5625:ih*0.653,scale=56:25,format=gray"  # logo/QR fixos do painel


def _frame_sig(source: Path, t: float) -> bytes | None:
    proc = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", str(source), "-frames:v", "1",
         "-vf", _SCENE_REGION, "-f", "rawvideo", "-"], capture_output=True)
    return proc.stdout if len(proc.stdout) == 56 * 25 else None


def scene_ref(source: Path, talk_at: float | None) -> bytes | None:
    """Assinatura do painel de conversa, tirada de um instante em que ele aparece."""
    return _frame_sig(source, talk_at) if talk_at is not None else None


def detect_scene(source: Path, start: float, end: float, ref: bytes | None, threshold: float = 22.0) -> str:
    if ref is None:
        return "game"
    hits = 0
    for f in (0.15, 0.35, 0.5, 0.65, 0.85):
        sig = _frame_sig(source, start + (end - start) * f)
        if sig and sum(abs(a - b) for a, b in zip(sig, ref)) / len(ref) < threshold:
            hits += 1
    return "talk" if hits >= 3 else "game"


def _crop(c: Facecam) -> str:
    return f"crop={c.w}:{c.h}:{c.x}:{c.y}"


def build_filtergraph(info: MediaInfo, ass_name: str, facecam: Facecam | None, fonts_dir: str | None,
                      layout: str = "fill", scene: str = "game", talk_cam: Facecam = TALK_CAM) -> str:
    ass = f"ass={ass_name}" + (f":fontsdir={fonts_dir}" if fonts_dir else "")
    if info.width / info.height <= PORTRAIT_RATIO:
        return (
            f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},{ass}[v]"
        )
    if scene == "talk" and facecam is not None and layout != "stack":
        # só a câmera grande, em largura total, sobre fundo borrado
        return (
            f"[0:v]split=2[bg][c];[bg]{_BLUR_BG}[bgb];[c]{_crop(talk_cam)},scale={OUT_W}:-2:flags=lanczos[cs];"
            f"[bgb][cs]overlay=0:(H-h)/2-120[v0];[v0]{ass}[v]"
        )
    if facecam is not None and layout != "stack":
        # facecam no topo (largura total) + gameplay recortado no centro, preenchendo o resto
        cam_h = int(round(OUT_W * facecam.h / facecam.w / 2)) * 2
        game_h = OUT_H - cam_h
        return (
            f"[0:v]split=2[g][c];[c]{_crop(facecam)},scale={OUT_W}:{cam_h}:flags=lanczos[cs];"
            f"[g]crop=w='trunc(ih*0.8889*{OUT_W}/{game_h}/2)*2':h='trunc(ih*0.8889/2)*2':x='(iw-ow)/2':y='ih*0.0556',"
            f"scale={OUT_W}:{game_h}:flags=lanczos[gs];[cs][gs]vstack[v0];[v0]{ass}[v]"
        )
    if facecam is None and layout == "fill":
        # tela cheia 9:16: escala pela altura e corta o centro (perde as laterais do 16:9)
        return f"[0:v]scale=-2:{OUT_H}:flags=lanczos,crop={OUT_W}:{OUT_H},{ass}[v]"
    if facecam is None:
        return (
            f"[0:v]split=2[bg][fg];[bg]{_BLUR_BG}[bgb];[fg]scale={OUT_W}:-2:flags=lanczos[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2-120[v0];[v0]{ass}[v]"
        )
    return (
        f"[0:v]split=3[bg][fg][fc];[bg]{_BLUR_BG}[bgb];[fg]scale={OUT_W}:-2:flags=lanczos[fgs];"
        f"[fc]crop={facecam.w}:{facecam.h}:{facecam.x}:{facecam.y},scale=640:-2[fcs];"
        f"[bgb][fgs]overlay=(W-w)/2:200[v0];[v0][fcs]overlay=(W-w)/2:860[v1];[v1]{ass}[v]"
    )


def render_clip(
    source: Path,
    info: MediaInfo,
    segments: list[Segment],
    start: float,
    end: float,
    out_dir: Path,
    stem: str,
    *,
    facecam: Facecam | None = None,
    font: str = "Inter",
    fonts_dir: str | None = None,
    layout: str = "fill",
    scene: str = "game",
) -> tuple[Path, Path]:
    """Renderiza `stem.mp4` e `stem.jpg` em `out_dir`. Devolve (vídeo, poster)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ass_path = out_dir / f"{stem}.ass"
    ass_path.write_text(build_ass(segments, start, end, font=font), encoding="utf-8")
    video, poster = out_dir / f"{stem}.mp4", out_dir / f"{stem}.jpg"
    duration = end - start
    cmd = [
        ffmpeg_bin(), "-y", "-v", "error", "-nostdin",
        "-ss", f"{start:.3f}", "-i", str(source.resolve()), "-t", f"{duration:.3f}",
        "-filter_complex", build_filtergraph(info, ass_path.name, facecam, fonts_dir, layout, scene),
        "-map", "[v]", "-map", "0:a:0?",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", video.name,
    ]
    # cwd na pasta do corte: o filtro `ass` recebe só o nome do arquivo, sem escapar caminho
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=out_dir)
    if proc.returncode != 0 or not video.is_file():
        raise ClipperError(f"FFmpeg falhou em {stem}: {proc.stderr.strip()[-400:]}")
    subprocess.run(
        [ffmpeg_bin(), "-y", "-v", "error", "-nostdin", "-ss", f"{duration / 3:.2f}", "-i", video.name,
         "-frames:v", "1", "-vf", "scale=360:-2", poster.name],
        capture_output=True, text=True, cwd=out_dir,
    )
    return video, poster
