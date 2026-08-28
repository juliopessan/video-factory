"""Geracao de texto estruturado (storytelling e storyboard).

Usa o mesmo cliente da Gemini API. Quando nao ha API key, o chamador cai no
fallback deterministico definido em `pipeline.py`, para o app seguir utilizavel
offline.
"""
from __future__ import annotations

import json
import os

from .config import settings

TEXT_MODEL = os.environ.get("VF_TEXT_MODEL", "gemini-flash-latest")


class TextGenError(RuntimeError):
    pass


def available() -> bool:
    return bool(settings.api_key)


def generate_json(system_instruction: str, prompt: str, schema: dict) -> dict:
    """Pede uma resposta JSON valida ao modelo de texto."""
    if not available():
        raise TextGenError("GEMINI_API_KEY nao configurada.")
    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover
        raise TextGenError("Pacote google-genai nao instalado.") from exc

    client = genai.Client(api_key=settings.api_key)
    try:
        response = client.models.generate_content(
            model=TEXT_MODEL,
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
