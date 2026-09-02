"""Testes do tools_tokens: orçamento, corte, busca e manifesto.

Roda offline, sem rede: `python3 tests_tools_tokens.py`.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from tools_tokens import (
    Headroom,
    HeadroomExceeded,
    chunk_text,
    estimate_tokens,
    index_path,
    pack_query,
    prepare,
    set_counter,
    tokenize,
)

falhas: list[str] = []


def check(label: str, cond: bool, detalhe: str = "") -> None:
    print(f"{'ok  ' if cond else 'FALHA'} {label}{'' if cond else ' -> ' + str(detalhe)}")
    if not cond:
        falhas.append(label)


# ---------------------------------------------------------------- headroom

h = Headroom(window=200_000, reserve_output=8_000)
check("janela desconta a reserva de saída", h.available == 192_000, h.available)
h.spend("conversation", 120_000)
check("gasto reduz o disponível", h.available == 72_000, h.available)
check("o gasto fica registrado por rótulo", h.spent["conversation"] == 120_000)
check("orçamento pedido é recortado ao que cabe", h.allow(100_000) == 72_000)
check("orçamento menor passa inteiro", h.allow(6_000) == 6_000)
try:
    h.spend("estouro", 80_000)
    check("gasto maior que a janela é recusado", False, "não levantou")
except HeadroomExceeded as exc:
    check("gasto maior que a janela é recusado", "80000" in str(exc).replace(" ", ""), str(exc)[:60])
check("recusa não altera a conta", h.available == 72_000, h.available)
try:
    Headroom(window=1000, reserve_output=1000)
    check("reserva que engole a janela é recusada", False, "não levantou")
except ValueError:
    check("reserva que engole a janela é recusada", True)

# ------------------------------------------------------------------ tokens

check("texto vazio custa zero", estimate_tokens("") == 0)
check("identificador longo custa mais que palavra curta",
      estimate_tokens("POLL_INTERVAL_SECONDS") > estimate_tokens("poll"))
set_counter(lambda t: 42, exact=True)
from tools_tokens import count_tokens, is_exact  # noqa: E402

check("contador injetado é usado", count_tokens("qualquer coisa") == 42 and is_exact())
set_counter(None)
check("volta para a estimativa local", not is_exact())

# ------------------------------------------------------------------- corte

codigo = '''import time

POLL_INTERVAL_SECONDS = 5


def espera(deadline: float) -> None:
    """Doc."""
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)


class Cliente:
    def busca(self):
        return 1
'''
trechos = chunk_text("exemplo.py", codigo)
check("o arquivo vira mais de um trecho", len(trechos) >= 2, len(trechos))
check("todo trecho tem citação com linhas",
      all(":" in t.citation and "-" in t.citation for t in trechos))
check("as linhas cobrem o arquivo sem buraco",
      trechos[0].start_line == 1 and trechos[-1].end_line == len(codigo.splitlines()),
      [(t.start_line, t.end_line) for t in trechos])
check("o símbolo da definição é reconhecido",
      {"espera", "Cliente"} & {t.symbol for t in trechos}, [t.symbol for t in trechos])

# ------------------------------------------------------------------ termos

termos, expandidos = prepare("where is the retry backoff configured?")
check("palavras de ligação saem da busca", termos == ["retry", "backoff"], termos)
check("vocabulário de código entra por expansão",
      {"poll", "interval", "sleep", "timeout"} <= set(expandidos), expandidos)
check("snake_case e camelCase são quebrados",
      {"poll", "interval", "seconds", "pollinterval"} & set(tokenize("POLL_INTERVAL_SECONDS pollInterval")),
      tokenize("POLL_INTERVAL_SECONDS pollInterval"))

# --------------------------------------------------------- índice e pacote

tmp = Path(tempfile.mkdtemp(prefix="tt-"))
(tmp / "provider.py").write_text(codigo, encoding="utf-8")
(tmp / "ui.js").write_text("export function render() { return 1; }\n", encoding="utf-8")
(tmp / "nota.md").write_text("# Guia\n\nNada a ver com espera.\n", encoding="utf-8")
(tmp / "binario.bin").write_bytes(b"\x00\x01\x02")
(tmp / "sub").mkdir()
(tmp / "sub" / "outro.py").write_text("VALOR = 1\n", encoding="utf-8")
(tmp / "node_modules").mkdir()
(tmp / "node_modules" / "lixo.js").write_text("x", encoding="utf-8")

index = index_path(tmp)
check("indexa recursivamente", index.files == 4, index.stats())
check("ignora node_modules", not any("node_modules" in c.path for c in index.chunks))
check("pula extensão desconhecida", index.skipped.get("extensão", 0) >= 1, index.skipped)
check("vocabulário não é vazio", index.stats()["vocabulary"] > 5)

pequeno = Headroom(window=10_000, reserve_output=1_000)
ctx = pack_query("retry backoff interval", index, budget=200, headroom=pequeno)
check("o pacote respeita o orçamento", ctx.tokens_used <= 200, ctx.tokens_used)
check("todo bloco vem citado",
      all(c.citation in ctx.text for c in [s.chunk for s in ctx.selections]) and ctx.citations)
check("citação não se confunde com comentário", "◇ " in ctx.text and "# provider.py" not in ctx.text)
check("o gasto é debitado da janela",
      pequeno.spent.get("context") == ctx.tokens_used, pequeno.spent)

m = ctx.manifest()
check("manifesto traz orçamento pedido e efetivo",
      m["budget"]["requested"] == 200 and m["budget"]["used"] == ctx.tokens_used, m["budget"])
check("manifesto diz se a contagem é exata", "estimated" in m["counting"], m["counting"])
check("manifesto lista o que ficou de fora e por quê",
      all("reason" in d for d in m["dropped"]), m["dropped"][:2])
check("manifesto guarda a janela antes e depois",
      m["headroom"]["before"]["available"] > m["headroom"]["after"]["available"])
check("manifesto traz as estatísticas do índice", m["index"]["chunks"] == len(index.chunks))

sem_espaco = Headroom(window=5_000, reserve_output=1_000)
sem_espaco.spend("conversation", 4_000)
vazio = pack_query("retry", index, budget=500, headroom=sem_espaco)
check("sem janela disponível, devolve contexto vazio", vazio.text == "" and vazio.tokens_used == 0)
check("e explica o motivo no manifesto",
      "sem orçamento" in vazio.manifest()["dropped"][0]["reason"], vazio.manifest()["dropped"])

try:
    index_path(tmp / "nao-existe")
    check("caminho inexistente é recusado", False, "não levantou")
except FileNotFoundError:
    check("caminho inexistente é recusado", True)

print("\n" + ("FALHAS: " + ", ".join(falhas) if falhas else "Tudo verde."))
raise SystemExit(1 if falhas else 0)
