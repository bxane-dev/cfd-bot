from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Signal:
    side: str | None  # "buy" | "sell" | None
    sl: float
    tp: float
    stop_distance: float
    reason: str


def empty(reason: str) -> Signal:
    return Signal(None, 0.0, 0.0, 0.0, reason)
