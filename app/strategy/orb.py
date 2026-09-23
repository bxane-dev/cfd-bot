from __future__ import annotations

from datetime import datetime, time

import pandas as pd

from instruments import BERLIN, Market
from strategy.base import Signal, empty
from strategy.indicators import atr, finite
from strategy.levels import atr_bracket


def _session_start(market: Market) -> time:
    start = market.session_windows[0][0]
    h, m = start.split(":")
    return time(int(h), int(m))


def evaluate(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    """Opening-range breakout of the first N minutes of the cash session.

    Built for Germany 40 08:00 and US indices 15:30 Berlin. Only one
    attempt per session (first valid break after the range completes).
    """
    p = (cfg.get("strategy") or {}).get("orb") or {}
    minutes = int(p.get("minutes", 15))
    expire_minutes = int(p.get("expire_minutes", 90))

    if len(df) < max(market.atr_period, minutes) + 10:
        return empty("not enough bars")

    idx = df.index
    if idx.tz is None:
        local = idx.tz_localize("UTC").tz_convert(BERLIN)
    else:
        local = idx.tz_convert(BERLIN)

    last_local = pd.Timestamp(local[-1])
    today = last_local.date()
    sess = _session_start(market)
    start_ts = pd.Timestamp(datetime.combine(today, sess), tz=BERLIN)
    end_range = start_ts + pd.Timedelta(minutes=minutes)
    expire = start_ts + pd.Timedelta(minutes=expire_minutes)

    now_local = last_local
    if now_local < end_range:
        return empty(f"orb forming until {end_range.strftime('%H:%M')}")
    if now_local > expire:
        return empty("orb window closed")

    mask = (local >= start_ts) & (local < end_range)
    rng = df.loc[mask]
    if len(rng) < max(3, minutes // 3):
        return empty("orb range incomplete")

    hi = float(rng["high"].max())
    lo = float(rng["low"].min())
    if hi <= lo:
        return empty("flat orb")

    a = float(atr(df, market.atr_period).iloc[-1])
    if not finite(a) or a <= 0:
        return empty("bad atr")

    price = float(df["close"].iloc[-1])
    prev = float(df["close"].iloc[-2])
    width = hi - lo
    if width < 0.4 * a:
        return empty("orb too tight")

    if prev <= hi and price > hi:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} ORB{minutes} break high {hi:.{market.digits}f}")
    if prev >= lo and price < lo:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} ORB{minutes} break low {lo:.{market.digits}f}")
    return empty("inside opening range")
