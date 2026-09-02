"""tools_tokens — contexto citado que respeita a janela.

    headroom = Headroom(window=200_000, reserve_output=8_000)
    headroom.spend("conversation", 120_000)

    index = index_path("./src")
    context = pack_query("where is the retry backoff configured?",
                         index, budget=6_000, headroom=headroom)

    print(context.text)        # contexto citado, dentro do orçamento
    print(context.manifest())  # e a conta inteira de como chegou lá

Sem rede, sem embeddings, sem dependência externa: BM25 sobre trechos cortados
na estrutura do arquivo, expansão explícita de vocabulário de desenvolvimento e
orçamento verificado a cada bloco.
"""
from .chunking import Chunk, chunk_text
from .headroom import Headroom, HeadroomExceeded
from .index import Index, index_path, tokenize
from .pack import Context, Selection, pack_query
from .query import prepare
from .tokens import count_tokens, estimate_tokens, is_exact, set_counter

__all__ = [
    "Chunk", "chunk_text", "Context", "Headroom", "HeadroomExceeded", "Index",
    "Selection", "count_tokens", "estimate_tokens", "index_path", "is_exact",
    "pack_query", "prepare", "set_counter", "tokenize",
]
__version__ = "0.1.0"
