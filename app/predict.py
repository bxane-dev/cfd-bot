from __future__ import annotations

import math

import numpy as np
import pandas as pd

from strategy.indicators import atr, rsi


def _features(df: pd.DataFrame, i: int, atr_p: int) -> np.ndarray | None:
    if i < max(30, atr_p + 5):
        return None
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    c = close[i]
    if not math.isfinite(c) or c == 0:
        return None
    r1 = (c - close[i - 1]) / c
    r5 = (c - close[i - 5]) / c
    r15 = (c - close[i - 15]) / c
    window = close[i - 19 : i + 1]
    vol = float(np.std(np.diff(window) / window[:-1])) if len(window) > 2 else 0.0
    rng = (high[i] - low[i]) / c
    # simple rsi-like 14
    diff = np.diff(close[i - 14 : i + 1])
    up = float(np.clip(diff, 0, None).mean()) if len(diff) else 0.0
    dn = float(np.clip(-diff, 0, None).mean()) if len(diff) else 0.0
    rs = up / dn if dn > 1e-12 else 10.0
    rsi_n = 100.0 - 100.0 / (1.0 + rs)
    return np.array([1.0, r1, r5, r15, vol, rng, (rsi_n - 50.0) / 50.0], dtype=float)


def _fit_logit(X: np.ndarray, y: np.ndarray, l2: float = 0.5, steps: int = 80) -> np.ndarray:
    w = np.zeros(X.shape[1], dtype=float)
    lr = 0.35
    for _ in range(steps):
        z = np.clip(X @ w, -12, 12)
        p = 1.0 / (1.0 + np.exp(-z))
        grad = X.T @ (p - y) / len(y) + l2 * w
        w -= lr * grad
    return w


def forecast(df: pd.DataFrame, horizon: int = 5, atr_period: int = 14) -> dict:
    """P(close[t+h] > close[t]) from a leak-free logistic fit on this window."""
    n = len(df)
    h = max(1, int(horizon))
    if n < 80:
        return {"ok": False, "reason": "not enough bars"}
    rows, labels = [], []
    last = n - 1
    train_end = last - h  # cannot use labels that need future past now
    for i in range(40, train_end):
        feat = _features(df, i, atr_period)
        if feat is None:
            continue
        future = float(df["close"].iloc[i + h])
        now = float(df["close"].iloc[i])
        rows.append(feat)
        labels.append(1.0 if future > now else 0.0)
    if len(rows) < 40:
        return {"ok": False, "reason": "not enough labeled rows"}
    X = np.vstack(rows)
    y = np.array(labels)
    w = _fit_logit(X, y)
    feat_now = _features(df, last, atr_period)
    if feat_now is None:
        return {"ok": False, "reason": "bad last features"}
    z = float(np.clip(feat_now @ w, -12, 12))
    p_up = 1.0 / (1.0 + math.exp(-z))
    base = float(y.mean())
    a = atr(df, atr_period)
    last_atr = float(a.iloc[-1]) if len(a) and math.isfinite(float(a.iloc[-1])) else 0.0
    px = float(df["close"].iloc[-1])
    side = "buy" if p_up >= 0.5 else "sell"
    return {
        "ok": True,
        "horizon": h,
        "p_up": p_up,
        "p_down": 1.0 - p_up,
        "base_up": base,
        "edge": p_up - base,
        "side": side,
        "atr": last_atr,
        "price": px,
        "samples": int(len(y)),
        "reason": f"pred h={h} p_up={p_up:.2f} base={base:.2f} edge={p_up-base:+.2f}",
    }


def agree(pred: dict, signal_side: str, min_p: float, min_edge: float) -> tuple[bool, str]:
    if not pred.get("ok"):
        return False, pred.get("reason") or "no forecast"
    p = float(pred["p_up"] if signal_side == "buy" else pred["p_down"])
    edge = float(pred.get("edge") or 0.0)
    if signal_side == "sell":
        edge = -edge
    if p < min_p:
        return False, f"predict reject p={p:.2f}<{min_p:.2f} ({pred['reason']})"
    if edge < min_edge:
        return False, f"predict reject edge={edge:+.2f}<{min_edge:.2f}"
    return True, pred["reason"]
