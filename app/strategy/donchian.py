from __future__ import annotations

import pandas as pd

from instruments import Market
from strategy.base import Signal, empty
from strategy.indicators import atr, donchian, finite
from strategy.levels import atr_bracket


def evaluate(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    """N-bar Donchian breakout. Useful around cash-open momentum on indices."""
    p = (cfg.get("strategy") or {}).get("donchian") or {}
    length = int(p.get("length", 20))
    min_atr_move = float(p.get("min_atr_move", 0.15))

    need = max(length, market.atr_period) + 5
    if len(df) < need:
        return empty("not enough bars")

    upper, lower = donchian(df, length)
    a = float(atr(df, market.atr_period).iloc[-1])
    if not finite(a) or a <= 0:
        return empty("bad atr")

    price = float(df["close"].iloc[-1])
    prev = float(df["close"].iloc[-2])
    up = float(upper.iloc[-2])  # break the *completed* channel
    dn = float(lower.iloc[-2])
    if not finite(up) or not finite(dn):
        return empty("bad channel")

    if prev <= up and price > up and (price - up) >= min_atr_move * a:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} donchian{length} break up")
    if prev >= dn and price < dn and (dn - price) >= min_atr_move * a:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} donchian{length} break down")
    return empty("inside channel")
