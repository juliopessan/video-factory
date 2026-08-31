"""Geracao de texto estruturado (storytelling e storyboard).

Usa o mesmo cliente da Gemini API. Quando nao ha API key, o chamador cai no
fallback deterministico definido em `pipeline.py`, para o app seguir utilizavel
offline.
"""
from __future__ import annotations

import json
import os

import re

from . import config

# `config.settings` é relido a cada chamada de propósito: o modal de configuração
# reconstrói o objeto, e quem guardasse uma referência continuaria com a chave
# antiga até o servidor reiniciar.
DEFAULT_TEXT_MODEL = "gemini-flash-latest"


def text_model() -> str:
    """Lido do ambiente a cada chamada, pelo mesmo motivo de `config.settings`."""
    return os.environ.get("VF_TEXT_MODEL", DEFAULT_TEXT_MODEL)


# mantido para quem importa TEXT_MODEL direto (interface e testes)
TEXT_MODEL = text_model()


class TextGenError(RuntimeError):
    pass


def available() -> bool:
    return bool(config.settings.api_key)


SAFETY_SYSTEM = """You are an expert AI video prompt safety specialist.
The provided video generation prompt was blocked by Google's Generative AI Prohibited Use policy (content_blocked or sensitive word filter).
Your task is to rephrase the prompt to make it 100% compliant and policy-safe while retaining 100% of its visual storytelling, camera movements, high-end commercial aesthetic, character actions, and voiceover intent.

Guidelines:
- Eliminate sensitive or aggressive terms (e.g. 'whip cut', 'burst through', 'attack', 'kill', 'weapon', 'shoot', 'penetrate', ambiguous violence or security terms).
- Use professional, elegant cinematography language (e.g. 'dynamic pan cut', 'glide forward', 'cinematographic view', 'seamless transition', 'transform').
- Maintain the exact prompt template structure (e.g. SCENE CONTEXT, CHARACTERS, FIRST FRAME, FORMAT MODE, SCRIPT BEAT, ACTION AND CAMERA SEQUENCE, LIGHTING, PHYSICS, AUDIO).
- Return valid JSON with safe_prompt, changes_summary, sanitized_shot_sequence, and sanitized_vo."""

SAFETY_SCHEMA = {
    "type": "object",
    "properties": {
        "safe_prompt": {"type": "string"},
        "changes_summary": {"type": "string"},
        "sanitized_shot_sequence": {"type": "string"},
        "sanitized_vo": {"type": "string"},
    },
    "required": ["safe_prompt", "changes_summary"],
}

SENSITIVE_REPLACEMENTS = [
    (r"\bWHIP\s+CUT\b", "DYNAMIC PAN CUT"),
    (r"\bwhip\s+cut\b", "dynamic pan cut"),
    (r"\bHARD\s+CUT\b", "DIRECT CUT"),
    (r"\bhard\s+cut\b", "direct cut"),
    (r"\bbursts?\s+through\b", "glides smoothly forward"),
    (r"\bbursting\s+through\b", "gliding smoothly forward"),
    (r"\bcarrying the old, heavy artifact\b", "carrying legacy equipment"),
    (r"\bold, heavy artifact\b", "legacy equipment"),
    (r"\bkill(?:ing|ed|s)?\b", "streamlining"),
    (r"\battack(?:ing|ed|s)?\b", "resolving"),
    (r"\bpenetrat(?:e|ing|ion)\b", "accessing"),
    (r"\bweapon\b", "tool"),
    (r"\bviolence\b", "intensity"),
]


def sanitize_text(text: str) -> str:
    """Substituição determinística de termos que costumam ativar filtros de segurança."""
    sanitized = text
    for pattern, replacement in SENSITIVE_REPLACEMENTS:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    return sanitized


def rephrase_blocked_prompt(prompt: str, segment: dict | None = None, error_detail: str | None = None) -> dict:
    """Refraseia um prompt bloqueado por filtros de segurança (content_blocked)."""
    segment = segment or {}
    if available():
        user_prompt = (
            f"Original blocked prompt:\n{prompt}\n\n"
            f"Error message: {error_detail or 'content_blocked - sensitive words detected'}\n\n"
            f"Segment details:\n"
            f"- Voiceover: {segment.get('vo', '')}\n"
            f"- Shot sequence: {segment.get('shot_sequence', '')}\n"
            f"- First frame: {segment.get('first_frame', '')}\n\n"
            "Please rewrite this prompt to be completely safe, compliant with Google AI safety policies, "
            "and ready for immediate generation."
        )
        try:
            res = generate_json(SAFETY_SYSTEM, user_prompt, SAFETY_SCHEMA)
            if res.get("safe_prompt"):
                return res
        except Exception:
            pass

    # Fallback determinístico
    safe_prompt = sanitize_text(prompt)
    shot_seq = sanitize_text(segment.get("shot_sequence") or "")
    vo = sanitize_text(segment.get("vo") or "")
    return {
        "safe_prompt": safe_prompt,
        "changes_summary": "Termos sensíveis substituídos por equivalentes cinematográficos seguros (ex: WHIP CUT → DYNAMIC PAN CUT, burst → glide).",
        "sanitized_shot_sequence": shot_seq,
        "sanitized_vo": vo,
    }


def generate_json(system_instruction: str, prompt: str, schema: dict) -> dict:
    """Pede uma resposta JSON valida ao modelo de texto."""
    if not available():
        raise TextGenError("GEMINI_API_KEY nao configurada.")
    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover
        raise TextGenError("Pacote google-genai nao instalado.") from exc

    client = genai.Client(api_key=config.settings.api_key)
    try:
        response = client.models.generate_content(
            model=text_model(),
            contents=prompt,
            config={
                "system_instruction": system_instruction,
                "response_mime_type": "application/json",
                "response_schema": schema,
                "temperature": 0.9,
            },
        )
    except Exception as exc:
        raise TextGenError(f"Falha no modelo de texto: {exc}") from exc

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise TextGenError("Modelo de texto retornou resposta vazia.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TextGenError(f"Resposta nao e JSON valido: {text[:200]}") from exc
