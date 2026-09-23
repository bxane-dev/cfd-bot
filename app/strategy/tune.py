from __future__ import annotations

import copy
from dataclasses import replace

from instruments import Market

# Small spaces: 2–4 knobs per named strategy.
SPACES = {
    "ema_atr": lambda t: {"sl_atr": t.suggest_float("sl_atr", 1.2, 2.4), "tp_atr": t.suggest_float("tp_atr", 1.4, 3.0)},
    "ema_pullback": lambda t: {
        "touch_atr": t.suggest_float("touch_atr", 0.10, 0.45),
        "sl_atr": t.suggest_float("sl_atr", 1.2, 2.4),
        "tp_atr": t.suggest_float("tp_atr", 1.4, 3.0),
    },
    "rsi_reversion": lambda t: {
        "oversold": t.suggest_int("oversold", 20, 35),
        "overbought": t.suggest_int("overbought", 65, 80),
        "sl_atr": t.suggest_float("sl_atr", 1.2, 2.4),
        "tp_atr": t.suggest_float("tp_atr", 1.4, 3.0),
    },
    "donchian": lambda t: {
        "length": t.suggest_int("length", 10, 30),
        "sl_atr": t.suggest_float("sl_atr", 1.2, 2.4),
        "tp_atr": t.suggest_float("tp_atr", 1.4, 3.0),
    },
    "macd_trend": lambda t: {
        "signal": t.suggest_int("signal", 6, 12),
        "sl_atr": t.suggest_float("sl_atr", 1.2, 2.4),
        "tp_atr": t.suggest_float("tp_atr", 1.4, 3.0),
    },
    "orb": lambda t: {
        "minutes": t.suggest_int("minutes", 10, 25),
        "expire_minutes": t.suggest_int("expire_minutes", 60, 150),
        "sl_atr": t.suggest_float("sl_atr", 1.2, 2.4),
        "tp_atr": t.suggest_float("tp_atr", 1.4, 3.0),
    },
    "sma_cross": lambda t: {
        "fast": t.suggest_int("fast", 5, 15),
        "slow": t.suggest_int("slow", 18, 40),
        "sl_atr": t.suggest_float("sl_atr", 1.2, 2.4),
        "tp_atr": t.suggest_float("tp_atr", 1.4, 3.0),
    },
    "bollinger": lambda t: {
        "period": t.suggest_int("period", 14, 30),
        "k": t.suggest_float("k", 1.5, 2.8),
        "sl_atr": t.suggest_float("sl_atr", 1.2, 2.4),
    },
    "supertrend": lambda t: {
        "period": t.suggest_int("period", 7, 14),
        "mult": t.suggest_float("mult", 1.8, 4.0),
        "sl_atr": t.suggest_float("sl_atr", 1.2, 2.4),
        "tp_atr": t.suggest_float("tp_atr", 1.4, 3.0),
    },
    "stochastic": lambda t: {
        "oversold": t.suggest_int("oversold", 15, 30),
        "overbought": t.suggest_int("overbought", 70, 85),
        "sl_atr": t.suggest_float("sl_atr", 1.2, 2.4),
    },
    "vwap": lambda t: {"sl_atr": t.suggest_float("sl_atr", 1.2, 2.4), "tp_atr": t.suggest_float("tp_atr", 1.4, 3.0)},
    "keltner": lambda t: {
        "period": t.suggest_int("period", 14, 30),
        "mult": t.suggest_float("mult", 1.2, 2.2),
        "sl_atr": t.suggest_float("sl_atr", 1.2, 2.4),
    },
    "sar": lambda t: {
        "step": t.suggest_float("step", 0.01, 0.04),
        "sl_atr": t.suggest_float("sl_atr", 1.2, 2.4),
        "tp_atr": t.suggest_float("tp_atr", 1.4, 3.0),
    },
}


def suggest(trial, name: str) -> dict:
    fn = SPACES.get(name)
    if not fn:
        return {
            "sl_atr": trial.suggest_float("sl_atr", 1.2, 2.4),
            "tp_atr": trial.suggest_float("tp_atr", 1.4, 3.0),
        }
    params = fn(trial)
    if name == "sma_cross" and params.get("fast", 0) >= params.get("slow", 99):
        params["slow"] = params["fast"] + 8
    if name == "rsi_reversion" and params.get("oversold", 0) >= params.get("overbought", 0):
        params["overbought"] = params["oversold"] + 40
    return params


def apply_params(cfg: dict, market: Market, name: str, params: dict) -> tuple[dict, Market]:
    cfg = copy.deepcopy(cfg)
    block = cfg.setdefault("strategy", {})
    inner = dict(block.get(name) or {})
    sl = params.get("sl_atr")
    tp = params.get("tp_atr")
    extra = {k: v for k, v in params.items() if k not in ("sl_atr", "tp_atr")}
    inner.update(extra)
    block[name] = inner
    kw = {}
    if sl is not None:
        kw["sl_atr"] = float(sl)
    if tp is not None:
        kw["tp_atr"] = float(tp)
    market = replace(market, **kw) if kw else market
    return cfg, market


def apply_tuned(cfg: dict, market: Market) -> tuple[dict, Market]:
    strategy = cfg.get("strategy") or {}
    tuned = (strategy.get("tuned") or {}).get(market.key) or {}
    name = tuned.get("name")
    params = {k: v for k, v in tuned.items() if k != "name"}
    if not name or not params:
        return cfg, market

    routed = (strategy.get("per_market") or {}).get(market.key)
    if routed and str(routed) != str(name):
        # Never apply parameters belonging to a strategy that is no longer
        # routed to this market.
        return cfg, market
    return apply_params(cfg, market, name, params)
