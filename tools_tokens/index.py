"""Índice léxico dos arquivos de um diretório.

Sem embeddings e sem rede: BM25 sobre os identificadores do código. Para a
pergunta típica de quem lê um repositório — "onde fica X configurado?" — o nome
do símbolo aparece literalmente no trecho certo, e um índice léxico acha isso
mais rápido e sem custo.
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .chunking import Chunk, chunk_text

EXTENSOES = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".css", ".html", ".json", ".toml", ".yml", ".yaml", ".sh"}
IGNORAR = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "storage", "out", ".next"}
MAX_BYTES = 400_000

_TERMO = re.compile(r"[A-Za-z_][A-Za-z0-9_]+|\d+")


def tokenize(texto: str) -> list[str]:
    """Termos de busca: minúsculas, com snake_case e camelCase também quebrados."""
    termos: list[str] = []
    for bruto in _TERMO.findall(texto):
        baixo = bruto.lower()
        termos.append(baixo)
        partes = [p for p in bruto.split("_") if p]
        if len(partes) > 1:
            termos += [p.lower() for p in partes]
        camel = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", bruto)
        if len(camel) > 1:
            termos += [c.lower() for c in camel]
    return termos


@dataclass
class Index:
    root: str
    chunks: list[Chunk] = field(default_factory=list)
    postings: dict[str, list[int]] = field(default_factory=dict)
    freqs: list[Counter] = field(default_factory=list)
    lengths: list[int] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    @property
    def files(self) -> int:
        return len({c.path for c in self.chunks})

    @property
    def avg_length(self) -> float:
        return sum(self.lengths) / len(self.lengths) if self.lengths else 0.0

    @property
    def total_tokens(self) -> int:
        return sum(c.tokens for c in self.chunks)

    def stats(self) -> dict:
        return {
            "root": self.root,
            "files": self.files,
            "chunks": len(self.chunks),
            "tokens_indexed": self.total_tokens,
            "vocabulary": len(self.postings),
            "skipped": dict(self.skipped),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }

    # -- BM25 ----------------------------------------------------------------

    def search(self, query: str, limit: int = 40, k1: float = 1.4, b: float = 0.72,
               expanded: list[str] | None = None, expansion_weight: float = 0.55) -> list[tuple[int, float]]:
        """BM25. Termos vindos de expansão pesam menos que os da pergunta."""
        from .query import prepare

        if expanded is None:
            termos, expanded = prepare(query)
        else:
            termos = tokenize(query)
        if not (termos or expanded) or not self.chunks:
            return []
        pesos = {t: 1.0 for t in termos}
        for t in expanded:
            pesos.setdefault(t, expansion_weight)
        n = len(self.chunks)
        media = self.avg_length or 1.0
        escores: dict[int, float] = {}
        for termo, peso in pesos.items():
            posts = self.postings.get(termo)
            if not posts:
                continue
            idf = math.log(1 + (n - len(posts) + 0.5) / (len(posts) + 0.5))
            for i in posts:
                tf = self.freqs[i][termo]
                norma = 1 - b + b * (self.lengths[i] / media)
                escore = peso * idf * (tf * (k1 + 1)) / (tf + k1 * norma)
                # definição ou constante com o nome procurado vale mais que menção em prosa
                simbolo = (self.chunks[i].symbol or "").lower()
                if simbolo and termo in tokenize(simbolo):
                    escore *= 1.8
                escores[i] = escores.get(i, 0.0) + escore
        return sorted(escores.items(), key=lambda par: par[1], reverse=True)[:limit]


def index_path(path: str | Path, *, extensions: set[str] | None = None) -> Index:
    """Percorre, quebra e indexa. Devolve o índice com as contas do caminho."""
    raiz = Path(path)
    if not raiz.exists():
        raise FileNotFoundError(f"caminho não encontrado: {raiz}")
    extensoes = extensions or EXTENSOES
    inicio = time.monotonic()
    index = Index(root=str(raiz))

    arquivos = [raiz] if raiz.is_file() else sorted(
        p for p in raiz.rglob("*")
        if p.is_file() and not any(parte in IGNORAR for parte in p.parts)
    )
    for arquivo in arquivos:
        if arquivo.suffix not in extensoes:
            index.skipped["extensão"] = index.skipped.get("extensão", 0) + 1
            continue
        try:
            if arquivo.stat().st_size > MAX_BYTES:
                index.skipped["grande demais"] = index.skipped.get("grande demais", 0) + 1
                continue
            texto = arquivo.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            index.skipped["ilegível"] = index.skipped.get("ilegível", 0) + 1
            continue
        rel = str(arquivo.relative_to(raiz)) if raiz.is_dir() else arquivo.name
        for trecho in chunk_text(rel, texto):
            i = len(index.chunks)
            index.chunks.append(trecho)
            termos = tokenize(trecho.text)
            freq = Counter(termos)
            index.freqs.append(freq)
            index.lengths.append(len(termos))
            for termo in freq:
                index.postings.setdefault(termo, []).append(i)
    index.elapsed_seconds = time.monotonic() - inicio
    return index
