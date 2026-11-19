"""Clipper — cortes verticais de 61–90 s a partir da gravação de uma live.

Fluxo: probe -> energia do áudio -> transcrição (palavras com tempo) ->
pontuação de janelas -> render 9:16 com legenda queimada -> fila de revisão.
"""
from __future__ import annotations

MIN_SECONDS = 61.0   # Creator Rewards paga vídeos com mais de 1 min: margem de 1 s
MAX_SECONDS = 90.0
