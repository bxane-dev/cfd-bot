#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from broker.capital import CapitalBroker
from instruments import CAPITAL_EPICS, MARKETS, Market, apply_broker_details, in_session, market_by_symbol
from risk import RiskManager
from strategy.registry import get as get_strategy
from strategy.tune import apply_tuned
from memory import remember, remember_case, recall_context
from news import news_signal
from predict import forecast, agree
from streamers import streamer_signal

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
STATE_PATH = LOG_DIR / "state.json"
TRADES_PATH = LOG_DIR / "trades.csv"
EQUITY_PATH = LOG_DIR / "equity.csv"
_LAST_STATE_TEXT: str | None = None
_LAST_EQUITY_WRITE_AT = 0.0


def load_cfg() -> dict:
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def effective_trade_cfg(cfg: dict, mode: str) -> dict:
    """Apply demo-only frequency overrides without changing LIVE behavior."""
    if str(mode).lower() != "demo":
        return cfg
    execution = cfg.get("execution") or {}
    overrides = execution.get("demo_frequency") or {}
    if not overrides:
        return cfg
    return _deep_merge(cfg, overrides)


def enabled_markets(cfg: dict, mode: str | None = None) -> list[Market]:
    flags = cfg.get("markets") or {}
    execution = cfg.get("execution") or {}
    demo_scope = str(execution.get("demo_market_scope", "live")).strip().lower()
    effective_mode = str(mode or cfg.get("mode") or "demo").strip().lower()
    use_live_scope = effective_mode == "live" or (effective_mode == "demo" and demo_scope == "live")
    out = []
    for key, market in MARKETS.items():
        spec = flags.get(key, True)
        enabled = spec if isinstance(spec, bool) else spec.get("enabled", True)
        if not enabled:
            continue
        if use_live_scope and isinstance(spec, dict) and spec.get("live_enabled", True) is False:
            continue
        out.append(market)
    return out


def ensure_logs() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    if not TRADES_PATH.exists():
        with open(TRADES_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                ["time", "mode", "market", "symbol", "side", "lots", "price", "sl", "tp", "ok", "message"]
            )
    if not EQUITY_PATH.exists():
        with open(EQUITY_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["time", "equity", "open_positions"])


def append_trade(row: list) -> None:
    with open(TRADES_PATH, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


def append_equity(
    equity: float,
    npos: int,
    min_interval: float = 30.0,
    *,
    force: bool = False,
) -> None:
    global _LAST_EQUITY_WRITE_AT
    now = time.monotonic()
    if not force and now - _LAST_EQUITY_WRITE_AT < max(0.0, float(min_interval)):
        return
    with open(EQUITY_PATH, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([datetime.now(timezone.utc).isoformat(), f"{equity:.2f}", npos])
    _LAST_EQUITY_WRITE_AT = now


def save_state(obj: dict) -> None:
    """Persist state only when it changed, using an atomic replace."""
    global _LAST_STATE_TEXT
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    if text == _LAST_STATE_TEXT:
        return
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, STATE_PATH)
    _LAST_STATE_TEXT = text


def load_state() -> dict:
    state = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("last_bar", {})
    state.setdefault("bar_state", {})
    state.setdefault("order_guard", {})
    state.setdefault("diagnostics", {})
    state.setdefault("open_tickets", [])
    state.setdefault("recommendations", {})
    state.setdefault("bot_deal_ids", {})
    state.setdefault("manual_protection", {})
    return state


def make_broker(cfg: dict, mode: str, markets: list[Market] | None = None):
    if mode not in ("demo", "live"):
        raise RuntimeError("Capital.com only. Use --mode demo or --mode live.")
    return CapitalBroker(demo=(mode == "demo"))


def capital_map(markets: list[Market], broker=None) -> dict[str, str]:
    """Resolve configured markets and skip only those unavailable at the broker."""
    out: dict[str, str] = {}
    for market in markets:
        try:
            epic = market.epic or CAPITAL_EPICS.get(market.key, "")
            if not epic:
                if broker is None:
                    raise RuntimeError(f"{market.name} needs a Capital.com market resolver")
                epic = broker.resolve_epic(market.search_term or market.name)
            if epic not in market.live_aliases:
                market.live_aliases.append(epic)
            out[market.key] = epic
        except Exception as exc:
            print(f"  {market.name}: unavailable on Capital.com, skipping ({exc})")
    if not out:
        raise RuntimeError("No configured Capital.com markets could be resolved")
    return out


def resolved_markets(markets: list[Market], live_map: dict[str, str]) -> list[Market]:
    return [market for market in markets if market.key in live_map]


def sync_market_rules(markets: list[Market], broker, live_map: dict[str, str]) -> dict[str, dict]:
    """Refresh deal sizes, margin factors, precision, and trading hours from Capital.com."""
    out: dict[str, dict] = {}
    for market in markets:
        epic = live_map.get(market.key, market.epic)
        try:
            details = broker.market_details(epic)
            out[market.key] = apply_broker_details(market, details)
            r = out[market.key]
            print(
                f"  {market.name}: broker rules size={r['min_lot']}-{r['max_lot']} "
                f"step={r['lot_step']} margin={r['margin_factor']} {r['margin_factor_unit']}"
            )
        except Exception as exc:
            print(f"  {market.name}: broker rules unavailable, using config fallback ({exc})")
    return out


def gross_open_margin(positions, risk: RiskManager) -> float:
    total = 0.0
    for p in positions:
        market = market_by_symbol(p.symbol)
        if market is None:
            continue
        try:
            total += risk.estimate_margin(float(p.entry), float(p.lots), market)
        except (TypeError, ValueError):
            continue
    return total


def note_decision(
    state: dict,
    market: Market,
    stage: str,
    reason: str,
    *,
    status: str = "blocked",
    terminal: bool = False,
    bar_key: str | None = None,
    extra: dict | None = None,
) -> dict:
    row = {
        "time": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "reason": reason,
        "status": status,
        "terminal": bool(terminal),
    }
    if bar_key is not None:
        row["bar"] = bar_key
    if extra:
        row.update(extra)
    state.setdefault("diagnostics", {})[market.key] = row
    if terminal and bar_key is not None:
        state.setdefault("bar_state", {})[market.key] = {
            "bar": bar_key,
            "terminal": True,
            "stage": stage,
        }
        state.setdefault("last_bar", {})[market.key] = bar_key
    return row


def bar_is_terminal(state: dict, market_key: str, bar_key: str) -> bool:
    row = (state.get("bar_state") or {}).get(market_key) or {}
    return row.get("bar") == bar_key and bool(row.get("terminal"))


def terminal_scan_due(
    state: dict,
    market_key: str,
    timeframe: str,
    now: datetime | None = None,
) -> bool:
    """Avoid re-downloading history when the current closed bar is terminal."""
    row = (state.get("bar_state") or {}).get(market_key) or {}
    if not row.get("terminal") or not row.get("bar"):
        return True
    seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }.get(str(timeframe), 60)
    try:
        bar_time = datetime.fromisoformat(str(row["bar"]).replace("Z", "+00:00"))
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=timezone.utc)
        bar_time = bar_time.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return True
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # bar_time is the start of the newest CLOSED candle. The next candle is
    # not closed until two intervals after that timestamp.
    return now >= bar_time + timedelta(seconds=(2 * seconds) + 1)


def retry_scan_due(
    state: dict,
    market_key: str,
    min_interval_seconds: float,
    now: datetime | None = None,
) -> bool:
    """Throttle same-bar retries after non-terminal blockers.

    Technical signals are based on closed candles, so re-running the full
    pipeline every few seconds after a temporary blocker wastes broker/API
    bandwidth without creating a new technical setup.
    """
    interval = max(0.0, float(min_interval_seconds or 0))
    if interval <= 0:
        return True
    row = (state.get("diagnostics") or {}).get(market_key) or {}
    if bool(row.get("terminal")):
        return True
    if str(row.get("status") or "") not in {"blocked", "no_signal"}:
        return True
    raw = row.get("time")
    if not raw:
        return True
    try:
        then = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        then = then.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return True
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (current - then).total_seconds() >= interval


def order_guard_key(market_key: str, bar_key: str, strategy: str, side: str) -> str:
    return f"{market_key}|{bar_key}|{strategy}|{side}"


def reserve_order(state: dict, key: str, payload: dict) -> bool:
    guard = state.setdefault("order_guard", {})
    if key in guard:
        return False
    guard[key] = {
        "time": datetime.now(timezone.utc).isoformat(),
        "status": "reserved",
        **payload,
    }
    while len(guard) > 500:
        guard.pop(next(iter(guard)))
    save_state(state)
    return True


def quality_gate(
    cfg: dict,
    market: Market,
    sig,
    pred: dict,
    price: float,
    spread: float,
    strategy_name: str,
) -> tuple[bool, str, dict]:
    """Conservative entry-quality checks; does not change direction or risk size."""
    qcfg = cfg.get("quality") or {}
    if not qcfg.get("enabled", True):
        return True, "quality gate disabled", {}

    stop = abs(float(price) - float(sig.sl))
    target = abs(float(sig.tp) - float(price))
    rr = target / stop if stop > 0 else 0.0
    min_rr = float(qcfg.get("min_reward_risk", 1.05) or 0.0)
    if rr < min_rr:
        return False, f"quality reject rr={rr:.2f}<{min_rr:.2f}", {"rr": rr}

    if stop > 0:
        spread_ratio = max(0.0, float(spread)) / stop
        max_spread_ratio = float(qcfg.get("max_spread_stop_ratio", 0.18) or 1.0)
        if spread_ratio > max_spread_ratio:
            return False, (
                f"quality reject spread/stop={spread_ratio:.2f}>{max_spread_ratio:.2f}"
            ), {"rr": rr, "spread_stop_ratio": spread_ratio, "retryable": True}

    if qcfg.get("veto_strong_predictor_opposite", True) and pred.get("ok"):
        predicted_side = str(pred.get("side") or "")
        if predicted_side in ("buy", "sell") and predicted_side != sig.side:
            opposite_p = float(
                pred.get("p_up") if predicted_side == "buy" else pred.get("p_down")
            )
            edge_strength = abs(float(pred.get("edge") or 0.0))
            min_p = float(qcfg.get("predictor_opposite_p", 0.62) or 0.62)
            min_edge = float(qcfg.get("predictor_opposite_edge", 0.03) or 0.0)
            if opposite_p >= min_p and edge_strength >= min_edge:
                return False, (
                    f"quality reject strong predictor {predicted_side} "
                    f"p={opposite_p:.2f} edge={edge_strength:.2f}"
                ), {"rr": rr, "predictor_p": opposite_p, "predictor_edge": edge_strength}

    if qcfg.get("use_walk_holdout", True):
        walk_path = LOG_DIR / "walk.json"
        max_age_h = float(qcfg.get("walk_max_age_hours", 72) or 0)
        try:
            fresh = max_age_h <= 0 or (time.time() - walk_path.stat().st_mtime) / 3600.0 <= max_age_h
            if fresh:
                walk = json.loads(walk_path.read_text(encoding="utf-8"))
                block = (walk.get("markets") or {}).get(market.key) or {}
                holdout = block.get("holdout") or {}
                tested_name = str(holdout.get("name") or block.get("winner") or "")
                min_trades = int(qcfg.get("min_holdout_trades", 3) or 0)
                trades = int(holdout.get("trades") or 0)
                if tested_name == strategy_name and trades >= min_trades:
                    pf = float(holdout.get("pf") or 0.0)
                    exp = float(holdout.get("expectancy") or 0.0)
                    pnl = float(holdout.get("pnl") or 0.0)
                    min_pf = float(qcfg.get("min_holdout_profit_factor", 1.05) or 0.0)
                    min_exp = float(qcfg.get("min_holdout_expectancy", 0.0) or 0.0)
                    if pnl <= 0 or pf < min_pf or exp <= min_exp:
                        return False, (
                            f"quality reject holdout n={trades} pnl={pnl:.2f} "
                            f"pf={pf:.2f} exp={exp:.2f}"
                        ), {"rr": rr, "holdout_trades": trades, "holdout_pf": pf, "holdout_expectancy": exp}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    return True, f"quality ok rr={rr:.2f}", {"rr": rr}


def open_index_count(broker, markets: list[Market], positions=None) -> int:
    index_epics = set()
    for m in markets:
        if m.group != "index":
            continue
        index_epics.add(m.epic)
        index_epics.add(m.key)
        index_epics.update(m.live_aliases)
    n = 0
    for p in positions if positions is not None else broker.positions():
        found = market_by_symbol(p.symbol)
        if found and found.group == "index":
            n += 1
        elif p.symbol in index_epics:
            n += 1
    return n


def runtime_lookback_bars(
    cfg: dict,
    market: Market,
    timeframe: str,
    configured_lookback: int,
) -> int:
    """Use a smaller live payload where strategy math does not need 400 bars."""
    if str(timeframe) != "1m":
        return int(configured_lookback)

    strategy_cfg = cfg.get("strategy") or {}
    strategy_name = (
        (strategy_cfg.get("per_market") or {}).get(market.key)
        or strategy_cfg.get("default")
        or strategy_cfg.get("name")
        or ""
    )
    # Session VWAP needs the complete European cash session. Keep a longer
    # window for that one strategy; 180 bars is ample for the other current
    # indicators (largest configured EMA is 55 plus warmup).
    if str(strategy_name).lower() == "vwap":
        return max(int(configured_lookback), 600)

    live_cap = int((cfg.get("execution") or {}).get("runtime_lookback_bars", 180) or 0)
    if live_cap <= 0:
        return int(configured_lookback)
    return max(120, min(int(configured_lookback), live_cap))


def fetch_capital_bars(broker, epic: str, timeframe: str, lookback: int):
    import pandas as pd

    res = {
        "1m": "MINUTE",
        "5m": "MINUTE_5",
        "15m": "MINUTE_15",
        "30m": "MINUTE_30",
        "1h": "HOUR",
        "4h": "HOUR_4",
        "1d": "DAY",
    }.get(timeframe, "MINUTE")
    raw = broker.candles(epic, res, lookback)
    if not raw:
        raise RuntimeError(f"no Capital.com candles for {epic}")

    def mid(obj):
        if isinstance(obj, dict):
            bid = obj.get("bid")
            ask = obj.get("ask")
            if bid is not None and ask is not None:
                return (float(bid) + float(ask)) / 2.0
            return float(bid or ask or 0)
        return float(obj or 0)

    rows = []
    for bar in raw:
        # Prefer the explicitly UTC timestamp. snapshotTime may be account/local time.
        ts = bar.get("snapshotTimeUTC") or bar.get("snapshotTime")
        rows.append(
            {
                "time": ts,
                "open": mid(bar.get("openPrice") or {}),
                "high": mid(bar.get("highPrice") or {}),
                "low": mid(bar.get("lowPrice") or {}),
                "close": mid(bar.get("closePrice") or {}),
            }
        )

    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = df.dropna(subset=["time", "close"]).set_index("time").sort_index()
    if df.empty:
        raise RuntimeError(f"no valid Capital.com candles for {epic}")

    seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }.get(timeframe, 60)
    now_utc = pd.Timestamp.now(tz="UTC")
    if df.index[-1] + pd.Timedelta(seconds=seconds) > now_utc and len(df) > 1:
        df = df.iloc[:-1]
    if df.empty:
        raise RuntimeError(f"no closed Capital.com candles for {epic}")

    # The prices response already carries bid/ask on the newest bar. Avoid a
    # second GET /markets/{epic} request on every scan when possible.
    latest = raw[-1].get("closePrice") or {}
    bid = float(latest.get("bid") or 0) if isinstance(latest, dict) else 0.0
    ask = float(latest.get("ask") or latest.get("offer") or 0) if isinstance(latest, dict) else 0.0
    if bid <= 0 or ask <= 0:
        bid, ask = broker.quote(epic)
    return df, bid, ask


def note_closed_positions(broker, state: dict, positions=None) -> None:
    current = []
    for p in positions if positions is not None else broker.positions():
        current.append(str(getattr(p, "deal_id", None) or p.ticket))
    prev = list(state.get("open_tickets") or [])
    gone = [t for t in prev if t not in current]
    if gone:
        print(f"  closed on Capital.com: {len(gone)} position(s)")
    state["open_tickets"] = current


def _position_id(position) -> str:
    return str(getattr(position, "deal_id", None) or getattr(position, "ticket", "") or "")


def _recommendation_age_seconds(recommendation: dict) -> float | None:
    raw = recommendation.get("time")
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - when).total_seconds())
    except (TypeError, ValueError):
        return None


def portfolio_adjusted_protection(
    equity: float,
    risk: RiskManager,
    market: Market,
    position,
    recommendation: dict,
) -> dict | None:
    """Translate a technical SL/TP into a portfolio-cash-risk-aware SL/TP."""
    try:
        equity = float(equity)
        lots = abs(float(getattr(position, "lots", 0) or 0))
        entry = float(getattr(position, "entry", 0) or 0)
        rec_price = float(recommendation.get("price") or entry)
        rec_sl = float(recommendation.get("sl") or 0)
        rec_tp = float(recommendation.get("tp") or 0)
    except (TypeError, ValueError):
        return None
    if equity <= 0 or lots <= 0 or entry <= 0 or rec_price <= 0 or rec_sl <= 0 or rec_tp <= 0:
        return None

    risk_snapshot = risk.snapshot(equity)
    risk_pct = float(risk_snapshot.get("per_trade") or 0)
    if risk_pct <= 0:
        return None
    risk_cash = equity * risk_pct / 100.0

    value_per_price_unit = (
        max(float(getattr(market, "point_value", 0) or 0), 1e-12)
        * max(float(getattr(market, "contract_size", 1.0) or 1.0), 1e-12)
        * lots
    )
    if value_per_price_unit <= 0:
        return None

    strategy_stop = abs(rec_price - rec_sl)
    strategy_target = abs(rec_tp - rec_price)
    if strategy_stop <= 0 or strategy_target <= 0:
        return None
    rr = strategy_target / strategy_stop

    cash_stop = risk_cash / value_per_price_unit
    stop_distance = min(strategy_stop, cash_stop)
    if stop_distance <= 0:
        return None

    side = str(getattr(position, "side", "") or recommendation.get("side") or "").lower()
    if side == "buy":
        sl = entry - stop_distance
        tp = entry + stop_distance * rr
    elif side == "sell":
        sl = entry + stop_distance
        tp = entry - stop_distance * rr
    else:
        return None

    digits = int(getattr(market, "digits", 5) or 5)
    sl = round(sl, digits)
    tp = round(tp, digits)
    estimated_risk_cash = stop_distance * value_per_price_unit
    return {
        "sl": sl,
        "tp": tp,
        "risk_pct": risk_pct,
        "risk_cash": risk_cash,
        "estimated_risk_cash": estimated_risk_cash,
        "reward_risk": rr,
        "portfolio_adjusted": stop_distance + 1e-12 < strategy_stop,
    }


def sync_manual_trade_protection(
    cfg: dict,
    broker,
    state: dict,
    positions,
    *,
    equity: float | None = None,
    risk: RiskManager | None = None,
) -> bool:
    """Apply the bot's matching SL/TP to each newly seen manual deal exactly once."""
    pcfg = cfg.get("manual_trade_protection") or {}
    if not bool(pcfg.get("enabled", True)):
        return False

    bot_deals = state.setdefault("bot_deal_ids", {})
    protected = state.setdefault("manual_protection", {})
    recommendations = state.setdefault("recommendations", {})
    changed = False

    current = {_position_id(p) for p in positions if _position_id(p)}
    if not state.get("manual_protection_initialized"):
        # Do not rewrite positions that already existed when this feature first
        # came online; they may already have user-adjusted stops/targets.
        if bool(pcfg.get("ignore_existing_on_first_start", True)):
            now = datetime.now(timezone.utc).isoformat()
            for pos in positions:
                deal_id = _position_id(pos)
                if deal_id and deal_id not in bot_deals:
                    protected.setdefault(
                        deal_id,
                        {
                            "status": "preexisting_ignored",
                            "time": now,
                            "symbol": getattr(pos, "symbol", ""),
                        },
                    )
        state["manual_protection_initialized"] = True
        return True

    max_age = float(pcfg.get("max_recommendation_age_seconds", 300) or 0)
    require_matching_side = bool(pcfg.get("require_matching_side", True))

    for pos in positions:
        deal_id = _position_id(pos)
        if not deal_id or deal_id in bot_deals or deal_id in protected:
            continue

        market = market_by_symbol(getattr(pos, "symbol", ""))
        if market is None:
            continue
        rec = recommendations.get(market.key) or {}
        if not rec:
            continue

        age = _recommendation_age_seconds(rec)
        if max_age > 0 and (age is None or age > max_age):
            continue

        rec_side = str(rec.get("side") or "").lower()
        pos_side = str(getattr(pos, "side", "") or "").lower()
        if require_matching_side and rec_side not in {"buy", "sell"}:
            continue
        if require_matching_side and rec_side != pos_side:
            continue

        try:
            sl = float(rec.get("sl") or 0)
            tp = float(rec.get("tp") or 0)
        except (TypeError, ValueError):
            continue
        if sl <= 0 or tp <= 0:
            continue

        portfolio_meta = None
        if bool(pcfg.get("portfolio_based", True)) and equity is not None and risk is not None:
            portfolio_meta = portfolio_adjusted_protection(float(equity), risk, market, pos, rec)
            if portfolio_meta is None:
                continue
            sl = float(portfolio_meta["sl"])
            tp = float(portfolio_meta["tp"])

        result = broker.modify_position(deal_id, sl, tp, "bot-recommended-protection")
        print(
            f"  manual protection {market.name} deal={deal_id}: "
            f"SL {sl:.{market.digits}f} TP {tp:.{market.digits}f} | {result.message}"
        )
        if result.ok:
            protected[deal_id] = {
                "status": "applied_once",
                "time": datetime.now(timezone.utc).isoformat(),
                "market": market.key,
                "side": pos_side,
                "sl": sl,
                "tp": tp,
                "recommendation_time": rec.get("time"),
                "portfolio": portfolio_meta or {},
            }
            remember(
                "manual_protection",
                f"applied bot SL/TP once to manual {market.key} deal {deal_id}",
                how="deal_id_once",
                market=market.key,
                extra={
                    "deal_id": deal_id,
                    "side": pos_side,
                    "sl": sl,
                    "tp": tp,
                    "portfolio": portfolio_meta or {},
                },
            )
            changed = True

    # Keep bot ownership only for deals that can still matter. Protected
    # records intentionally remain, so a later user SL/TP edit is never reset.
    for deal_id in list(bot_deals):
        if deal_id not in current:
            bot_deals.pop(deal_id, None)
            changed = True
    return changed


def run_once(cfg: dict, mode: str, broker, risk: RiskManager, state: dict, markets: list[Market], live_map: dict[str, str]) -> None:
    trade_cfg = effective_trade_cfg(cfg, mode)
    acct = broker.account()
    positions = list(acct.positions or [])
    idx_open = open_index_count(broker, markets, positions)
    note_closed_positions(broker, state, positions)
    sync_manual_trade_protection(cfg, broker, state, positions, equity=acct.equity, risk=risk)
    gate = risk.check_account(acct.equity, len(positions), idx_open)
    print(f"[{mode}] equity={acct.equity:.2f} {acct.currency} open={len(positions)} index={idx_open} gate={gate.reason}")
    if not gate.allowed:
        remember("risk", gate.reason, how="check_account", extra={"equity": acct.equity, "open": len(positions)})
        for market in markets:
            note_decision(state, market, "account_risk", gate.reason, status="blocked", terminal=False)
        append_equity(
            acct.equity,
            len(positions),
            float(cfg.get("equity_log_seconds", 30) or 0),
        )
        save_state(state)
        return

    tf = cfg["timeframe"]
    lookback = int(cfg["lookback_bars"])
    open_total = len(positions)
    open_margin = gross_open_margin(positions, risk)
    open_by_market: dict[str, int] = {}
    for pos in positions:
        found = market_by_symbol(pos.symbol)
        if found:
            open_by_market[found.key] = open_by_market.get(found.key, 0) + 1
    evaluate = get_strategy((trade_cfg.get("strategy") or {}).get("name") or "ema_atr")

    for market in markets:
        symbol = live_map.get(market.key, market.epic)
        open_ok, session_msg = in_session(market)
        if not open_ok:
            print(f"  {market.name}: {session_msg}")
            note_decision(state, market, "session", session_msg, status="blocked", terminal=False)
            continue

        market_open = open_by_market.get(market.key, 0)
        corr = risk.allow_new(
            market,
            idx_open,
            open_market=market_open,
            open_positions=open_total,
            equity=acct.equity,
        )
        if not corr.allowed:
            print(f"  {market.name}: {corr.reason}")
            note_decision(
                state,
                market,
                "position_limit",
                corr.reason,
                status="blocked",
                terminal=False,
            )
            continue

        if not terminal_scan_due(state, market.key, tf):
            continue
        retry_seconds = float(
            (cfg.get("execution") or {}).get("retry_scan_seconds", 15) or 0
        )
        if not retry_scan_due(state, market.key, retry_seconds):
            continue

        try:
            market_lookback = runtime_lookback_bars(
                trade_cfg, market, tf, lookback
            )
            df, bid, ask = fetch_capital_bars(
                broker, symbol, tf, market_lookback
            )
        except Exception as exc:
            why = f"data error {exc}"
            print(f"  {market.name}: {why}")
            note_decision(state, market, "data", why, status="blocked", terminal=False)
            continue

        mid_price = (bid + ask) / 2.0 if bid > 0 and ask > 0 else bid or ask
        state.setdefault("quotes", {})[market.key] = {
            "price": mid_price,
            "spread": (ask - bid) if bid > 0 and ask > 0 else None,
            "updated": datetime.now(timezone.utc).isoformat(),
        }

        bar_key = str(df.index[-1])
        if bar_is_terminal(state, market.key, bar_key):
            print(f"  {market.name}: same bar already completed")
            continue

        spread = risk.spread_ok(bid, ask, market)
        if not spread.allowed:
            print(f"  {market.name}: {spread.reason}")
            remember("skip", spread.reason, how="spread", market=market.key)
            note_decision(state, market, "spread", spread.reason, status="blocked", terminal=False, bar_key=bar_key)
            continue

        cfg_use, market_use = apply_tuned(trade_cfg, market)
        sig = evaluate(df, cfg_use, market_use)
        last = float(df["close"].iloc[-1])
        print(
            f"  {market.name}: px={last:.{market.digits}f} "
            f"spread={(ask - bid):.{market.digits}f} {sig.reason}"
        )
        if not sig.side:
            note_decision(
                state,
                market,
                "signal",
                sig.reason or "no technical signal",
                status="no_signal",
                terminal=True,
                bar_key=bar_key,
            )
            continue

        # Expensive external/context filters only run after technicals produce
        # an actionable setup.
        news = news_signal(trade_cfg, market.key)
        print(f"  {market.name}: {news['reason']}")
        remember(
            "news",
            news["reason"],
            how="direction",
            market=market.key,
            extra={
                "side": news.get("side"),
                "confidence": news.get("confidence"),
                "score": news.get("score"),
                "headlines": news.get("headlines", [])[:3],
            },
        )

        pcfg = trade_cfg.get("predict") or {}
        pred = forecast(df, int(pcfg.get("horizon", 5)), market.atr_period)
        if pred.get("ok"):
            print(f"  {market.name}: {pred['reason']}")
            remember("predict", pred["reason"], how="logit", market=market.key, extra=pred)

        strategy_block = cfg_use.get("strategy") or {}
        configured_strategy = (
            (strategy_block.get("per_market") or {}).get(market.key)
            or strategy_block.get("name")
            or "unknown"
        )

        crowd = streamer_signal(trade_cfg, market)
        state.setdefault("streamer_consensus", {})[market.key] = crowd
        print(f"  {market.name}: {crowd['reason']}")
        remember(
            "streamers",
            crowd["reason"],
            how="public_creator_consensus",
            market=market.key,
            extra={
                "side": crowd.get("side"),
                "confidence": crowd.get("confidence"),
                "votes": crowd.get("votes"),
                "buy_votes": crowd.get("buy_votes"),
                "sell_votes": crowd.get("sell_votes"),
                "sources": (crowd.get("sources") or [])[:5],
            },
        )

        scfg = trade_cfg.get("streamers") or {}
        if scfg.get("enabled", False):
            crowd_side = str(crowd.get("side") or "neutral")
            if scfg.get("require_consensus", False) and crowd_side not in ("buy", "sell"):
                why = crowd.get("reason") or "streamer consensus required"
                print(f"  {market.name}: {why}")
                note_decision(
                    state,
                    market,
                    "streamers",
                    why,
                    status="blocked",
                    terminal=False,
                    bar_key=bar_key,
                    extra={"streamers": crowd},
                )
                continue
            if (
                crowd_side in ("buy", "sell")
                and scfg.get("veto_opposite", True)
                and crowd_side != sig.side
            ):
                why = (
                    f"streamer majority {crowd_side} "
                    f"({int(crowd.get('buy_votes') or 0)} buy / "
                    f"{int(crowd.get('sell_votes') or 0)} sell) "
                    f"opposes technical {sig.side}"
                )
                print(f"  {market.name}: {why}")
                remember("skip", why, how="streamers", market=market.key, extra=crowd)
                note_decision(
                    state,
                    market,
                    "streamers",
                    why,
                    status="blocked",
                    terminal=False,
                    bar_key=bar_key,
                    extra={"streamers": crowd},
                )
                continue
            if crowd_side == sig.side:
                print(
                    f"  {market.name}: STREAMERS CONFIRM {sig.side.upper()} "
                    f"({int(crowd.get('votes') or 0)} creators)"
                )

        quality_ok, quality_reason, quality_extra = quality_gate(
            cfg_use,
            market,
            sig,
            pred,
            last,
            ask - bid,
            str(configured_strategy),
        )
        print(f"  {market.name}: {quality_reason}")
        if not quality_ok:
            remember("skip", quality_reason, how="quality", market=market.key, extra=quality_extra)
            note_decision(
                state,
                market,
                "quality",
                quality_reason,
                status="blocked",
                terminal=not bool(quality_extra.get("retryable")),
                bar_key=bar_key,
                extra=quality_extra,
            )
            continue

        # MEMORY_V2_SETUP
        memory_news = locals().get("news") or locals().get("news_payload") or {}
        if not isinstance(memory_news, dict):
            memory_news = {}
        memory_news_side = str(memory_news.get("side") or "neutral")
        memory_pred_side = (
            str(pred.get("side") or "neutral")
            if isinstance(pred, dict)
            else "neutral"
        )
        recalled = recall_context(
            market=market.key,
            strategy=str(configured_strategy),
            side=sig.side,
            news_side=memory_news_side,
            predictor_side=memory_pred_side,
            limit=5,
        )
        if recalled.get("matches"):
            print(f"  {market.name}: memory -> {recalled['summary']}")
        setup_memory = remember_case(
            event="setup",
            market=market.key,
            strategy=str(configured_strategy),
            side=sig.side,
            technical_reason=sig.reason,
            news=memory_news,
            predictor=pred if isinstance(pred, dict) else {},
            price=last,
            spread=(ask - bid),
            extra={
                "mode": mode,
                "similar_memory_ids": [
                    m.get("id") for m in recalled.get("matches", [])
                ],
            },
        )

        ncfg = trade_cfg.get("news") or {}
        news_side = str(news.get("side") or "neutral")
        news_conf = float(news.get("confidence") or 0.0)
        min_news_conf = float(ncfg.get("min_confidence", 0.62))
        strong_news = news_side in ("buy", "sell") and news_conf >= min_news_conf

        if ncfg.get("enabled", True):
            if ncfg.get("require_signal", False) and not strong_news:
                why = f"news confirmation required ({news_side} {news_conf:.2f})"
                print(f"  {market.name}: {why}")
                remember("skip", why, how="news", market=market.key, extra={"signal": sig.reason})
                note_decision(state, market, "news", why, status="blocked", terminal=False, bar_key=bar_key)
                continue
            if strong_news and ncfg.get("veto_opposite", True) and news_side != sig.side:
                why = f"news {news_side} {news_conf:.2f} opposes technical {sig.side}"
                print(f"  {market.name}: {why}")
                remember("skip", why, how="news", market=market.key, extra={"signal": sig.reason, "news": news})
                note_decision(state, market, "news", why, status="blocked", terminal=False, bar_key=bar_key)
                continue
            if strong_news and news_side == sig.side:
                print(f"  {market.name}: NEWS CONFIRMS {sig.side.upper()} ({news_conf:.2f})")

        if pcfg.get("filter", True) and pred.get("ok"):
            ok, why = agree(pred, sig.side, float(pcfg.get("min_p", 0.55)), float(pcfg.get("min_edge", 0.0)))
            if not ok:
                print(f"  {market.name}: {why}")
                remember("skip", why, how="predict", market=market.key, extra={"signal": sig.reason})
                note_decision(state, market, "predict", why, status="blocked", terminal=True, bar_key=bar_key)
                continue

        sized = risk.size_lots(
            acct.equity,
            sig.stop_distance,
            market,
            price=last,
            allocated_margin=open_margin,
        )
        if not sized.allowed:
            print(f"  {market.name}: {sized.reason}")
            permanent = sized.reason in {"invalid equity/stop/point value", "size below min lot"}
            note_decision(
                state,
                market,
                "sizing",
                sized.reason,
                status="blocked",
                terminal=permanent,
                bar_key=bar_key,
                extra={"margin_used": open_margin},
            )
            continue

        risk_snapshot = risk.snapshot(acct.equity)
        state.setdefault("recommendations", {})[market.key] = {
            "time": datetime.now(timezone.utc).isoformat(),
            "market": market.key,
            "side": sig.side,
            "sl": float(sig.sl),
            "tp": float(sig.tp),
            "price": last,
            "strategy": str(configured_strategy),
            "bar": bar_key,
            "portfolio_equity": float(acct.equity),
            "portfolio_currency": str(acct.currency or ""),
            "risk_pct": float(risk_snapshot.get("per_trade") or 0),
            "risk_cash": float(acct.equity) * float(risk_snapshot.get("per_trade") or 0) / 100.0,
            "suggested_lots": float(sized.lots),
        }

        guard_key = order_guard_key(market.key, bar_key, str(configured_strategy), sig.side)
        if not reserve_order(
            state,
            guard_key,
            {
                "market": market.key,
                "bar": bar_key,
                "strategy": str(configured_strategy),
                "side": sig.side,
                "lots": sized.lots,
            },
        ):
            why = "duplicate order blocked for this strategy/candle/side"
            print(f"  {market.name}: {why}")
            note_decision(state, market, "duplicate_guard", why, status="blocked", terminal=True, bar_key=bar_key)
            continue

        fill = broker.market_order(symbol, sig.side, sized.lots, sig.sl, sig.tp, cfg["broker"]["comment"])
        guard_row = state.setdefault("order_guard", {}).setdefault(guard_key, {})
        guard_row["status"] = "accepted" if fill.ok else "rejected_or_unknown"
        guard_row["message"] = fill.message
        guard_row["updated"] = datetime.now(timezone.utc).isoformat()
        print(
            f"  -> {sig.side.upper()} {sized.lots} {market.name} @ {fill.price:.{market.digits}f} "
            f"SL {sig.sl:.{market.digits}f} TP {sig.tp:.{market.digits}f} | {fill.message}"
        )
        remember(
            "trade",
            f"{sig.side} {sized.lots} {market.key} @ {fill.price} sl={sig.sl} tp={sig.tp}",
            how=sig.reason,
            market=market.key,
            extra={"ok": fill.ok, "message": fill.message, "mode": mode, "symbol": symbol},
        )
        append_trade(
            [
                datetime.now(timezone.utc).isoformat(),
                mode,
                market.key,
                symbol,
                sig.side,
                sized.lots,
                fill.price,
                sig.sl,
                sig.tp,
                fill.ok,
                fill.message,
            ]
        )
        remember_case(
            event="order",
            market=market.key,
            strategy=str(configured_strategy),
            side=sig.side,
            technical_reason=sig.reason,
            news=memory_news,
            predictor=pred if isinstance(pred, dict) else {},
            price=fill.price or last,
            spread=(ask - bid),
            parent_id=setup_memory.get("id"),
            extra={
                "mode": mode,
                "lots": sized.lots,
                "sl": sig.sl,
                "tp": sig.tp,
                "accepted": bool(fill.ok),
                "broker_message": fill.message,
            },
        )
        note_decision(
            state,
            market,
            "order",
            fill.message or ("accepted" if fill.ok else "broker rejected or confirmation unknown"),
            status="trade" if fill.ok else "order_failed",
            terminal=True,
            bar_key=bar_key,
            extra={
                "side": sig.side,
                "lots": sized.lots,
                "price": fill.price or last,
                "strategy": str(configured_strategy),
            },
        )
        save_state(state)

        if fill.ok:
            bot_deal_id = str(getattr(fill, "deal_id", "") or "")
            if bot_deal_id:
                state.setdefault("bot_deal_ids", {})[bot_deal_id] = {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "market": market.key,
                    "side": sig.side,
                }
                save_state(state)
            open_total += 1
            open_by_market[market.key] = open_by_market.get(market.key, 0) + 1
            open_margin += risk.estimate_margin(float(fill.price or last), float(sized.lots), market)
            if market.group == "index":
                idx_open += 1

    # The next loop starts with a fresh account/positions snapshot. Avoid a
    # second /accounts + /positions round trip at the end of every scan.
    append_equity(
        acct.equity,
        open_total,
        float(cfg.get("equity_log_seconds", 30) or 0),
    )
    save_state(state)


def main() -> None:
    load_dotenv(ROOT / ".env")
    cfg = load_cfg()
    parser = argparse.ArgumentParser(description="Capital.com 21-market strategy desk")
    parser.add_argument("--mode", choices=["demo", "live"], default=os.getenv("MODE") or cfg.get("mode", "demo"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--only", nargs="*", choices=list(MARKETS.keys()))
    args = parser.parse_args()

    markets = enabled_markets(cfg, args.mode)
    if args.only:
        markets = [m for m in markets if m.key in args.only]

    print("Capital.com desk: 21 strategy-mapped markets")
    print("Markets:", ", ".join(m.name for m in markets) or "(none enabled)")
    print("DEMO" if args.mode == "demo" else "LIVE — real money")

    ensure_logs()
    broker = make_broker(cfg, args.mode, markets)
    live_map = capital_map(markets, broker)
    markets = resolved_markets(markets, live_map)
    sync_market_rules(markets, broker, live_map)
    for m in markets:
        print(f"  {m.name}: {live_map[m.key]}")
    risk = RiskManager(cfg)
    state = load_state()

    while True:
        try:
            run_once(cfg, args.mode, broker, risk, state, markets, live_map)
        except KeyboardInterrupt:
            print("stopped")
            break
        except Exception as exc:
            print(f"loop error: {exc}")
        if args.once:
            break
        time.sleep(int(cfg.get("poll_seconds", 5)))


if __name__ == "__main__":
    main()
