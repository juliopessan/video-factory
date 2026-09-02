"""Quebra de arquivos em trechos que fazem sentido sozinhos.

Cortar a cada N linhas produz trecho que começa no meio de uma função e não
serve de citação. Aqui o corte segue a estrutura do arquivo — `def`, `class`,
cabeçalhos markdown, blocos separados por linha em branco — e cada trecho
carrega o caminho e o intervalo de linhas, que é o que vira a citação.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .tokens import count_tokens

MAX_CHUNK_TOKENS = 400
MIN_CHUNK_TOKENS = 20

# início de bloco por linguagem: onde é seguro cortar
LIMITES = {
    ".py": re.compile(r"^(?:@|(?:async\s+)?def\s|class\s|[A-Z_][A-Z0-9_]*\s*=)"),
    ".js": re.compile(r"^(?:export\s|function\s|const\s|class\s|async\s+function\s)"),
    ".ts": re.compile(r"^(?:export\s|function\s|const\s|class\s|interface\s|type\s)"),
    ".tsx": re.compile(r"^(?:export\s|function\s|const\s|class\s|interface\s|type\s)"),
    ".md": re.compile(r"^#{1,6}\s"),
    ".css": re.compile(r"^[.#:@a-zA-Z\[][^{]*\{\s*$"),
    ".html": re.compile(r"^\s*<(?:section|main|header|footer|nav|article|div|body)\b"),
}


@dataclass(frozen=True)
class Chunk:
    path: str
    start_line: int
    end_line: int
    text: str
    tokens: int
    symbol: str | None = None

    @property
    def citation(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


def _simbolo(linha: str) -> str | None:
    m = re.match(r"\s*(?:async\s+)?(?:def|class|function|const|interface|type)\s+([A-Za-z_$][\w$]*)", linha)
    if m:
        return m.group(1)
    m = re.match(r"^(#{1,6})\s+(.*)", linha)
    if m:
        return m.group(2).strip()[:60]
    return None


def chunk_text(path: str, text: str, max_tokens: int = MAX_CHUNK_TOKENS) -> list[Chunk]:
    linhas = text.splitlines()
    if not linhas:
        return []
    limite = LIMITES.get(Path(path).suffix, re.compile(r"^\S"))

    trechos: list[Chunk] = []
    inicio = 0
    atual: list[str] = []
    simbolo: str | None = None

    def fecha(fim: int) -> None:
        nonlocal atual, inicio, simbolo
        corpo = "\n".join(atual).strip("\n")
        if corpo.strip():
            trechos.append(
                Chunk(path, inicio + 1, fim, corpo, count_tokens(corpo), simbolo)
            )
        atual = []
        simbolo = None

    for i, linha in enumerate(linhas):
        novo_bloco = bool(limite.match(linha)) and atual
        if novo_bloco and count_tokens("\n".join(atual)) >= MIN_CHUNK_TOKENS:
            fecha(i)
            inicio = i
        if not atual:
            inicio = i
            simbolo = _simbolo(linha)
        atual.append(linha)
        if count_tokens("\n".join(atual)) >= max_tokens:
            fecha(i + 1)
            inicio = i + 1
    fecha(len(linhas))
    return trechos
