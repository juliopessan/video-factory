"""Montagem do contexto dentro do orçamento.

Três regras: nada entra sem citação (`arquivo:linha-linha`), nada ultrapassa o
orçamento, e tudo o que ficou de fora aparece no manifesto com o motivo. O
manifesto é a diferença entre um contexto que você confere e um que você aceita
no escuro.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .chunking import Chunk
from .headroom import Headroom
from .index import Index
from .query import prepare
from .tokens import count_tokens, is_exact

CABECALHO_TOKENS = 12  # citação + separadores por bloco

# `#` se confunde com comentário de código na hora de ler o contexto: a citação
# usa um delimitador que não existe em nenhuma das linguagens indexadas.
ABRE = "◇ "
FECHA = "\n◈"


@dataclass
class Selection:
    chunk: Chunk
    score: float
    rank: int


@dataclass
class Context:
    query: str
    text: str
    selections: list[Selection] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    expanded: list[str] = field(default_factory=list)
    budget_requested: int = 0
    budget_effective: int = 0
    tokens_used: int = 0
    index_stats: dict = field(default_factory=dict)
    relevance_floor: float = 0.0
    headroom_before: dict | None = None
    headroom_after: dict | None = None
    elapsed_seconds: float = 0.0

    @property
    def citations(self) -> list[str]:
        return [s.chunk.citation for s in self.selections]

    @property
    def files(self) -> list[str]:
        vistos: list[str] = []
        for s in self.selections:
            if s.chunk.path not in vistos:
                vistos.append(s.chunk.path)
        return vistos

    def manifest(self) -> dict:
        """A conta inteira: o que entrou, o que ficou de fora e por quê."""
        return {
            "query": self.query,
            "terms": self.terms,
            "expanded_terms": self.expanded,
            "relevance_floor": round(self.relevance_floor, 3),
            "budget": {
                "requested": self.budget_requested,
                "effective": self.budget_effective,
                "used": self.tokens_used,
                "free": self.budget_effective - self.tokens_used,
                "utilization": round(self.tokens_used / self.budget_effective, 3)
                if self.budget_effective else 0.0,
            },
            "counting": "exact" if is_exact() else "estimated (~4 chars/token, local heuristic)",
            "selected": [
                {
                    "citation": s.chunk.citation,
                    "symbol": s.chunk.symbol,
                    "score": round(s.score, 3),
                    "rank": s.rank,
                    "tokens": s.chunk.tokens,
                }
                for s in self.selections
            ],
            "dropped": self.dropped,
            "files": self.files,
            "index": self.index_stats,
            "headroom": {"before": self.headroom_before, "after": self.headroom_after},
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def _render(selections: list[Selection]) -> str:
    partes = []
    for s in selections:
        partes.append(f"{ABRE}{s.chunk.citation}\n{s.chunk.text}{FECHA}")
    return "\n\n".join(partes)


def pack_query(
    query: str,
    index: Index,
    *,
    budget: int,
    headroom: Headroom | None = None,
    label: str = "context",
    max_per_file: int = 3,
    candidates: int = 60,
    min_score_ratio: float = 0.45,
) -> Context:
    """Busca, ordena e enche o orçamento — nessa ordem, sem estourar.

    Orçamento sobrando não é motivo para incluir trecho fraco: o que pontua
    abaixo de `min_score_ratio` do melhor resultado fica de fora, com o motivo
    no manifesto. Contexto é resposta, não é tudo que coube.
    """
    inicio = time.monotonic()
    termos, expandidos = prepare(query)
    efetivo = headroom.allow(budget) if headroom else budget

    contexto = Context(
        query=query,
        text="",
        terms=termos,
        expanded=expandidos,
        budget_requested=budget,
        budget_effective=efetivo,
        index_stats=index.stats(),
        headroom_before=headroom.snapshot() if headroom else None,
    )
    if efetivo <= 0:
        contexto.dropped.append({"reason": "sem orçamento disponível na janela", "count": 1})
        contexto.headroom_after = headroom.snapshot() if headroom else None
        contexto.elapsed_seconds = time.monotonic() - inicio
        return contexto

    ranking = index.search(query, limit=candidates, expanded=expandidos)
    piso = ranking[0][1] * min_score_ratio if ranking else 0.0
    usado = 0
    por_arquivo: dict[str, int] = {}

    for posicao, (i, escore) in enumerate(ranking, start=1):
        trecho = index.chunks[i]
        if escore < piso:
            contexto.dropped.append({
                "citation": trecho.citation,
                "reason": f"abaixo do piso de relevância ({min_score_ratio:g} do topo)",
                "score": round(escore, 3),
            })
            continue
        custo = trecho.tokens + CABECALHO_TOKENS
        if por_arquivo.get(trecho.path, 0) >= max_per_file:
            contexto.dropped.append({"citation": trecho.citation, "reason": f"limite de {max_per_file} trechos por arquivo"})
            continue
        if usado + custo > efetivo:
            contexto.dropped.append({"citation": trecho.citation, "reason": "não cabia no orçamento", "tokens": custo})
            continue
        contexto.selections.append(Selection(trecho, escore, posicao))
        por_arquivo[trecho.path] = por_arquivo.get(trecho.path, 0) + 1
        usado += custo

    # ordena o texto por arquivo e linha: lê-se como código, não como ranking
    contexto.relevance_floor = piso
    # ordena o texto por relevância decrescente dentro de cada arquivo lido em
    # ordem de melhor pontuação: o leitor encontra a resposta no primeiro bloco
    ordem_arquivo = {}
    for s in contexto.selections:
        ordem_arquivo.setdefault(s.chunk.path, s.rank)
    contexto.selections.sort(key=lambda s: (ordem_arquivo[s.chunk.path], s.chunk.start_line))
    contexto.text = _render(contexto.selections)
    contexto.tokens_used = count_tokens(contexto.text)

    if headroom and contexto.tokens_used:
        headroom.spend(label, contexto.tokens_used)
    contexto.headroom_after = headroom.snapshot() if headroom else None
    contexto.elapsed_seconds = time.monotonic() - inicio
    return contexto
