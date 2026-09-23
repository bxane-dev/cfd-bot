from __future__ import annotations

import pandas as pd

from instruments import Market
from strategy.base import Signal, empty
from strategy.indicators import atr, ema, finite, macd
from strategy.levels import atr_bracket


def evaluate(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    """MACD line cross confirmed by histogram flip and slow EMA side."""
    p = (cfg.get("strategy") or {}).get("macd_trend") or {}
    fast = int(p.get("fast", 12))
    slow_m = int(p.get("slow", 26))
    signal_n = int(p.get("signal", 9))

    need = max(slow_m + signal_n, market.slow_ema, market.atr_period) + 5
    if len(df) < need:
        return empty("not enough bars")

    close = df["close"]
    line, sig, hist = macd(close, fast, slow_m, signal_n)
    a = float(atr(df, market.atr_period).iloc[-1])
    trend = ema(close, market.slow_ema)
    if not finite(a) or a <= 0:
        return empty("bad atr")

    l0, l1 = float(line.iloc[-2]), float(line.iloc[-1])
    s0, s1 = float(sig.iloc[-2]), float(sig.iloc[-1])
    h0, h1 = float(hist.iloc[-2]), float(hist.iloc[-1])
    price = float(close.iloc[-1])
    t1 = float(trend.iloc[-1])

    crossed_up = l0 <= s0 and l1 > s1 and h1 > 0 and h1 > h0
    crossed_dn = l0 >= s0 and l1 < s1 and h1 < 0 and h1 < h0

    if crossed_up and price >= t1:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} macd cross up")
    if crossed_dn and price <= t1:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} macd cross down")
    return empty("no macd setup")
