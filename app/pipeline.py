"""Pipeline editorial: contexto -> storytelling -> storyboard -> resultado final.

O storytelling segue a estrutura executiva de 5 Atos (Gancho, Problema Real,
Ponto de Virada, Valor de Negocio, Call to Action) com locucao em PT-BR.
O storyboard converte os atos em segmentos de 10s com prompts em ingles no
template de producao (SCENE CONTEXT / ACTIVE REFERENCE / CHARACTERS / FIRST
FRAME / FORMAT MODE / ACTION AND CAMERA SEQUENCE / LIGHTING / PHYSICS / AUDIO).
"""
from __future__ import annotations

import json
import math

from . import db, studio, textgen
from .providers import capabilities as provider_capabilities
from .config import EXTENSION_SECONDS, MAX_CUMULATIVE_SECONDS, RESOLUTIONS, settings

SEGMENT_SECONDS = EXTENSION_SECONDS  # 10s por peca, como no modelo de referencia

# Padrão de script: intro → hook → meat → cta. Os cinco atos continuam sendo a
# espinha do filme; cada um carrega um beat do script, e o "meat" ocupa três
# atos porque é onde mora a substância (problema, virada e valor).
SCRIPT_BEATS = ("intro", "hook", "meat", "cta")

BEAT_BRIEF = {
    "intro": "Situe em uma frase: quem fala, sobre o quê e onde. Sem preâmbulo, sem saudação.",
    "hook": "Crie a tensão que segura o espectador: o risco de continuar como está, dito de "
            "forma concreta. É a frase que faz a pessoa não pular o vídeo.",
    "meat": "Entregue a substância: o custo real do jeito atual, a mudança de abordagem e o "
            "valor que ela produz. Concreto, sem adjetivo vazio.",
    "cta": "Feche com uma ação clara e curta. Uma frase, no imperativo ou no convite direto.",
}

# (nome do ato, o que ele carrega, beat do script)
ACT_BLUEPRINT = [
    ("O Gancho", "Intro e risco do status quo", "hook"),
    ("O Problema Real", "O custo do jeito atual", "meat"),
    ("O Ponto de Virada", "A nova abordagem", "meat"),
    ("O Valor de Negócio", "O resultado tangível", "meat"),
    ("Call to Action", "O fechamento", "cta"),
]

# O Ato 1 abre com a intro e vira hook em seguida: é um ato, dois beats.
ACT_OPENING_BEATS = {1: ("intro", "hook")}

DEFAULT_AESTHETIC = (
    "Premium photorealistic footage, ARRI Alexa 35 texture, high-end corporate "
    "tech-commercial lighting, sharp art direction, subtle natural grain, high dynamic range."
)

STORY_SYSTEM = """Voce e diretor criativo de comerciais enterprise premium.
Escreva um roteiro em 5 Atos para um filme publicitario curto, no formato executivo:
Ato 1 O Gancho (risco do status quo), Ato 2 O Problema Real, Ato 3 O Ponto de Virada,
Ato 4 O Valor de Negocio, Ato 5 Call to Action.

PADRAO DE SCRIPT — intro, hook, meat, cta. Todo roteiro segue esta ordem, e cada
ato declara o beat que carrega no campo `script_beat`:
- intro: a primeira frase do Ato 1. Situa quem fala, sobre o que e onde, em uma
  linha. Sem saudacao, sem "neste video", sem preambulo.
- hook: ainda no Ato 1. A tensao que segura o espectador — o risco concreto de
  continuar como esta. E a frase que faz a pessoa nao pular.
- meat: Atos 2, 3 e 4. A substancia: o custo real do jeito atual, a mudanca de
  abordagem e o valor que ela produz. Concreto, sem adjetivo vazio.
- cta: Ato 5. Uma acao clara e curta, no imperativo ou como convite direto.

Escreva o Ato 1 como duas frases nessa ordem: primeiro a intro, depois o hook.

Regras:
- A locucao (`vo`) e SEMPRE em portugues do Brasil, na primeira pessoa do plural, curta e
  falavel dentro do tempo do ato (aproximadamente 2,5 palavras por segundo).
- `action_camera` e SEMPRE em ingles, escrito como direcao de fotografia: tipo de corte,
  lente/campo de visao em graus, altura e movimento de camera, acao dos personagens.
  Use cortes motivados (HARD CUT, MATCH CUT, WHIP CUT) e uma ideia visual nova por ato.
- Nada de subtitulos, marcas de terceiros ou musica licenciada.
- Nunca invente numeros, clientes ou resultados que nao estejam no contexto.
- `script_beat` e um de: intro, hook, meat, cta. O Ato 1 usa "hook" (ele abre com
  a intro e emenda no hook), os Atos 2 a 4 usam "meat" e o Ato 5 usa "cta"."""

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "aesthetic_base": {"type": "string"},
        "acts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "name": {"type": "string"},
                    "beat": {"type": "string"},
                    "script_beat": {"type": "string", "enum": list(SCRIPT_BEATS)},
                    "timecode": {"type": "string"},
                    "vo": {"type": "string"},
                    "action_camera": {"type": "string"},
                },
                "required": ["n", "name", "beat", "script_beat", "timecode", "vo", "action_camera"],
            },
        },
        "direction_notes": {
            "type": "object",
            "properties": {"music": {"type": "string"}, "pacing": {"type": "string"}},
            "required": ["music", "pacing"],
        },
    },
    "required": ["title", "logline", "aesthetic_base", "acts", "direction_notes"],
}

STORYBOARD_SYSTEM = """Voce transforma um roteiro de 5 Atos em um storyboard de producao
para um modelo de video generativo. Divida o filme em segmentos de 10 segundos.

Para cada segmento devolva:
- `script_beats`: quais beats do padrao intro/hook/meat/cta o segmento cobre, na
  ordem em que aparecem (o primeiro segmento comeca com intro e hook);
- `vo`: a locucao em portugues que roda naquele trecho (junte os atos cobertos);
- `shot_sequence`: em ingles, a sequencia de planos com cortes motivados, campo de visao
  em graus, altura e movimento de camera, acao dos personagens e transicoes;
- `first_frame`: em ingles, o que ja esta visivel no primeiro frame (sem plano de
  estabelecimento vazio, sem revelacao atrasada do conceito);
- `continuity`: em ingles, o que precisa permanecer identico ao segmento anterior
  (identidade dos personagens, figurino, paleta, objeto-heroi).

Os segmentos 2 em diante continuam a MESMA tomada e comecam com "Continue the video."."""

STORYBOARD_SCHEMA = {
    "type": "object",
    "properties": {
        "scene_context": {"type": "string"},
        "characters": {"type": "string"},
        "format_mode": {"type": "string"},
        "lighting": {"type": "string"},
        "physics": {"type": "string"},
        "audio": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "timecode": {"type": "string"},
                    "acts": {"type": "array", "items": {"type": "integer"}},
                    "script_beats": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(SCRIPT_BEATS)},
                    },
                    "vo": {"type": "string"},
                    "first_frame": {"type": "string"},
                    "shot_sequence": {"type": "string"},
                    "continuity": {"type": "string"},
                },
                "required": ["index", "timecode", "acts", "vo", "first_frame", "shot_sequence", "continuity"],
            },
        },
    },
    "required": ["scene_context", "characters", "format_mode", "lighting", "physics", "audio", "segments"],
}


# --------------------------------------------------------------------------- contexto


def normalize_context(raw: dict) -> dict:
    duration = int(raw.get("duration_seconds") or 30)
    duration = max(SEGMENT_SECONDS, min(duration, MAX_CUMULATIVE_SECONDS))
    duration = int(math.ceil(duration / SEGMENT_SECONDS) * SEGMENT_SECONDS)
    return {
        "brand": (raw.get("brand") or "").strip(),
        "product": (raw.get("product") or "").strip(),
        "audience": (raw.get("audience") or "").strip(),
        "problem": (raw.get("problem") or "").strip(),
        "turning_point": (raw.get("turning_point") or "").strip(),
        "value": (raw.get("value") or "").strip(),
        "cta": (raw.get("cta") or "").strip(),
        "characters": (raw.get("characters") or "").strip(),
        "aesthetic": (raw.get("aesthetic") or "").strip() or DEFAULT_AESTHETIC,
        "reference_note": (raw.get("reference_note") or "").strip(),
        "reference_asset_id": raw.get("reference_asset_id") or None,
        "aspect_ratio": raw.get("aspect_ratio") or "16:9",
        "resolution": raw.get("resolution") or "720p",
        "duration_seconds": duration,
    }


def _validate_context(context: dict) -> None:
    if not context["product"]:
        raise studio.StudioError("Descreva o produto ou servico no contexto.")
    if not context["value"]:
        raise studio.StudioError("Descreva o valor de negocio no contexto.")
    if context["reference_asset_id"]:
        studio.get_asset(context["reference_asset_id"])


def _segment_count(context: dict) -> int:
    return max(1, context["duration_seconds"] // SEGMENT_SECONDS)


def _timecode(start: int, end: int) -> str:
    fmt = lambda s: f"{s // 60}:{s % 60:02d}"  # noqa: E731 - helper local curto
    return f"{fmt(start)}–{fmt(end)}"


# --------------------------------------------------------------------------- storytelling


def _context_brief(context: dict) -> str:
    return json.dumps(context, ensure_ascii=False, indent=2)


def build_story(context: dict) -> dict:
    """Passo 2. Cai no fallback deterministico se nao houver modelo de texto."""
    total = context["duration_seconds"]
    if textgen.available():
        prompt = (
            f"Contexto do filme (JSON):\n{_context_brief(context)}\n\n"
            f"Duracao total: {total} segundos, dividida entre os 5 atos de forma proporcional "
            "ao peso narrativo (gancho curto, virada e valor mais longos). "
            "Preencha o timecode de cada ato em mm:ss–mm:ss."
        )
        try:
            story = textgen.generate_json(STORY_SYSTEM, prompt, STORY_SCHEMA)
            story["source"] = "model"
            return story
        except textgen.TextGenError as exc:
            story = _fallback_story(context)
            story["warning"] = f"Storytelling gerado localmente: {exc}"
            return story
    story = _fallback_story(context)
    story["warning"] = "Sem GEMINI_API_KEY: storytelling montado a partir do template local."
    return story


def _fallback_story(context: dict) -> dict:
    total = context["duration_seconds"]
    weights = [0.17, 0.23, 0.20, 0.23, 0.17]
    brand = context["brand"] or "a companhia"
    problem = context["problem"] or "a nossa própria fundação"
    turning = context["turning_point"] or "mapeamos tudo de forma determinística antes de construir"
    produto = context["product"]
    publico = context["audience"] or "quem decide"
    lines = [
        # Ato 1 = intro + hook, nessa ordem
        f"{brand} constrói {produto} para {publico}. "
        f"E, neste exato momento, a maior barreira para o crescimento não é o mercado: é {problem}.",
        "Resolver isso do jeito manual consome meses — e decisões baseadas em suposição estouram prazo e orçamento.",
        f"Então mudamos a abordagem: {turning}.",
        f"O resultado? {context['value']}.",
        context["cta"] or "Não é apenas uma mudança técnica. É velocidade de mercado. Vamos começar.",
    ]
    shots = [
        "OPENING SHOT — 107° wide rectilinear view, camera 60 cm above a glossy dark glass table. "
        "The hero concept looms large in the immediate foreground while the lead specialist leans "
        "toward the lens from midground and points directly at the problem. Rapid tabletop push-in.",
        "WHIP CUT — a glowing portal fills the frame and the camera bursts through into the legacy "
        "environment. 84° classic wide, waist height, stabilized dolly moving backward as the team "
        "strides toward the lens carrying the old, heavy artifact.",
        "MATCH CUT to OVERHEAD SHOT — perfect top-down view of the team in a clean radial composition "
        "around a giant circular glowing table, passing work packets clockwise. Rapid 18° macro inserts "
        "of the new structure forming with precise rack focus.",
        "LOW-ANGLE HERO SHOT — 107° wide rectilinear view from floor height. The team forms a loose "
        "diamond around the central pedestal; the camera cranes rapidly upward between them as they "
        "turn and look into the lens, expressions shifting into confident smiles.",
        "FINAL GROUP SHOT to PACKSHOT — fast curved dolly around the group for a synchronized swipe, "
        "then HARD CUT to a pristine studio background where the hero object stands on a mirror-polished "
        "pedestal. Slow 29° short-telephoto push, premium parallax, razor-sharp.",
    ]
    acts, cursor = [], 0
    for i, (name, beat, script_beat) in enumerate(ACT_BLUEPRINT):
        seconds = round(total * weights[i])
        if i == len(ACT_BLUEPRINT) - 1:
            seconds = total - cursor
        acts.append(
            {
                "n": i + 1,
                "name": name,
                "beat": beat,
                "script_beat": script_beat,
                "timecode": _timecode(cursor, cursor + seconds),
                "vo": lines[i],
                "action_camera": shots[i],
            }
        )
        cursor += seconds
    return {
        "title": f"{brand}: {context['product']}"[:120],
        "logline": f"Um filme de {total}s sobre {context['product']} para {context['audience'] or 'decisores de negócio'}.",
        "aesthetic_base": context["aesthetic"],
        "acts": acts,
        "direction_notes": {
            "music": "Baixo tenso e denso nos Atos 1 e 2; a trilha abre limpa e inspiradora no Ato 3 e "
                     "culmina em um chime de ativação no Ato 5.",
            "pacing": "Ritmo energético com cortes motivados; apenas o packshot final segura mais tempo.",
        },
        "source": "template",
    }


# --------------------------------------------------------------------------- storyboard


def build_storyboard(context: dict, story: dict) -> dict:
    """Passo 3. Agrupa os atos em segmentos de 10s e monta o prompt de cada peca."""
    count = _segment_count(context)
    if textgen.available():
        prompt = (
            f"Contexto (JSON):\n{_context_brief(context)}\n\n"
            f"Roteiro em 5 atos (JSON):\n{json.dumps(story, ensure_ascii=False)}\n\n"
            f"Monte exatamente {count} segmentos de {SEGMENT_SECONDS} segundos, cobrindo os 5 atos em ordem."
        )
        try:
            board = textgen.generate_json(STORYBOARD_SYSTEM, prompt, STORYBOARD_SCHEMA)
            board["source"] = "model"
        except textgen.TextGenError as exc:
            board = _fallback_storyboard(context, story, count)
            board["warning"] = f"Storyboard montado localmente: {exc}"
    else:
        board = _fallback_storyboard(context, story, count)
        board["warning"] = "Sem GEMINI_API_KEY: storyboard montado a partir do template local."

    segments = board.get("segments") or []
    acts_by_number = {a.get("n"): a for a in (story.get("acts") or [])}
    for index, segment in enumerate(segments):
        segment["index"] = index + 1
        if not segment.get("script_beats"):
            covered = [acts_by_number[n] for n in (segment.get("acts") or []) if n in acts_by_number]
            segment["script_beats"] = _beats_for(covered) or (["intro", "hook"] if index == 0 else ["meat"])
        segment.setdefault("timecode", _timecode(index * SEGMENT_SECONDS, (index + 1) * SEGMENT_SECONDS))
        segment["duration_seconds"] = SEGMENT_SECONDS
        segment["mode"] = "extend" if index else _first_mode(context)
        segment["prompt"] = render_prompt(context, story, board, segment, index)
    return board


def _first_mode(context: dict) -> str:
    return "image_to_video" if context["reference_asset_id"] else "text_to_video"


def chaining_strategy(provider: str | None = None) -> str:
    """Como as peças se ligam: `extend` (o modelo continua a interação) ou
    `keyframe` (a peça seguinte parte do último frame da anterior)."""
    return "extend" if provider_capabilities(provider).get("extend", True) else "keyframe"


def _beats_for(acts: list[dict]) -> list[str]:
    """Beats do script cobertos por um conjunto de atos, sem repetir e em ordem."""
    beats: list[str] = []
    for act in acts:
        for beat in ACT_OPENING_BEATS.get(act.get("n"), (act.get("script_beat") or "meat",)):
            if beat not in beats:
                beats.append(beat)
    return beats


def _fallback_storyboard(context: dict, story: dict, count: int) -> dict:
    acts = story.get("acts") or []
    per_segment = max(1, math.ceil(len(acts) / count))
    segments = []
    for index in range(count):
        chunk = acts[index * per_segment : (index + 1) * per_segment] or acts[-1:]
        segments.append(
            {
                "index": index + 1,
                "timecode": _timecode(index * SEGMENT_SECONDS, (index + 1) * SEGMENT_SECONDS),
                "acts": [a.get("n") for a in chunk],
                "script_beats": _beats_for(chunk),
                "vo": " ".join(a.get("vo", "") for a in chunk).strip(),
                "first_frame": (
                    "The first visible frame already contains the hero concept in the extreme foreground "
                    "with the lead specialist directly behind it. No empty establishing shot."
                    if index == 0
                    else "The frame continues exactly from the previous shot, same subjects and lighting."
                ),
                "shot_sequence": " ".join(a.get("action_camera", "") for a in chunk).strip(),
                "continuity": (
                    "Same character identities, wardrobe, hero object and color world as the previous segment."
                ),
            }
        )
    return {
        "scene_context": (
            f"A premium {context['duration_seconds']}-second enterprise commercial "
            f"({count} pieces of {SEGMENT_SECONDS}s each) for {context['product']}"
            + (f" by {context['brand']}" if context["brand"] else "")
            + ". Visionary, high-tech, sophisticated and highly polished. Energetic pacing, wide-angle "
            "intimacy, modern corporate visual language and inventive graphic transitions."
        ),
        "characters": context["characters"]
        or (
            "Four strikingly sharp, diverse senior specialists (ages 30-45) with distinctive modern "
            "executive identities, sophisticated global tech-hub styling, expressive eyes, natural skin "
            "texture and believable professional synergy. Faces remain anatomically stable in wide shots."
        ),
        "format_mode": (
            "A controlled multi-shot commercial with precisely motivated HARD CUTS, MATCH CUTS and WHIP "
            "CUTS. Every shot introduces a new visual idea while preserving character identities, wardrobe "
            "continuity and the same color world."
        ),
        "lighting": context["aesthetic"],
        "physics": (
            "Realistic ambient glow onto faces and hands, believable finger contact with glass interfaces, "
            "clothing and gestures with real inertia and follow-through."
        ),
        "audio": (
            "Original driving corporate score with deep cinematic bass, sharp digital ticks and sweeping "
            "risers; no borrowed melody. Synchronized UI bleeps, sub-bass whooshes on transitions and a "
            "crisp activation chime at the closing beat. No subtitles."
        ),
        "segments": segments,
        "source": "template",
    }


def render_prompt(context: dict, story: dict, board: dict, segment: dict, index: int) -> str:
    """Monta o prompt final da peca no template de producao."""
    blocks: list[str] = []
    if index == 0:
        blocks.append(f"SCENE CONTEXT\n{board.get('scene_context', '')}")
        if context["reference_asset_id"] or context["reference_note"]:
            blocks.append(
                "ACTIVE REFERENCE\n<<<image_1>>> is the visual source of truth. "
                + (context["reference_note"] or "Preserve its proportions, palette and identity in every shot. ")
                + "Keep it recognizable and unchanged across the whole film."
            )
        blocks.append(f"CHARACTERS\n{board.get('characters', '')}")
        blocks.append(f"FIRST FRAME\n{segment.get('first_frame', '')}")
        blocks.append(f"FORMAT MODE\n{board.get('format_mode', '')}")
    else:
        blocks.append(
            "CONTINUATION\nContinue the video from the previous shot. "
            + segment.get("continuity", "")
        )

    beats = segment.get("script_beats") or []
    if beats:
        blocks.append(
            "SCRIPT BEAT\n"
            + " → ".join(beats).upper()
            + ". "
            + " ".join(BEAT_BRIEF[b] for b in beats if b in BEAT_BRIEF)
        )
    blocks.append(f"ACTION AND CAMERA SEQUENCE\n{segment.get('shot_sequence', '')}")
    blocks.append(f"LIGHTING AND IMAGE QUALITY\n{board.get('lighting', context['aesthetic'])}")
    if index == 0:
        blocks.append(f"PHYSICS\n{board.get('physics', '')}")
    voice = segment.get("vo", "").strip()
    audio = board.get("audio", "")
    if voice:
        audio = f'{audio} Voiceover in Brazilian Portuguese, confident executive tone: "{voice}"'
    blocks.append(f"AUDIO\n{audio}")
    return "\n\n".join(b.strip() for b in blocks if b and b.strip())


# --------------------------------------------------------------------------- persistencia


def create_pipeline(project_id: str, raw_context: dict) -> dict:
    studio.get_project(project_id)
    context = normalize_context(raw_context)
    _validate_context(context)
    story = build_story(context)
    board = build_storyboard(context, story)
    row = {
        "id": studio.new_id("pipe"),
        "project_id": project_id,
        "title": story.get("title") or context["product"][:120],
        "context": json.dumps(context, ensure_ascii=False),
        "story": json.dumps(story, ensure_ascii=False),
        "storyboard": json.dumps(board, ensure_ascii=False),
        "status": "draft",
        "created_at": db.now(),
        "updated_at": db.now(),
    }
    db.insert("pipelines", row)
    return get_pipeline(row["id"])


def get_pipeline(pipeline_id: str) -> dict:
    row = db.query_one("SELECT * FROM pipelines WHERE id = ?", [pipeline_id])
    if not row:
        raise studio.StudioError(f"Pipeline {pipeline_id} nao encontrado.")
    row = dict(row)
    for key in ("context", "story", "storyboard"):
        row[key] = json.loads(row[key] or "{}")
    row["renders"] = [
        db.loads_meta(r)
        for r in db.query(
            """
            SELECT g.*, pr.segment_index
            FROM pipeline_renders pr JOIN generations g ON g.id = pr.generation_id
            WHERE pr.pipeline_id = ? ORDER BY pr.segment_index ASC
            """,
            [pipeline_id],
        )
    ]
    return row


def list_pipelines(project_id: str) -> list[dict]:
    rows = db.query(
        "SELECT id, project_id, title, status, created_at FROM pipelines "
        "WHERE project_id = ? ORDER BY created_at DESC",
        [project_id],
    )
    return rows


def sync_vo_from_story(story: dict, storyboard: dict) -> dict:
    """Reescreve a locução de cada peça a partir dos atos que ela cobre.

    A locução do storyboard é derivada do roteiro: se o ato foi editado no passo
    2, a peça correspondente precisa acompanhar, senão o prompt compilado
    continua falando o texto antigo. Direção de câmera, primeiro frame e
    continuidade não são tocados — só a fala.
    """
    acts = {a.get("n"): a for a in (story.get("acts") or [])}
    for segment in storyboard.get("segments") or []:
        cobertos = [acts[n] for n in (segment.get("acts") or []) if n in acts]
        if cobertos:
            segment["vo"] = " ".join((a.get("vo") or "").strip() for a in cobertos).strip()
    return storyboard


def update_pipeline(pipeline_id: str, story: dict | None = None, storyboard: dict | None = None) -> dict:
    pipeline = get_pipeline(pipeline_id)
    data: dict = {}
    if story is not None:
        data["story"] = json.dumps(story, ensure_ascii=False)
        if storyboard is None and pipeline.get("storyboard", {}).get("segments"):
            # o roteiro mudou: a locução das peças acompanha
            storyboard = sync_vo_from_story(story, pipeline["storyboard"])
    if storyboard is not None:
        for index, segment in enumerate(storyboard.get("segments", [])):
            segment["index"] = index + 1
            segment["duration_seconds"] = SEGMENT_SECONDS
            segment["mode"] = "extend" if index else _first_mode(pipeline["context"])
            if story is not None or not segment.get("prompt"):
                # roteiro editado invalida o prompt antigo: recompila com a fala nova
                segment["prompt"] = render_prompt(
                    pipeline["context"], story or pipeline["story"], storyboard, segment, index
                )
        data["storyboard"] = json.dumps(storyboard, ensure_ascii=False)
    if data:
        data["updated_at"] = db.now()
        db.update("pipelines", pipeline_id, data)
    return get_pipeline(pipeline_id)


def regenerate_prompts(pipeline_id: str) -> dict:
    """Reescreve os prompts a partir do storyboard atual (apos edicao manual)."""
    pipeline = get_pipeline(pipeline_id)
    board = pipeline["storyboard"]
    for index, segment in enumerate(board.get("segments", [])):
        segment["prompt"] = render_prompt(pipeline["context"], pipeline["story"], board, segment, index)
    return update_pipeline(pipeline_id, storyboard=board)


def render(pipeline_id: str, resolution: str | None = None) -> dict:
    """Passo 4. Valida o plano e dispara a renderizacao sequencial em background."""
    pipeline = get_pipeline(pipeline_id)
    context = pipeline["context"]
    segments = (pipeline["storyboard"] or {}).get("segments") or []
    if not segments:
        raise studio.StudioError("O storyboard esta vazio.")
    if pipeline["status"] == "rendering":
        raise studio.StudioError("Este pipeline ja esta renderizando.")
    total = len(segments) * SEGMENT_SECONDS
    if total > MAX_CUMULATIVE_SECONDS:
        raise studio.StudioError(
            f"O filme tem {total}s; o limite cumulativo de uma cena estendida e {MAX_CUMULATIVE_SECONDS}s."
        )
    resolution = resolution or context["resolution"]
    if resolution not in RESOLUTIONS:
        raise studio.StudioError(f"Resolucao invalida: {resolution}.")

    db.execute("DELETE FROM pipeline_renders WHERE pipeline_id = ?", [pipeline_id])
    db.update("pipelines", pipeline_id, {"status": "rendering", "error": None, "updated_at": db.now()})
    studio.EXECUTOR.submit(_run_render, pipeline_id, resolution)
    return {"pipeline_id": pipeline_id, "status": "rendering", "segments": len(segments), "resolution": resolution}


def _keyframe_media(project_id: str, generation_id: str) -> dict:
    """Extrai o último frame da peça anterior e devolve a referência de mídia."""
    from . import postproduction

    previous = studio.get_generation(generation_id)
    if not previous.get("asset_path"):
        raise studio.StudioError("A peça anterior não deixou arquivo para extrair o keyframe.")
    frame = postproduction.extract_last_frame(
        previous["asset_path"], settings.uploads_dir / f"{generation_id}_last.png"
    )
    asset = studio.save_upload(project_id, frame.name, frame.read_bytes(), "image")
    return {"asset_id": asset["id"], "kind": "image", "role": "first_frame"}


def _run_render(pipeline_id: str, resolution: str) -> None:
    """Cada peca so entra na fila depois que a anterior termina: a extensao
    precisa do `interaction_id` do clipe anterior."""
    try:
        pipeline = get_pipeline(pipeline_id)
    except studio.StudioError:
        return
    context = pipeline["context"]
    segments = pipeline["storyboard"]["segments"]
    media = (
        [{"asset_id": context["reference_asset_id"], "kind": "image", "role": "first_frame"}]
        if context["reference_asset_id"]
        else []
    )
    strategy = chaining_strategy()
    parent_id: str | None = None
    try:
        for index, segment in enumerate(segments):
            step_media = media if index == 0 else []
            mode = _first_mode(context) if index == 0 else "extend"
            if index and strategy == "keyframe":
                # provider que não estende cena: a continuidade vem do último
                # frame da peça anterior, usado como primeiro frame da próxima
                mode = "image_to_video"
                step_media = [_keyframe_media(pipeline["project_id"], parent_id)]
                parent_id = None
            generation = studio.create_generation(
                project_id=pipeline["project_id"],
                prompt=segment.get("prompt") or "",
                mode=mode,
                resolution=resolution,
                aspect_ratio=context["aspect_ratio"],
                duration_seconds=SEGMENT_SECONDS,
                parent_id=parent_id,
                media=step_media,
                label=f"Peça {index + 1} · {segment.get('timecode', '')}",
                enqueue=False,
            )
            db.insert(
                "pipeline_renders",
                {
                    "pipeline_id": pipeline_id,
                    "generation_id": generation["id"],
                    "segment_index": index + 1,
                    "created_at": db.now(),
                },
            )
            studio.run_generation(generation["id"])
            done = studio.get_generation(generation["id"])
            if done["status"] != "completed":
                db.update(
                    "pipelines",
                    pipeline_id,
                    {
                        "status": "failed",
                        "error": f"Peca {index + 1}: {done.get('error') or 'falha desconhecida'}",
                        "updated_at": db.now(),
                    },
                )
                return
            parent_id = generation["id"]
        db.update(
            "pipelines",
            pipeline_id,
            {"status": "completed", "chaining": strategy, "updated_at": db.now()},
        )
    except Exception as exc:
        db.update(
            "pipelines",
            pipeline_id,
            {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "updated_at": db.now()},
        )


def delete_pipeline(pipeline_id: str) -> None:
    get_pipeline(pipeline_id)
    db.execute("DELETE FROM pipeline_renders WHERE pipeline_id = ?", [pipeline_id])
    db.execute("DELETE FROM pipelines WHERE id = ?", [pipeline_id])
