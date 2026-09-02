"""Contagem de tokens.

Sem tokenizador do provedor instalado, a contagem é uma estimativa — e o
manifesto diz isso em vez de fingir precisão. A heurística é calibrada para
código: identificadores longos, pontuação densa e indentação custam mais por
caractere do que prosa.

Quem tiver o tokenizador real injeta pela API: `set_counter(fn)`.
"""
from __future__ import annotations

import re
from typing import Callable

_PALAVRA = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|\s+|[^\sA-Za-z0-9_]")

_contador: Callable[[str], int] | None = None
_exato = False


def set_counter(fn: Callable[[str], int] | None, *, exact: bool = True) -> None:
    """Troca a contagem pela do seu provedor (tiktoken, count_tokens, etc.)."""
    global _contador, _exato
    _contador, _exato = fn, exact if fn else False


def is_exact() -> bool:
    return _exato


def count_tokens(text: str) -> int:
    if _contador is not None:
        return _contador(text)
    return estimate_tokens(text)


def estimate_tokens(text: str) -> int:
    """Estimativa local, sem rede e sem dependência.

    Palavras curtas viram um token; identificadores longos são quebrados a cada
    ~4 caracteres, como fazem os BPE; espaço em branco em bloco conta pouco;
    pontuação conta um cada.
    """
    if not text:
        return 0
    total = 0
    for pedaco in _PALAVRA.findall(text):
        if pedaco.isspace():
            total += max(1, pedaco.count("\n"))
        elif pedaco.isalnum() or "_" in pedaco:
            total += max(1, round(len(pedaco) / 4))
        else:
            total += 1
    return total
