from __future__ import annotations

from instruments import Market
from strategy.base import Signal, empty
from strategy.indicators import finite


def atr_bracket(price: float, atr_val: float, market: Market, cfg: dict, side: str, reason: str) -> Signal:
    if not finite(atr_val) or atr_val <= 0:
        return empty("bad atr")
    min_stop = atr_val * float(cfg["risk"]["min_stop_atr"])
    sl_dist = max(atr_val * market.sl_atr, min_stop)
    tp_dist = atr_val * market.tp_atr
    d = market.digits
    if side == "buy":
        return Signal(side, round(price - sl_dist, d), round(price + tp_dist, d), sl_dist, reason)
    if side == "sell":
        return Signal(side, round(price + sl_dist, d), round(price - tp_dist, d), sl_dist, reason)
    return empty("bad side")
