from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev = close.shift(1)
    return pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    return true_range(df).rolling(n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    hist = line - sig
    return line, sig, hist


def donchian(df: pd.DataFrame, n: int) -> tuple[pd.Series, pd.Series]:
    return df["high"].rolling(n).max(), df["low"].rolling(n).min()


def finite(value: float) -> bool:
    return bool(np.isfinite(value))

def stdev(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).std(ddof=0)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(close, n)
    sd = stdev(close, n)
    return mid + k * sd, mid, mid - k * sd


def keltner(df: pd.DataFrame, n: int = 20, m: float = 1.5) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = ema(df["close"], n)
    width = atr(df, n) * m
    return mid + width, mid, mid - width


def stochastic(df: pd.DataFrame, k: int = 14, d: int = 3) -> tuple[pd.Series, pd.Series]:
    low_n = df["low"].rolling(k).min()
    high_n = df["high"].rolling(k).max()
    raw = 100.0 * (df["close"] - low_n) / (high_n - low_n).replace(0, np.nan)
    return raw, sma(raw, d)


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high_n = df["high"].rolling(n).max()
    low_n = df["low"].rolling(n).min()
    return -100.0 * (high_n - df["close"]) / (high_n - low_n).replace(0, np.nan)


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    avg = sma(tp, n)
    mad = tp.rolling(n).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - avg) / (0.015 * mad.replace(0, np.nan))


def roc(close: pd.Series, n: int = 10) -> pd.Series:
    return close.pct_change(n) * 100.0


def adx(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df)
    atr_s = tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, min_periods=n, adjust=False).mean() / atr_s
    minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, min_periods=n, adjust=False).mean() / atr_s
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_s = dx.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    return adx_s, plus_di, minus_di


def parabolic_sar(df: pd.DataFrame, step: float = 0.02, max_af: float = 0.2) -> pd.Series:
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(df)
    sar = np.zeros(n)
    if n == 0:
        return pd.Series(dtype=float)
    up = True
    af = step
    ep = high[0]
    sar[0] = low[0]
    for i in range(1, n):
        prev = sar[i - 1]
        nxt = prev + af * (ep - prev)
        if up:
            nxt = min(nxt, low[i - 1])
            if i >= 2:
                nxt = min(nxt, low[i - 2])
            if low[i] < nxt:
                up = False
                nxt = ep
                ep = low[i]
                af = step
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(max_af, af + step)
        else:
            nxt = max(nxt, high[i - 1])
            if i >= 2:
                nxt = max(nxt, high[i - 2])
            if high[i] > nxt:
                up = True
                nxt = ep
                ep = high[i]
                af = step
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(max_af, af + step)
        sar[i] = nxt
    return pd.Series(sar, index=df.index)


def supertrend(df: pd.DataFrame, n: int = 10, m: float = 3.0) -> tuple[pd.Series, pd.Series]:
    hl2 = (df["high"] + df["low"]) / 2.0
    a = atr(df, n)
    upper = hl2 + m * a
    lower = hl2 - m * a
    st = np.zeros(len(df))
    direction = np.ones(len(df))
    close = df["close"].to_numpy(dtype=float)
    up = upper.to_numpy(dtype=float)
    dn = lower.to_numpy(dtype=float)
    for i in range(1, len(df)):
        if not np.isfinite(up[i]) or not np.isfinite(dn[i]):
            st[i] = close[i]
            direction[i] = direction[i - 1]
            continue
        if close[i - 1] <= st[i - 1]:
            st[i] = up[i] if (not np.isfinite(st[i - 1]) or up[i] < st[i - 1]) else st[i - 1]
            if close[i] > st[i]:
                direction[i] = 1
                st[i] = dn[i]
            else:
                direction[i] = -1
        else:
            st[i] = dn[i] if (not np.isfinite(st[i - 1]) or dn[i] > st[i - 1]) else st[i - 1]
            if close[i] < st[i]:
                direction[i] = -1
                st[i] = up[i]
            else:
                direction[i] = 1
    return pd.Series(st, index=df.index), pd.Series(direction, index=df.index)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    if "volume" in df.columns:
        vol = df["volume"].fillna(1.0).replace(0, 1.0)
    else:
        vol = pd.Series(1.0, index=df.index)
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        days = idx.tz_convert("UTC").date
    else:
        days = idx.date
    key = pd.Series(days, index=df.index)
    return (tp * vol).groupby(key).cumsum() / vol.groupby(key).cumsum().replace(0, np.nan)
