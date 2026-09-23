from __future__ import annotations

import pandas as pd

from instruments import Market
from strategy.base import Signal, empty
from strategy.indicators import atr, ema, finite
from strategy.levels import atr_bracket


def evaluate(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    """Classic fast/slow EMA cross with ATR stop and target."""
    if len(df) < max(market.slow_ema, market.atr_period) + 5:
        return empty("not enough bars")

    close = df["close"]
    fast = ema(close, market.fast_ema)
    slow = ema(close, market.slow_ema)
    a = float(atr(df, market.atr_period).iloc[-1])
    if not finite(a) or a <= 0:
        return empty("bad atr")

    f0, f1 = float(fast.iloc[-2]), float(fast.iloc[-1])
    s0, s1 = float(slow.iloc[-2]), float(slow.iloc[-1])
    price = float(close.iloc[-1])

    if f0 <= s0 and f1 > s1:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} ema{market.fast_ema}/{market.slow_ema} cross up")
    if f0 >= s0 and f1 < s1:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} ema{market.fast_ema}/{market.slow_ema} cross down")
    return empty("no cross")
