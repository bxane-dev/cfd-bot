from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Position:
    ticket: int
    symbol: str
    side: str
    lots: float
    entry: float
    sl: float
    tp: float
    opened_at: datetime = field(default_factory=utcnow)
    deal_id: str = ""
    deal_reference: str = ""
    contract_size: float = 1.0
    upl: float | None = None
    currency: str = ""
    leverage: float | None = None


@dataclass
class Fill:
    ticket: int
    symbol: str
    side: str
    lots: float
    price: float
    sl: float
    tp: float
    comment: str
    ok: bool
    message: str
    deal_id: str = ""
    deal_reference: str = ""


@dataclass
class AccountState:
    equity: float
    balance: float
    currency: str
    positions: list[Position]


class Broker(Protocol):
    def account(self) -> AccountState: ...
    def positions(self) -> list[Position]: ...
    def market_order(self, symbol: str, side: str, lots: float, sl: float, tp: float, comment: str) -> Fill: ...
    def modify_position(self, deal_id: str, sl: float, tp: float, comment: str = "modify") -> Fill: ...
    def close(self, ticket: int, comment: str = "close") -> Fill: ...
    def mark(self, symbol: str, bid: float, ask: float) -> list[dict]: ...
