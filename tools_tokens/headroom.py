"""Orçamento da janela de contexto.

A janela é finita e o output precisa caber nela também. `Headroom` mantém a
conta viva: quanto a janela tem, quanto a saída reserva, quanto cada parte já
gastou e quanto sobra para o próximo pacote.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class HeadroomExceeded(RuntimeError):
    """Gasto que não cabe no que sobrou da janela."""


@dataclass
class Headroom:
    window: int
    reserve_output: int = 0
    spent: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.window <= 0:
            raise ValueError("window precisa ser positiva")
        if self.reserve_output < 0 or self.reserve_output >= self.window:
            raise ValueError("reserve_output precisa caber na janela")

    # -- conta ---------------------------------------------------------------

    @property
    def total_spent(self) -> int:
        return sum(self.spent.values())

    @property
    def available(self) -> int:
        """O que ainda cabe, já descontada a reserva de saída."""
        return max(0, self.window - self.reserve_output - self.total_spent)

    def spend(self, label: str, tokens: int) -> int:
        """Registra um gasto e devolve o que sobrou."""
        if tokens < 0:
            raise ValueError("gasto negativo")
        if tokens > self.available:
            raise HeadroomExceeded(
                f"'{label}' pede {tokens} tokens e só há {self.available} disponíveis "
                f"(janela {self.window}, reserva de saída {self.reserve_output}, "
                f"já gastos {self.total_spent})"
            )
        self.spent[label] = self.spent.get(label, 0) + tokens
        return self.available

    def allow(self, budget: int) -> int:
        """Recorta um orçamento pedido ao que de fato cabe."""
        return max(0, min(budget, self.available))

    def snapshot(self) -> dict:
        return {
            "window": self.window,
            "reserve_output": self.reserve_output,
            "spent": dict(self.spent),
            "total_spent": self.total_spent,
            "available": self.available,
        }
