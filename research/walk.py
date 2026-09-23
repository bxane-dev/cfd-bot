#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv

from instruments import MARKETS, Market, apply_broker_details
from main import ROOT, load_cfg, make_broker
from strategy.registry import STRATEGIES
from strategy.tune import apply_params, suggest

WALK_PATH = ROOT / "logs" / "walk.json"


def bars_to_df(raw: list) -> pd.DataFrame:
    rows = []
    for bar in raw:
        ts = bar.get("snapshotTimeUTC") or bar.get("snapshotTime")
        o = bar.get("openPrice") or {}
        h = bar.get("highPrice") or {}
        l = bar.get("lowPrice") or {}
        c = bar.get("closePrice") or {}

        def mid(obj):
            if isinstance(obj, dict):
                bid = obj.get("bid")
                ask = obj.get("ask")
                if bid is not None and ask is not None:
                    return (float(bid) + float(ask)) / 2.0
                return float(bid or ask or 0)
            return float(obj or 0)

        rows.append({"time": ts, "open": mid(o), "high": mid(h), "low": mid(l), "close": mid(c)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = df.dropna(subset=["time", "close"]).drop_duplicates("time").sort_values("time").set_index("time")
    return df


def fetch_history(broker, epic: str, resolution: str, bars: int) -> pd.DataFrame:
    chunks = []
    remaining = max(int(bars), 200)
    end = datetime.now(timezone.utc)
    resolution_minutes = {
        "MINUTE": 1,
        "MINUTE_5": 5,
        "MINUTE_15": 15,
        "MINUTE_30": 30,
        "HOUR": 60,
        "HOUR_4": 240,
        "DAY": 1440,
    }.get(resolution, 1)
    while remaining > 0:
        take = min(remaining, 1000)
        start = end - timedelta(minutes=resolution_minutes * (take + 5))
        raw = broker.candles(
            epic,
            resolution,
            take,
            start=start.strftime("%Y-%m-%dT%H:%M:%S"),
            end=end.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        if not raw:
            # last-n without from/to as fallback
            raw = broker.candles(epic, resolution, take)
            df = bars_to_df(raw)
            if not df.empty:
                chunks.append(df)
            break
        df = bars_to_df(raw)
        if df.empty:
            break
        chunks.append(df)
        remaining -= len(df)
        end = df.index[0].to_pydatetime() - timedelta(seconds=1)
        if len(df) < max(20, take // 5):
            break
    if not chunks:
        raise RuntimeError(f"no history for {epic}")
    out = pd.concat(chunks).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    if not out.empty:
        step = pd.Timedelta(minutes=resolution_minutes)
        now = pd.Timestamp.now(tz="UTC")
        if out.index[-1] + step > now and len(out) > 1:
            out = out.iloc[:-1]
    return out.tail(int(bars))


def _regime(window: pd.DataFrame, market: Market) -> str:
    if len(window) < max(30, market.atr_period + 5):
        return "unknown"
    close = window["close"].astype(float)
    high = window["high"].astype(float)
    low = window["low"].astype(float)
    prev = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev).abs(), (low - prev).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(max(2, market.atr_period)).mean()
    current_atr = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else 0.0
    atr_median = float(atr.tail(60).median()) if atr.tail(60).notna().any() else current_atr
    fast = float(close.ewm(span=max(2, market.fast_ema), adjust=False).mean().iloc[-1])
    slow = float(close.ewm(span=max(3, market.slow_ema), adjust=False).mean().iloc[-1])
    strength = abs(fast - slow) / max(current_atr, 1e-12)
    direction = "range" if strength < 0.5 else ("bull" if fast > slow else "bear")
    if atr_median <= 0:
        vol = "normal_vol"
    elif current_atr >= atr_median * 1.25:
        vol = "high_vol"
    elif current_atr <= atr_median * 0.80:
        vol = "low_vol"
    else:
        vol = "normal_vol"
    return f"{direction}_{vol}"


def _regime_stats(records: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for row in records:
        key = row.get("regime") or "unknown"
        bucket = out.setdefault(key, {"trades": 0, "wins": 0, "pnl": 0.0})
        bucket["trades"] += 1
        bucket["wins"] += int(float(row.get("pnl") or 0) > 0)
        bucket["pnl"] += float(row.get("pnl") or 0)
    for bucket in out.values():
        n = bucket["trades"]
        bucket["win_rate"] = (bucket["wins"] / n * 100.0) if n else 0.0
        bucket["expectancy"] = (bucket["pnl"] / n) if n else 0.0
    return out


def simulate(
    df: pd.DataFrame,
    market: Market,
    cfg: dict,
    evaluate,
    risk_pct: float,
    start_equity: float,
    *,
    active_start: int | None = None,
) -> dict:
    look = max(80, int(cfg.get("lookback_bars", 400)))
    warmup = max(market.slow_ema, market.atr_period, 60) + 5
    i = max(warmup, int(active_start) if active_start is not None else warmup)
    equity = float(start_equity)
    peak = equity
    pnl = 0.0
    max_dd = 0.0
    max_dd_pct = 0.0
    wins = losses = 0
    trades: list[float] = []
    records: list[dict] = []
    cooldown = 0
    research_cfg = cfg.get("research") or {}
    slippage_fraction = max(0.0, float(research_cfg.get("slippage_spread_fraction", 0.10) or 0))
    max_hold_bars = max(1, int(research_cfg.get("max_hold_bars", 120) or 120))
    spread_points = max(0.0, float(market.typical_spread))
    adverse_side_cost = spread_points / 2.0 + spread_points * slippage_fraction

    while i < len(df) - 1:
        if cooldown > 0:
            cooldown -= 1
            i += 1
            continue
        window = df.iloc[max(0, i + 1 - look) : i + 1]
        try:
            sig = evaluate(window, cfg, market)
        except Exception as exc:
            return {
                "error": str(exc)[:160],
                "trades": 0,
                "pnl": 0.0,
                "win_rate": 0.0,
                "max_dd": 0.0,
                "max_dd_pct": 0.0,
                "pf": 0.0,
                "expectancy": 0.0,
                "avg_hold_bars": 0.0,
                "estimated_costs": 0.0,
                "regimes": {},
                "score": float("-inf"),
            }
        i += 1
        if not sig.side:
            continue

        entry_mid = float(df["close"].iloc[i - 1])
        sl, tp = float(sig.sl), float(sig.tp)
        stop = abs(entry_mid - sl) or 1e-9
        value_per_price_unit = (
            max(float(market.point_value), 1e-12)
            * max(float(getattr(market, "contract_size", 1.0) or 1.0), 1e-12)
        )
        raw_lots = (equity * risk_pct) / (stop * value_per_price_unit)
        raw_lots = min(market.max_lot, raw_lots)
        step = max(float(market.lot_step), 1e-12)
        lots = math.floor((raw_lots + 1e-12) / step) * step
        lots = round(min(lots, market.max_lot), 8)
        if lots < market.min_lot:
            continue

        entry_px = entry_mid + adverse_side_cost if sig.side == "buy" else entry_mid - adverse_side_cost
        result = "timeout"
        exit_mid = float(df["close"].iloc[min(len(df) - 1, i)])
        j = i
        horizon = min(len(df), i + max_hold_bars)
        for j in range(i, horizon):
            high = float(df["high"].iloc[j])
            low = float(df["low"].iloc[j])
            if sig.side == "buy":
                if low <= sl:
                    result, exit_mid = "sl", sl
                    break
                if high >= tp:
                    result, exit_mid = "tp", tp
                    break
            else:
                if high >= sl:
                    result, exit_mid = "sl", sl
                    break
                if low <= tp:
                    result, exit_mid = "tp", tp
                    break
            exit_mid = float(df["close"].iloc[j])

        exit_px = exit_mid - adverse_side_cost if sig.side == "buy" else exit_mid + adverse_side_cost
        move = (exit_px - entry_px) if sig.side == "buy" else (entry_px - exit_px)
        trade_pnl = move * lots * value_per_price_unit
        estimated_cost = (
            spread_points + 2.0 * spread_points * slippage_fraction
        ) * lots * value_per_price_unit
        hold_bars = max(1, j - (i - 1))
        regime = _regime(window, market)

        pnl += trade_pnl
        equity += trade_pnl
        peak = max(peak, equity)
        drawdown = peak - equity
        max_dd = max(max_dd, drawdown)
        max_dd_pct = max(max_dd_pct, (drawdown / peak * 100.0) if peak > 0 else 0.0)
        if trade_pnl >= 0:
            wins += 1
        else:
            losses += 1
            cooldown = 2
        trades.append(trade_pnl)
        records.append(
            {
                "pnl": trade_pnl,
                "result": result,
                "hold_bars": hold_bars,
                "regime": regime,
                "estimated_cost": estimated_cost,
            }
        )
        i = j + 1

    n = len(trades)
    wr = (wins / n * 100.0) if n else 0.0
    gross_win = sum(x for x in trades if x > 0)
    gross_loss = abs(sum(x for x in trades if x < 0))
    pf = (gross_win / gross_loss) if gross_loss else (2.0 if gross_win else 0.0)
    expectancy = (pnl / n) if n else 0.0
    avg_hold = (sum(r["hold_bars"] for r in records) / n) if n else 0.0
    estimated_costs = sum(r["estimated_cost"] for r in records)
    score = (pnl / max(max_dd, 1.0)) if n else float("-inf")
    if n < 5:
        score = float("-inf") if n == 0 else score * 0.25
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "pnl": pnl,
        "max_dd": max_dd,
        "max_dd_pct": max_dd_pct,
        "pf": pf,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "expectancy": expectancy,
        "avg_hold_bars": avg_hold,
        "estimated_costs": estimated_costs,
        "regimes": _regime_stats(records),
        "score": score,
        "error": None,
    }

def optuna_search(df, market, cfg, name, risk_pct, trials: int) -> tuple[dict, float]:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.ERROR)

    def objective(trial):
        params = suggest(trial, name)
        cfg2, m2 = apply_params(cfg, market, name, params)
        st = simulate(df, m2, cfg2, STRATEGIES[name], risk_pct, 10_000.0)
        if (
            st.get("error")
            or st["trades"] < 3
            or float(st.get("pnl") or 0.0) <= 0
            or float(st.get("expectancy") or 0.0) <= 0
            or float(st.get("pf") or 0.0) <= 1.0
        ):
            return -1_000_000.0
        sc = st["score"]
        return float(sc) if sc != float("-inf") else -1_000_000.0

    sampler = optuna.samplers.TPESampler(seed=17, n_startup_trials=min(5, max(2, trials // 3)))
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=max(1, trials), show_progress_bar=False)
    return dict(study.best_params), float(study.best_value)


def pick_winner(rows: list[dict]) -> dict | None:
    def positive(r: dict, min_trades: int) -> bool:
        return (
            not r.get("error")
            and int(r.get("trades") or 0) >= min_trades
            and float(r.get("pnl") or 0.0) > 0
            and float(r.get("expectancy") or 0.0) > 0
            and float(r.get("pf") or 0.0) > 1.0
        )

    usable = [r for r in rows if positive(r, 5)]
    if not usable:
        usable = [r for r in rows if positive(r, 2)]
    if not usable:
        return None
    usable.sort(
        key=lambda r: (r["score"], r["expectancy"], r["pf"], r["pnl"], r["win_rate"]),
        reverse=True,
    )
    return usable[0]


def holdout_passes(stats: dict | None, cfg: dict) -> tuple[bool, str]:
    if not stats or stats.get("error"):
        return False, "no valid holdout"
    rcfg = cfg.get("research") or {}
    min_trades = int(rcfg.get("min_holdout_trades", 3) or 0)
    min_pf = float(rcfg.get("min_holdout_profit_factor", 1.05) or 0.0)
    min_exp = float(rcfg.get("min_holdout_expectancy", 0.0) or 0.0)
    n = int(stats.get("trades") or 0)
    pnl = float(stats.get("pnl") or 0.0)
    pf = float(stats.get("pf") or 0.0)
    exp = float(stats.get("expectancy") or 0.0)
    ok = n >= min_trades and pnl > 0 and pf >= min_pf and exp > min_exp
    return ok, f"n={n} pnl={pnl:.2f} pf={pf:.2f} exp={exp:.2f}"


def folds(n: int, train: int, test: int, embargo: int, method: str) -> list[tuple[int, int, int, int]]:
    """Return (train_start, train_end, test_start, test_end) half-open index ranges."""
    out = []
    if method == "in_sample" or n < train + test + embargo:
        return []
    cursor = train
    while cursor + embargo + test <= n:
        if method == "anchored":
            tr0 = 0
        else:
            tr0 = cursor - train
        tr1 = cursor
        te0 = cursor + embargo
        te1 = te0 + test
        out.append((tr0, tr1, te0, te1))
        cursor += test
    return out


def merge_stats(parts: list[dict], name: str) -> dict:
    trades = [p for p in parts if not p.get("error")]
    n = sum(p["trades"] for p in trades)
    wins = sum(p.get("wins", 0) for p in trades)
    losses = sum(p.get("losses", 0) for p in trades)
    pnl = sum(p["pnl"] for p in trades)
    max_dd = max((p.get("max_dd", 0.0) for p in trades), default=0.0)
    max_dd_pct = max((p.get("max_dd_pct", 0.0) for p in trades), default=0.0)
    gross_win = sum(p.get("gross_win", 0.0) for p in trades)
    gross_loss = sum(p.get("gross_loss", 0.0) for p in trades)
    pf = (gross_win / gross_loss) if gross_loss else (2.0 if gross_win else 0.0)
    wr = (wins / n * 100.0) if n else 0.0
    expectancy = (pnl / n) if n else 0.0
    avg_hold = (
        sum(float(p.get("avg_hold_bars", 0.0)) * int(p.get("trades", 0)) for p in trades) / n
        if n
        else 0.0
    )
    estimated_costs = sum(float(p.get("estimated_costs", 0.0)) for p in trades)

    regimes: dict[str, dict] = {}
    for part in trades:
        for key, row in (part.get("regimes") or {}).items():
            bucket = regimes.setdefault(key, {"trades": 0, "wins": 0, "pnl": 0.0})
            bucket["trades"] += int(row.get("trades", 0))
            bucket["wins"] += int(row.get("wins", 0))
            bucket["pnl"] += float(row.get("pnl", 0.0))
    for bucket in regimes.values():
        rn = bucket["trades"]
        bucket["win_rate"] = (bucket["wins"] / rn * 100.0) if rn else 0.0
        bucket["expectancy"] = (bucket["pnl"] / rn) if rn else 0.0

    score = (pnl / max(max_dd, 1.0)) if n else float("-inf")
    if n < 5:
        score = float("-inf") if n == 0 else score * 0.25
    return {
        "name": name,
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "pnl": pnl,
        "max_dd": max_dd,
        "max_dd_pct": max_dd_pct,
        "pf": pf,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "expectancy": expectancy,
        "avg_hold_bars": avg_hold,
        "estimated_costs": estimated_costs,
        "regimes": regimes,
        "score": score,
        "error": None,
        "folds": len(trades),
    }


def evaluate_holdout(
    df: pd.DataFrame,
    market: Market,
    cfg: dict,
    name: str | None,
    risk_pct: float,
    holdout_bars: int,
    params: dict | None = None,
) -> dict | None:
    if not name or holdout_bars <= 0 or len(df) <= holdout_bars:
        return None
    warmup = max(80, market.slow_ema, market.atr_period + 5)
    holdout_start = len(df) - holdout_bars
    slice_start = max(0, holdout_start - warmup)
    sample = df.iloc[slice_start:]
    active_start = holdout_start - slice_start
    cfg_use, market_use = (cfg, market)
    if params:
        cfg_use, market_use = apply_params(cfg, market, name, params)
    stats = simulate(
        sample,
        market_use,
        cfg_use,
        STRATEGIES[name],
        risk_pct,
        10_000.0,
        active_start=active_start,
    )
    stats["name"] = name
    stats["params"] = params or {}
    stats["bars"] = holdout_bars
    stats["start"] = str(df.index[holdout_start])
    stats["end"] = str(df.index[-1])
    return stats

def apply_winners(cfg_path: Path, winners: dict[str, str], tuned: dict | None = None) -> None:
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg.setdefault("strategy", {})
    cfg["strategy"]["name"] = "router"
    current = dict(cfg["strategy"].get("per_market") or {})
    current.update(winners)
    cfg["strategy"]["per_market"] = current
    if tuned:
        current_tuned = dict(cfg["strategy"].get("tuned") or {})
        current_tuned.update(tuned)
        cfg["strategy"]["tuned"] = current_tuned
    cfg["strategy"]["walk_applied"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward analysis on Capital.com history")
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--only", nargs="*", choices=list(MARKETS))
    parser.add_argument("--strategy", nargs="*", default=list(STRATEGIES))
    parser.add_argument("--bars", type=int, default=1000)
    parser.add_argument(
        "--method",
        choices=["in_sample", "rolling", "anchored"],
        default="rolling",
        help="in_sample=same bars train+score; rolling=fixed train window; anchored=expanding train",
    )
    parser.add_argument("--train", type=int, default=400, help="IS train bars per fold")
    parser.add_argument("--test", type=int, default=100, help="OOS test bars per fold")
    parser.add_argument("--embargo", type=int, default=5, help="gap bars between train and test")
    parser.add_argument("--holdout", type=int, default=200, help="final untouched bars reserved for validation")
    parser.add_argument("--apply", action="store_true", help="write OOS winners into config.yaml")
    parser.add_argument("--optuna", action="store_true", help="TPE search knobs on each TRAIN fold only")
    parser.add_argument("--trials", type=int, default=20)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    cfg = load_cfg()
    broker = make_broker(cfg, args.mode)
    risk_pct = float(cfg["risk"]["risk_per_trade_pct"]) / 100.0
    res = {"1m": "MINUTE", "5m": "MINUTE_5", "15m": "MINUTE_15"}.get(cfg.get("timeframe", "1m"), "MINUTE")
    names = [n for n in args.strategy if n not in ("router", "combo", "auto")]
    markets = [MARKETS[k] for k in (args.only or MARKETS)]

    print(f"method={args.method} train={args.train} test={args.test} embargo={args.embargo} holdout={args.holdout} bars={args.bars} optuna={args.optuna} trials={args.trials}")
    print(f"{'market':14} {'strat':14} {'n':>4} {'win%':>6} {'pnl':>10} {'dd':>8} {'pf':>5} {'exp':>8} {'hold':>6}  score")
    report = {
        "when": datetime.now(timezone.utc).isoformat(),
        "method": args.method,
        "train": args.train,
        "test": args.test,
        "embargo": args.embargo,
        "holdout": args.holdout,
        "bars": args.bars,
        "optuna": args.optuna,
        "trials": args.trials,
        "markets": {},
    }
    winners: dict[str, str] = {}
    tuned: dict[str, dict] = {}

    for market in markets:
        try:
            epic = market.epic or broker.resolve_epic(market.search_term or market.name)
            if epic not in market.live_aliases:
                market.live_aliases.append(epic)
            apply_broker_details(market, broker.market_details(epic))
            df = fetch_history(broker, epic, res, args.bars)
        except Exception as exc:
            print(f"{market.key:14} DATA {exc}")
            continue
        print(f"# {market.name} {epic} bars={len(df)} {df.index[0]} -> {df.index[-1]}")
        minimum_dev = max(args.train + args.test + args.embargo, 200)
        holdout_bars = min(max(0, args.holdout), max(0, len(df) - minimum_dev))
        dev_df = df.iloc[:-holdout_bars] if holdout_bars else df
        fold_idx = folds(len(dev_df), args.train, args.test, args.embargo, args.method)

        if args.method == "in_sample" or not fold_idx:
            if args.method != "in_sample":
                print("  not enough bars for folds — falling back to in_sample")
            rows = []
            last_params = {}
            for name in names:
                cfg_use, m_use, params = cfg, market, {}
                if args.optuna:
                    params, _ = optuna_search(dev_df, market, cfg, name, risk_pct, args.trials)
                    cfg_use, m_use = apply_params(cfg, market, name, params)
                    last_params[name] = params
                stats = simulate(dev_df, m_use, cfg_use, STRATEGIES[name], risk_pct, 10_000.0)
                stats["name"] = name
                stats["params"] = params
                rows.append(stats)
                if stats.get("error"):
                    print(f"{market.key:14} {name:14} ERR {stats['error']}")
                    continue
                sc = stats["score"] if stats["score"] != float("-inf") else float("nan")
                print(
                    f"{market.key:14} {name:14} {stats['trades']:4d} {stats['win_rate']:5.1f}% "
                    f"{stats['pnl']:10.2f} {stats['max_dd']:8.2f} {stats['pf']:5.2f} "
                    f"{stats['expectancy']:8.2f} {stats['avg_hold_bars']:6.1f}  {sc:7.2f}"
                )
            best = pick_winner(rows)
            if best:
                winners[market.key] = best["name"]
                if last_params.get(best["name"]):
                    tuned[market.key] = {"name": best["name"], **last_params[best["name"]]}
                print(f"  -> best {market.key}: {best['name']}  pnl={best['pnl']:.2f}  n={best['trades']} params={last_params.get(best['name'])}")
            holdout_params = {}
            if winners.get(market.key) and tuned.get(market.key, {}).get("name") == winners.get(market.key):
                holdout_params = {k: v for k, v in tuned[market.key].items() if k != "name"}
            holdout = evaluate_holdout(
                df,
                market,
                cfg,
                winners.get(market.key),
                risk_pct,
                holdout_bars,
                holdout_params,
            )
            if holdout:
                print(
                    f"  HOLDOUT {market.key}: {holdout['name']} n={holdout['trades']} "
                    f"pnl={holdout['pnl']:.2f} dd={holdout['max_dd']:.2f} "
                    f"pf={holdout['pf']:.2f} exp={holdout['expectancy']:.2f}"
                )
            report["markets"][market.key] = {
                "bars": len(df),
                "development_bars": len(dev_df),
                "holdout_bars": holdout_bars,
                "winner": winners.get(market.key),
                "rows": rows,
                "folds": [],
                "tuned": tuned.get(market.key),
                "holdout": holdout,
            }
            continue

        print(f"  folds={len(fold_idx)}")
        oos_parts: dict[str, list] = {n: [] for n in names}
        selected = []
        fold_picks: list[str | None] = []
        path_pnl = 0.0
        for fi, (tr0, tr1, te0, te1) in enumerate(fold_idx, 1):
            train_df = dev_df.iloc[tr0:tr1]
            test_start = max(0, te0 - 80)
            test_df = dev_df.iloc[test_start:te1]  # warmup lookback before test
            is_rows = []
            fold_params = {}
            for name in names:
                cfg_use, m_use, params = cfg, market, {}
                if args.optuna:
                    params, _ = optuna_search(train_df, market, cfg, name, risk_pct, args.trials)
                    cfg_use, m_use = apply_params(cfg, market, name, params)
                    fold_params[name] = params
                st = simulate(train_df, m_use, cfg_use, STRATEGIES[name], risk_pct, 10_000.0)
                st["name"] = name
                st["params"] = params
                is_rows.append(st)
            pick = pick_winner(is_rows)
            chosen = pick["name"] if pick else None
            fold_picks.append(chosen)
            if chosen:
                selected.append(chosen)
            for name in names:
                params = fold_params.get(name) or {}
                cfg_use, m_use = apply_params(cfg, market, name, params) if params else (cfg, market)
                st = simulate(
                    test_df,
                    m_use,
                    cfg_use,
                    STRATEGIES[name],
                    risk_pct,
                    10_000.0,
                    active_start=te0 - test_start,
                )
                st["name"] = name
                st["params"] = params
                oos_parts[name].append(st)
            if pick and chosen:
                path_pnl += next((x["pnl"] for x in oos_parts[chosen][-1:]), 0)
            print(
                f"  fold {fi}/{len(fold_idx)} IS[{tr0}:{tr1}] OOS[{te0}:{te1}] pick={chosen or 'none'}"
                + (f" IS_score={pick['score']:.2f}" if pick else "")
            )

        rows = [merge_stats(oos_parts[n], n) for n in names]
        rows.sort(key=lambda r: (r["score"], r["pnl"]), reverse=True)
        for stats in rows:
            sc = stats["score"] if stats["score"] != float("-inf") else float("nan")
            print(
                f"{market.key:14} {stats['name']:14} {stats['trades']:4d} {stats['win_rate']:5.1f}% "
                f"{stats['pnl']:10.2f} {stats['max_dd']:8.2f} {stats['pf']:5.2f} "
                f"{stats['expectancy']:8.2f} {stats['avg_hold_bars']:6.1f}  {sc:7.2f}"
            )
        # winner = most selected on IS, break ties with OOS score
        freq: dict[str, int] = {}
        for n in selected:
            freq[n] = freq.get(n, 0) + 1
        by_freq = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)
        best = pick_winner(rows)
        if by_freq and by_freq[0][1] >= 2:
            winners[market.key] = by_freq[0][0]
        elif best:
            winners[market.key] = best["name"]

        # Fold-local parameters are not portable. If a strategy wins OOS,
        # tune that strategy once on the whole development sample, then test
        # those parameters only on the untouched holdout.
        winner_name = winners.get(market.key)
        if args.optuna and winner_name:
            final_params, _ = optuna_search(
                dev_df, market, cfg, winner_name, risk_pct, args.trials
            )
            tuned[market.key] = {"name": winner_name, **final_params}
        else:
            tuned.pop(market.key, None)

        print(f"  selection freq {freq}")
        print(f"  -> apply {market.key}: {winners.get(market.key)}")

        holdout_params = {}
        if winners.get(market.key) and tuned.get(market.key, {}).get("name") == winners.get(market.key):
            holdout_params = {k: v for k, v in tuned[market.key].items() if k != "name"}
        holdout = evaluate_holdout(
            df,
            market,
            cfg,
            winners.get(market.key),
            risk_pct,
            holdout_bars,
            holdout_params,
        )
        if holdout:
            print(
                f"  HOLDOUT {market.key}: {holdout['name']} n={holdout['trades']} "
                f"pnl={holdout['pnl']:.2f} dd={holdout['max_dd']:.2f} "
                f"pf={holdout['pf']:.2f} exp={holdout['expectancy']:.2f}"
            )

        report["markets"][market.key] = {
            "bars": len(df),
            "development_bars": len(dev_df),
            "holdout_bars": holdout_bars,
            "winner": winners.get(market.key),
            "rows": rows,
            "folds": [
                {"train": [a, b], "test": [c, d], "pick": fold_picks[i]}
                for i, (a, b, c, d) in enumerate(fold_idx)
            ],
            "selection_freq": freq,
            "tuned": tuned.get(market.key),
            "holdout": holdout,
        }

    approved: dict[str, str] = {}
    approved_tuned: dict[str, dict] = {}
    for key, name in winners.items():
        block = report["markets"].get(key) or {}
        ok, why = holdout_passes(block.get("holdout"), cfg)
        block["holdout_pass"] = ok
        block["holdout_gate_reason"] = why
        if ok:
            approved[key] = name
            if tuned.get(key):
                approved_tuned[key] = tuned[key]
        else:
            print(f"  HOLDOUT REJECT {key}: {name} ({why})")

    WALK_PATH.parent.mkdir(exist_ok=True)
    WALK_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"saved {WALK_PATH}")
    if args.apply:
        if not approved:
            print("no holdout-approved winners to apply; keeping current config")
            return
        apply_winners(ROOT / "config.yaml", approved, approved_tuned)
        print("wrote holdout-approved strategy.per_market:", approved)
        if approved_tuned:
            print("wrote holdout-approved strategy.tuned:", approved_tuned)


if __name__ == "__main__":
    main()
