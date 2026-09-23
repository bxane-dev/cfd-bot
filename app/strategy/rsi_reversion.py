from __future__ import annotations

import pandas as pd

from instruments import Market
from strategy.base import Signal, empty
from strategy.indicators import atr, ema, finite, rsi
from strategy.levels import atr_bracket


def evaluate(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    """Fade stretched 1-minute RSI when the slow EMA is not in a one-way trend.

    Better default for Gold than raw EMA crosses. Skips entries when the
    slow EMA slope is too steep so you are not fading a breakout.
    """
    p = (cfg.get("strategy") or {}).get("rsi_reversion") or {}
    period = int(p.get("period", 14))
    oversold = float(p.get("oversold", 28))
    overbought = float(p.get("overbought", 72))
    slope_bars = int(p.get("slope_bars", 8))
    max_slope_atr = float(p.get("max_slope_atr", 0.9))

    need = max(period, market.slow_ema, market.atr_period) + slope_bars + 5
    if len(df) < need:
        return empty("not enough bars")

    close = df["close"]
    r = rsi(close, period)
    r0, r1 = float(r.iloc[-2]), float(r.iloc[-1])
    a = float(atr(df, market.atr_period).iloc[-1])
    slow = ema(close, market.slow_ema)
    slope = float(slow.iloc[-1] - slow.iloc[-1 - slope_bars])
    if not finite(a) or a <= 0 or not finite(r1) or not finite(r0):
        return empty("bad indicators")

    # too trendy to mean-revert
    if abs(slope) > max_slope_atr * a:
        return empty(f"trend too strong for fade |slope|={abs(slope):.2f}")

    price = float(close.iloc[-1])
    if r0 <= oversold and r1 > oversold:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} rsi{period} leave oversold {r1:.0f}")
    if r0 >= overbought and r1 < overbought:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} rsi{period} leave overbought {r1:.0f}")
    return empty(f"rsi={r1:.0f} no fade")
