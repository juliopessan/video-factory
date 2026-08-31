"""Tempo como inteiro, não como float.

Ideia emprestada do OpenCut (changelog 0.3.0, MIT): representar tempo em ticks
inteiros a 120.000 por segundo em vez de segundos em ponto flutuante. 120.000 foi
escolhido porque divide exato por todo denominador de frame rate padrão —
23,976 dá 5.005 ticks/frame; 29,97 dá 4.004; 30 dá 4.000 — então corte, colagem e
soma de duração nunca acumulam resto.

Aqui isso resolve um problema concreto: as janelas de legenda eram calculadas em
float (`10 - 0.2`, divisões proporcionais ao tamanho do texto), e cada operação
carregava um erro que ninguém via mas que desalinha na hora de cortar por frame.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import gcd

TICKS_PER_SECOND = 120_000


@dataclass(frozen=True)
class FrameRate:
    """Frame rate como racional, não como float: 29,97 é 30000/1001."""

    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        if self.numerator <= 0 or self.denominator <= 0:
            raise ValueError("frame rate precisa ser positivo")

    @property
    def ticks_per_frame(self) -> int:
        """Exato para todo frame rate padrão — é o motivo de 120.000."""
        ticks = TICKS_PER_SECOND * self.denominator
        if ticks % self.numerator:
            raise ValueError(f"{self} não divide 120.000 ticks por segundo de forma exata")
        return ticks // self.numerator

    def __str__(self) -> str:
        if self.denominator == 1:
            return f"{self.numerator}fps"
        divisor = gcd(self.numerator, self.denominator)
        return f"{self.numerator // divisor}/{self.denominator // divisor}fps"


# os frame rates que aparecem em produção
FRAME_RATES = {
    "23.976": FrameRate(24000, 1001),
    "24": FrameRate(24),
    "25": FrameRate(25),
    "29.97": FrameRate(30000, 1001),
    "30": FrameRate(30),
    "50": FrameRate(50),
    "59.94": FrameRate(60000, 1001),
    "60": FrameRate(60),
}


def seconds_to_ticks(seconds: float) -> int:
    return round(seconds * TICKS_PER_SECOND)


def ticks_to_seconds(ticks: int) -> float:
    return ticks / TICKS_PER_SECOND


def snap_to_frame(ticks: int, frame_rate: FrameRate) -> int:
    """Alinha um instante ao frame mais próximo, sem sobrar resto."""
    per_frame = frame_rate.ticks_per_frame
    return round(ticks / per_frame) * per_frame


def split_ticks(total: int, weights: list[int]) -> list[int]:
    """Reparte `total` ticks proporcionalmente aos pesos, sem perder nem inventar.

    O resto da divisão inteira vai para as primeiras fatias, então a soma das
    partes é sempre exatamente `total` — o que float não garante.
    """
    if not weights:
        return []
    soma = sum(weights) or len(weights)
    partes = [total * peso // soma for peso in weights]
    sobra = total - sum(partes)
    for i in range(sobra):
        partes[i % len(partes)] += 1
    return partes


def srt_timestamp(ticks: int) -> str:
    """Marca de tempo do SRT (hh:mm:ss,mmm) a partir de ticks."""
    milissegundos = ticks * 1000 // TICKS_PER_SECOND
    h, resto = divmod(milissegundos, 3_600_000)
    m, resto = divmod(resto, 60_000)
    s, ms = divmod(resto, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
