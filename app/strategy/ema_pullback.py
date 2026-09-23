from __future__ import annotations

import pandas as pd

from instruments import Market
from strategy.base import Signal, empty
from strategy.indicators import atr, ema, finite, rsi
from strategy.levels import atr_bracket


def evaluate(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    """Trade with the slow EMA, enter on a bounce off the fast EMA.

    1-minute raw crosses fire too often in chop. This waits for trend
    alignment, a dip into the fast EMA, then a reclaim in the trend direction.
    """
    p = (cfg.get("strategy") or {}).get("ema_pullback") or {}
    slope_bars = int(p.get("slope_bars", 5))
    rsi_period = int(p.get("rsi_period", 14))
    rsi_lo = float(p.get("rsi_lo", 35))
    rsi_hi = float(p.get("rsi_hi", 65))
    touch_atr = float(p.get("touch_atr", 0.25))

    need = max(market.slow_ema, market.atr_period, rsi_period) + slope_bars + 5
    if len(df) < need:
        return empty("not enough bars")

    close = df["close"]
    high = df["high"]
    low = df["low"]
    fast = ema(close, market.fast_ema)
    slow = ema(close, market.slow_ema)
    a = float(atr(df, market.atr_period).iloc[-1])
    r = float(rsi(close, rsi_period).iloc[-1])
    if not finite(a) or a <= 0 or not finite(r):
        return empty("bad indicators")

    price = float(close.iloc[-1])
    f1 = float(fast.iloc[-1])
    s1 = float(slow.iloc[-1])
    s_now = float(slow.iloc[-1])
    s_prev = float(slow.iloc[-1 - slope_bars])
    slope = s_now - s_prev

    # last bar tagged the fast EMA, current bar closes back on the trend side
    prev_low = float(low.iloc[-2])
    prev_high = float(high.iloc[-2])
    f0 = float(fast.iloc[-2])
    touched = prev_low <= f0 + touch_atr * a and prev_high >= f0 - touch_atr * a

    if slope > 0 and price > s1 and touched and price > f1 and r >= rsi_lo:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} pullback long ema{market.fast_ema} rsi={r:.0f}")
    if slope < 0 and price < s1 and touched and price < f1 and r <= rsi_hi:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} pullback short ema{market.fast_ema} rsi={r:.0f}")
    return empty("no pullback")
