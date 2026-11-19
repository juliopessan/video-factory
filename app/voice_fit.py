"""Sugestão de locução que cabe na janela da peça.

A advertência de locução longa mostra o problema; este módulo propõe a saída.
Nada é aplicado sozinho: devolve uma sugestão e a UI deixa o usuário aceitar,
editar ou descartar.

Dois caminhos, sempre sinalizados em `source`:
- `model`: o modelo de texto condensa preservando fatos, números e nomes;
- `local`: sem chave, um corte determinístico por oração. É simples de propósito
  (mantém o começo de cada ideia e descarta o fim) e a UI avisa para revisar.
"""
from __future__ import annotations

import re

from . import textgen

# Palavras que não podem terminar a frase cortada ("... e", "... de").
_DANGLING = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "of", "on", "or", "such",
    "that", "the", "to", "with", "o", "os", "as", "um", "uma", "e", "de", "da", "do", "das",
    "dos", "em", "no", "na", "para", "por", "com", "que", "mas", "ou", "como",
}
# 1º nível: corta antes de um conector, onde a oração seguinte é elaboração
# ("..., but ...", "..., such as ..."); 2º nível: nas vírgulas.
_CONNECTOR_BREAK = re.compile(
    r"(?=,?\s+(?:but|which|so that|while|mas|que|porém|enquanto)\s)|(?=,\s+(?:and|e)\s)", re.I
)
_COMMA_BREAK = re.compile(r"(?<=[,;:—–])(?=\s)")
# oração dependente: se o corte cai no meio dela, ela não fica pela metade
_DEPENDENT = re.compile(r"^[,\s]*(?:but|which|so that|while|mas|que|porém|enquanto|such as|como)\b", re.I)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+")

FIT_SCHEMA = {
    "type": "object",
    "properties": {"voiceover": {"type": "string"}},
    "required": ["voiceover"],
}

_SYSTEM = (
    "You edit voiceover scripts for short commercials. Rewrite the text so it fits the word limit. "
    "Keep every key fact, name, number and the order of ideas; remove examples, qualifiers and "
    "redundancy first. Never add facts, claims or numbers that are not in the original. Keep the "
    "same language and a confident, spoken tone. Return only the rewritten voiceover."
)


def word_count(text: str) -> int:
    return len(text.split())


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", text))


def _greedy(segments: list[str], budget: int) -> str:
    kept: list[str] = []
    cut_short = False
    for segment in segments:
        if not segment.strip():
            continue
        if word_count("".join([*kept, segment])) > budget:
            cut_short = True
            break
        kept.append(segment)
    if cut_short and len(kept) > 1 and _DEPENDENT.match(kept[-1]):
        kept.pop()
    return "".join(kept).strip()


def _trim_to_words(text: str, budget: int) -> str:
    """Corta `text` em até `budget` palavras, preferindo fronteiras de oração."""
    if word_count(text) <= budget:
        return text.strip()
    chosen = ""
    for splitter in (_CONNECTOR_BREAK, _COMMA_BREAK):
        chosen = _greedy(splitter.split(text), budget)
        if chosen:
            break
    if not chosen:  # a primeira oração já estoura: corta na palavra
        chosen = " ".join(text.split()[:budget])
    words = chosen.split()
    while words and words[-1].strip(",;:—–").lower() in _DANGLING:
        words.pop()
    return " ".join(words).rstrip(" ,;:—–")


def shorten_local(text: str, limit: int) -> str:
    """Encurta sem modelo: reparte o orçamento entre as frases, em ordem.

    Cada frase fica com a parte do orçamento proporcional ao seu tamanho, então
    nenhuma ideia (problema, virada, valor, CTA) some por inteiro. Cortar em
    fronteira de oração deixa sobra; a sobra volta para as frases cortadas."""
    text = " ".join(text.split())
    if word_count(text) <= limit:
        return text
    sentences = [s.rstrip(" .!?…") for s in _SENTENCE_BREAK.split(text) if s.strip()]
    total = sum(word_count(s) for s in sentences)
    budgets = [max(3, round(word_count(s) / total * limit)) for s in sentences]
    cuts = [_trim_to_words(s, b) for s, b in zip(sentences, budgets)]
    for _ in range(4):
        leftover = limit - sum(word_count(c) for c in cuts)
        cut_idx = [i for i, (s, c) in enumerate(zip(sentences, cuts)) if word_count(c) < word_count(s)]
        if leftover <= 0 or not cut_idx:
            break
        for i in cut_idx:
            grown = _trim_to_words(sentences[i], word_count(cuts[i]) + max(leftover // len(cut_idx), 1))
            if sum(word_count(c) for c in cuts) - word_count(cuts[i]) + word_count(grown) <= limit:
                cuts[i] = grown
    return " ".join(f"{c}." for c in cuts)


def fit_voiceover(text: str, limit: int, language: str = "pt-BR", context: dict | None = None) -> dict:
    """Devolve {original, suggestion, words_before, words_after, limit, source, fits}."""
    original = " ".join((text or "").split())
    result = {
        "original": original, "limit": limit, "words_before": word_count(original),
        "source": "none", "suggestion": original, "words_after": word_count(original), "fits": True,
    }
    if word_count(original) <= limit:
        return result

    suggestion, source = None, "local"
    if textgen.available():
        lang = "American English" if language == "en-US" else "Brazilian Portuguese (PT-BR)"
        brief = ""
        if context:
            brief = f"Film: {context.get('product', '')} for {context.get('audience', '')}.\n"
        try:
            reply = textgen.generate_json(
                _SYSTEM,
                f"{brief}Language: {lang}\nWord limit: {limit} (hard maximum)\n\nVoiceover:\n{original}",
                FIT_SCHEMA,
            )
            candidate = " ".join(str(reply.get("voiceover", "")).split())
            # o modelo não pode estourar o limite nem perder um número do original
            if candidate and word_count(candidate) <= limit and _numbers(original) <= _numbers(candidate):
                suggestion, source = candidate, "model"
        except textgen.TextGenError:
            pass
    if suggestion is None:
        suggestion = shorten_local(original, limit)

    result.update(
        suggestion=suggestion, source=source, words_after=word_count(suggestion),
        fits=word_count(suggestion) <= limit,
    )
    return result
