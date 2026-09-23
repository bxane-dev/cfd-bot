from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from instruments import Market
from strategy.base import Signal, empty
from strategy.registry import get as get_strategy

WALK_PATH = Path(__file__).resolve().parents[2] / "logs" / "walk.json"


def _walk_map() -> dict[str, str]:
    if not WALK_PATH.exists():
        return {}
    try:
        payload = json.loads(WALK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for key, block in (payload.get("markets") or {}).items():
        winner = block.get("winner")
        if winner and block.get("holdout_pass") is True:
            out[key] = winner
    return out


def evaluate(df: pd.DataFrame, cfg: dict, market: Market) -> Signal:
    block = cfg.get("strategy") or {}
    mapping = dict(block.get("per_market") or {})
    if (block.get("name") == "auto") or block.get("use_walk"):
        mapping.update(_walk_map())
    name = mapping.get(market.key) or block.get("default") or "ema_pullback"
    if name in ("router", "combo", "auto"):
        return empty("router cannot route to itself")
    fn = get_strategy(name)
    sig = fn(df, cfg, market)
    sig.reason = f"[{name}] {sig.reason}"
    return sig
