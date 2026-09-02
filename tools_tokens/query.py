"""Preparo da pergunta.

Duas coisas separam uma busca útil de uma inútil aqui: tirar as palavras que só
ligam a frase ("where", "is", "the") e reconhecer que quem pergunta por "retry
backoff" pode estar procurando o código que se chama `POLL_INTERVAL_SECONDS`.
A expansão é uma tabela pequena e explícita de vocabulário de desenvolvimento —
não um modelo — e o manifesto mostra quais termos entraram por expansão.
"""
from __future__ import annotations

from .index import tokenize

VAZIAS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "that", "the", "to", "was", "what", "when",
    "where", "which", "who", "why", "with", "we", "i", "you", "set", "setup", "use",
    "configured", "configure", "configuration", "config",
    "o", "a", "os", "as", "de", "do", "da", "em", "no", "na", "que", "onde", "como",
    "qual", "para", "com", "e", "esta", "está", "fica", "configurado",
}

# vocabulário de desenvolvimento: termo perguntado -> termos que o código costuma usar
SINONIMOS = {
    "retry": ["retries", "attempt", "attempts", "poll", "polling", "again"],
    "backoff": ["interval", "delay", "sleep", "wait", "timeout", "poll"],
    "timeout": ["deadline", "timeout", "expires", "seconds"],
    "auth": ["token", "key", "credential", "authorization", "apikey"],
    "cache": ["cached", "memo", "store"],
    "log": ["logging", "logger", "debug"],
    "subtitle": ["srt", "caption", "legenda", "mov_text", "burn", "soft"],
    "burn": ["burned", "hardsub", "force_style", "drawtext"],
    "render": ["generate", "generation", "export"],
    "upload": ["asset", "file", "multipart"],
}


def _raizes(termo: str) -> list[str]:
    """Variações que a tabela pode conhecer.

    Tirar o plural é seguro ("subtitles" → "subtitle"). Cortar "es", "ed" e
    "ing" produz lixo com frequência ("subtitles" → "subtitl"), então essas
    formas só valem quando a tabela de sinônimos de fato conhece o resultado.
    """
    formas = [termo]
    if termo.endswith("s") and len(termo) >= 4:
        formas.append(termo[:-1])
    for sufixo in ("es", "ed", "ing"):
        if termo.endswith(sufixo) and len(termo) - len(sufixo) >= 3:
            raiz = termo[: -len(sufixo)]
            if raiz in SINONIMOS:
                formas.append(raiz)
    return formas


def prepare(query: str) -> tuple[list[str], list[str]]:
    """Devolve (termos da pergunta, termos acrescentados por expansão)."""
    base = [t for t in tokenize(query) if t not in VAZIAS and len(t) > 1]
    vistos = set(base)
    expandidos: list[str] = []
    for termo in list(base):
        candidatos: list[str] = []
        for raiz in _raizes(termo):
            candidatos += SINONIMOS.get(raiz, [])
            if raiz != termo and raiz not in vistos:
                # a raiz também procura: "subtitles" acha "subtitle" no código
                candidatos.append(raiz)
        for extra in candidatos:
            if extra not in vistos:
                vistos.add(extra)
                expandidos.append(extra)
    return base, expandidos
