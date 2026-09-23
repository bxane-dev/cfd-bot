from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from instruments import Market
from strategy.base import Signal
from strategy import donchian, ema_atr, ema_pullback, extra, macd_trend, orb, rsi_reversion

Evaluator = Callable[[pd.DataFrame, dict, Market], Signal]

STRATEGIES: dict[str, Evaluator] = {
    "ema_atr": ema_atr.evaluate,
    "ema_pullback": ema_pullback.evaluate,
    "rsi_reversion": rsi_reversion.evaluate,
    "donchian": donchian.evaluate,
    "macd_trend": macd_trend.evaluate,
    "orb": orb.evaluate,
    "sma_cross": extra.evaluate_sma_cross,
    "triple_ema": extra.evaluate_triple_ema,
    "bollinger": extra.evaluate_bollinger,
    "keltner": extra.evaluate_keltner,
    "squeeze": extra.evaluate_squeeze,
    "stochastic": extra.evaluate_stochastic,
    "cci": extra.evaluate_cci,
    "williams": extra.evaluate_williams,
    "adx_di": extra.evaluate_adx_di,
    "sar": extra.evaluate_sar,
    "supertrend": extra.evaluate_supertrend,
    "vwap": extra.evaluate_vwap,
    "momentum": extra.evaluate_momentum,
    "engulfing": extra.evaluate_engulfing,
    "inside_bar": extra.evaluate_inside_bar,
}

ALIASES = {
    "bb": "bollinger",
    "bands": "bollinger",
    "stoch": "stochastic",
    "willr": "williams",
    "psar": "sar",
    "parabolic_sar": "sar",
    "st": "supertrend",
    "roc": "momentum",
    "di": "adx_di",
    "adx": "adx_di",
    "sma": "sma_cross",
    "tema": "triple_ema",
    "kc": "keltner",
}


def get(name: str) -> Evaluator:
    key = (name or "ema_atr").strip().lower()
    key = ALIASES.get(key, key)
    if key in ("router", "combo", "auto"):
        from strategy.router import evaluate as router_eval

        return router_eval
    if key not in STRATEGIES:
        known = ", ".join(sorted(list(STRATEGIES) + ["router"]))
        raise KeyError(f"unknown strategy '{name}'. Choose one of: {known}")
    return STRATEGIES[key]


def names() -> list[str]:
    return sorted(list(STRATEGIES) + ["router", "auto"])
