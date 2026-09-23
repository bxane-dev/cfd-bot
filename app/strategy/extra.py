from __future__ import annotations

import pandas as pd

from instruments import Market
from strategy.base import Signal, empty
from strategy.indicators import (
    adx,
    atr,
    bollinger,
    cci,
    ema,
    finite,
    keltner,
    parabolic_sar,
    roc,
    session_vwap,
    sma,
    stochastic,
    supertrend,
    williams_r,
)
from strategy.levels import atr_bracket


def _p(cfg: dict, name: str) -> dict:
    return ((cfg.get("strategy") or {}).get(name) or {})


def _atr(df: pd.DataFrame, market: Market) -> float:
    return float(atr(df, market.atr_period).iloc[-1])


def evaluate_sma_cross(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "sma_cross")
    fast_n = int(p.get("fast", 9))
    slow_n = int(p.get("slow", 21))
    if len(df) < slow_n + 5:
        return empty("not enough bars")
    fast, slow = sma(df["close"], fast_n), sma(df["close"], slow_n)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    f0, f1 = float(fast.iloc[-2]), float(fast.iloc[-1])
    s0, s1 = float(slow.iloc[-2]), float(slow.iloc[-1])
    price = float(df["close"].iloc[-1])
    if f0 <= s0 and f1 > s1:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} sma{fast_n}/{slow_n} cross up")
    if f0 >= s0 and f1 < s1:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} sma{fast_n}/{slow_n} cross down")
    return empty("no sma cross")


def evaluate_triple_ema(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "triple_ema")
    a_n, b_n, c_n = int(p.get("a", 8)), int(p.get("b", 21)), int(p.get("c", 55))
    if len(df) < c_n + 5:
        return empty("not enough bars")
    e1, e2, e3 = ema(df["close"], a_n), ema(df["close"], b_n), ema(df["close"], c_n)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    stacked_up = float(e1.iloc[-1]) > float(e2.iloc[-1]) > float(e3.iloc[-1])
    stacked_dn = float(e1.iloc[-1]) < float(e2.iloc[-1]) < float(e3.iloc[-1])
    was_up = float(e1.iloc[-2]) > float(e2.iloc[-2]) > float(e3.iloc[-2])
    was_dn = float(e1.iloc[-2]) < float(e2.iloc[-2]) < float(e3.iloc[-2])
    price = float(df["close"].iloc[-1])
    if stacked_up and not was_up:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} triple ema stack up")
    if stacked_dn and not was_dn:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} triple ema stack down")
    return empty("no triple ema flip")


def evaluate_bollinger(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "bollinger")
    n, k = int(p.get("period", 20)), float(p.get("k", 2.0))
    if len(df) < n + 5:
        return empty("not enough bars")
    up, mid, lo = bollinger(df["close"], n, k)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    c0, c1 = float(df["close"].iloc[-2]), float(df["close"].iloc[-1])
    lo0, lo1 = float(lo.iloc[-2]), float(lo.iloc[-1])
    up0, up1 = float(up.iloc[-2]), float(up.iloc[-1])
    if c0 <= lo0 and c1 > lo1:
        return atr_bracket(c1, a, market, cfg, "buy", f"{market.name} bollinger reclaim lower")
    if c0 >= up0 and c1 < up1:
        return atr_bracket(c1, a, market, cfg, "sell", f"{market.name} bollinger reject upper")
    return empty("inside bands")


def evaluate_keltner(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "keltner")
    n, m = int(p.get("period", 20)), float(p.get("mult", 1.5))
    if len(df) < n + 5:
        return empty("not enough bars")
    up, mid, lo = keltner(df, n, m)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    c0, c1 = float(df["close"].iloc[-2]), float(df["close"].iloc[-1])
    if c0 <= float(up.iloc[-2]) and c1 > float(up.iloc[-1]):
        return atr_bracket(c1, a, market, cfg, "buy", f"{market.name} keltner break up")
    if c0 >= float(lo.iloc[-2]) and c1 < float(lo.iloc[-1]):
        return atr_bracket(c1, a, market, cfg, "sell", f"{market.name} keltner break down")
    return empty("inside keltner")


def evaluate_squeeze(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "squeeze")
    n = int(p.get("period", 20))
    if len(df) < n + 5:
        return empty("not enough bars")
    b_up, _, b_lo = bollinger(df["close"], n, float(p.get("bb_k", 2.0)))
    k_up, _, k_lo = keltner(df, n, float(p.get("kc_m", 1.5)))
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    squeezed = float(b_up.iloc[-2]) < float(k_up.iloc[-2]) and float(b_lo.iloc[-2]) > float(k_lo.iloc[-2])
    released = float(b_up.iloc[-1]) >= float(k_up.iloc[-1]) or float(b_lo.iloc[-1]) <= float(k_lo.iloc[-1])
    if not (squeezed and released):
        return empty("no squeeze release")
    price = float(df["close"].iloc[-1])
    mid = float(sma(df["close"], n).iloc[-1])
    if price > mid:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} squeeze release up")
    return atr_bracket(price, a, market, cfg, "sell", f"{market.name} squeeze release down")


def evaluate_stochastic(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "stochastic")
    k_n, d_n = int(p.get("k", 14)), int(p.get("d", 3))
    lo, hi = float(p.get("oversold", 20)), float(p.get("overbought", 80))
    if len(df) < k_n + d_n + 5:
        return empty("not enough bars")
    k, d = stochastic(df, k_n, d_n)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    k0, k1 = float(k.iloc[-2]), float(k.iloc[-1])
    d0, d1 = float(d.iloc[-2]), float(d.iloc[-1])
    price = float(df["close"].iloc[-1])
    if k0 <= d0 and k1 > d1 and k1 < lo + 15:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} stoch cross up {k1:.0f}")
    if k0 >= d0 and k1 < d1 and k1 > hi - 15:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} stoch cross down {k1:.0f}")
    return empty(f"stoch k={k1:.0f}")


def evaluate_cci(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "cci")
    n = int(p.get("period", 20))
    level = float(p.get("level", 100))
    if len(df) < n + 5:
        return empty("not enough bars")
    val = cci(df, n)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    v0, v1 = float(val.iloc[-2]), float(val.iloc[-1])
    price = float(df["close"].iloc[-1])
    if v0 <= -level and v1 > -level:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} cci leave oversold {v1:.0f}")
    if v0 >= level and v1 < level:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} cci leave overbought {v1:.0f}")
    return empty(f"cci={v1:.0f}")


def evaluate_williams(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "williams")
    n = int(p.get("period", 14))
    if len(df) < n + 5:
        return empty("not enough bars")
    w = williams_r(df, n)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    w0, w1 = float(w.iloc[-2]), float(w.iloc[-1])
    price = float(df["close"].iloc[-1])
    if w0 <= -80 and w1 > -80:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} willr leave oversold {w1:.0f}")
    if w0 >= -20 and w1 < -20:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} willr leave overbought {w1:.0f}")
    return empty(f"willr={w1:.0f}")


def evaluate_adx_di(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "adx_di")
    n = int(p.get("period", 14))
    min_adx = float(p.get("min_adx", 20))
    if len(df) < n * 3:
        return empty("not enough bars")
    adx_s, plus_di, minus_di = adx(df, n)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    adx1 = float(adx_s.iloc[-1])
    p0, p1 = float(plus_di.iloc[-2]), float(plus_di.iloc[-1])
    m0, m1 = float(minus_di.iloc[-2]), float(minus_di.iloc[-1])
    price = float(df["close"].iloc[-1])
    if adx1 < min_adx:
        return empty(f"adx weak {adx1:.0f}")
    if p0 <= m0 and p1 > m1:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} +DI cross adx={adx1:.0f}")
    if p0 >= m0 and p1 < m1:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} -DI cross adx={adx1:.0f}")
    return empty(f"adx={adx1:.0f} no DI cross")


def evaluate_sar(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "sar")
    step, mx = float(p.get("step", 0.02)), float(p.get("max_af", 0.2))
    if len(df) < 30:
        return empty("not enough bars")
    sar = parabolic_sar(df, step, mx)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    c0, c1 = float(df["close"].iloc[-2]), float(df["close"].iloc[-1])
    s0, s1 = float(sar.iloc[-2]), float(sar.iloc[-1])
    if c0 <= s0 and c1 > s1:
        return atr_bracket(c1, a, market, cfg, "buy", f"{market.name} psar flip long")
    if c0 >= s0 and c1 < s1:
        return atr_bracket(c1, a, market, cfg, "sell", f"{market.name} psar flip short")
    return empty("sar no flip")


def evaluate_supertrend(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "supertrend")
    n, m = int(p.get("period", 10)), float(p.get("mult", 3.0))
    if len(df) < n + 10:
        return empty("not enough bars")
    st, direction = supertrend(df, n, m)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    d0, d1 = float(direction.iloc[-2]), float(direction.iloc[-1])
    price = float(df["close"].iloc[-1])
    if d0 <= 0 and d1 > 0:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} supertrend flip long")
    if d0 >= 0 and d1 < 0:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} supertrend flip short")
    return empty("supertrend flat")


def evaluate_vwap(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    if len(df) < max(market.atr_period, 30):
        return empty("not enough bars")
    v = session_vwap(df)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    c0, c1 = float(df["close"].iloc[-2]), float(df["close"].iloc[-1])
    v0, v1 = float(v.iloc[-2]), float(v.iloc[-1])
    if not finite(v0) or not finite(v1):
        return empty("bad vwap")
    if c0 <= v0 and c1 > v1:
        return atr_bracket(c1, a, market, cfg, "buy", f"{market.name} vwap reclaim")
    if c0 >= v0 and c1 < v1:
        return atr_bracket(c1, a, market, cfg, "sell", f"{market.name} vwap lose")
    return empty("at vwap")


def evaluate_momentum(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    p = _p(cfg, "momentum")
    n = int(p.get("period", 10))
    if len(df) < n + 5:
        return empty("not enough bars")
    r = roc(df["close"], n)
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    r0, r1 = float(r.iloc[-2]), float(r.iloc[-1])
    price = float(df["close"].iloc[-1])
    if r0 <= 0 and r1 > 0:
        return atr_bracket(price, a, market, cfg, "buy", f"{market.name} roc{n} cross up")
    if r0 >= 0 and r1 < 0:
        return atr_bracket(price, a, market, cfg, "sell", f"{market.name} roc{n} cross down")
    return empty(f"roc={r1:.2f}")


def evaluate_engulfing(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    if len(df) < max(market.slow_ema, market.atr_period) + 5:
        return empty("not enough bars")
    o0, c0 = float(df["open"].iloc[-2]), float(df["close"].iloc[-2])
    o1, c1 = float(df["open"].iloc[-1]), float(df["close"].iloc[-1])
    trend = ema(df["close"], market.slow_ema)
    t1 = float(trend.iloc[-1])
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    bull = c0 < o0 and c1 > o1 and c1 >= o0 and o1 <= c0
    bear = c0 > o0 and c1 < o1 and c1 <= o0 and o1 >= c0
    if bull and c1 >= t1:
        return atr_bracket(c1, a, market, cfg, "buy", f"{market.name} bullish engulfing")
    if bear and c1 <= t1:
        return atr_bracket(c1, a, market, cfg, "sell", f"{market.name} bearish engulfing")
    return empty("no engulfing")


def evaluate_inside_bar(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    if len(df) < max(market.atr_period, 10):
        return empty("not enough bars")
    h2, l2 = float(df["high"].iloc[-3]), float(df["low"].iloc[-3])
    h1, l1 = float(df["high"].iloc[-2]), float(df["low"].iloc[-2])
    c1 = float(df["close"].iloc[-1])
    a = _atr(df, market)
    if not finite(a) or a <= 0:
        return empty("bad atr")
    inside = h1 <= h2 and l1 >= l2 and (h1 - l1) < (h2 - l2)
    if not inside:
        return empty("no inside bar")
    if c1 > h1:
        return atr_bracket(c1, a, market, cfg, "buy", f"{market.name} inside-bar break up")
    if c1 < l1:
        return atr_bracket(c1, a, market, cfg, "sell", f"{market.name} inside-bar break down")
    return empty("inside bar holding")
