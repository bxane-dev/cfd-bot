#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time

from dotenv import load_dotenv

from main import (
    ROOT,
    capital_map,
    enabled_markets,
    ensure_logs,
    load_cfg,
    load_state,
    make_broker,
    resolved_markets,
    run_once,
    sync_market_rules,
)
from memory import last, last_tune, remember
from news import fetch_desk_news
from web_app import start_background
from risk import RiskManager

WALK_PATH = ROOT / "logs" / "walk.json"
DEFAULT_STRATS = [
    "orb",
    "ema_pullback",
    "macd_trend",
    "rsi_reversion",
    "supertrend",
    "bollinger",
    "vwap",
    "donchian",
]


def require_login() -> None:
    env = ROOT / ".env"
    if not env.exists():
        raise RuntimeError("Missing .env — run fill_capital.bat")
    text = env.read_text(encoding="utf-8", errors="ignore")
    for key in ("CAPITAL_EMAIL", "CAPITAL_API_KEY", "CAPITAL_API_PASSWORD"):
        if not any(line.startswith(key + "=") and line.split("=", 1)[1].strip() for line in text.splitlines()):
            raise RuntimeError(f"{key} empty in .env")


def walk_age_hours() -> float | None:
    if not WALK_PATH.exists():
        return None
    return (time.time() - WALK_PATH.stat().st_mtime) / 3600.0


def should_tune(cfg: dict, force: bool, skip: bool) -> bool:
    if skip:
        return False
    if force:
        return True
    hours = float((cfg.get("auto") or {}).get("retune_hours", 12))
    age = walk_age_hours()
    if age is None:
        print("no walk.json — first tune")
        return True
    if age >= hours:
        print(f"walk.json {age:.1f}h old — retune")
        return True
    print(f"walk.json {age:.1f}h old — keep")
    return False


def run_tune(cfg: dict, mode: str) -> int:
    auto = cfg.get("auto") or {}
    research = cfg.get("research") or {}
    strats = auto.get("strategies") or DEFAULT_STRATS
    cmd = [
        sys.executable,
        "-m",
        "research.walk",
        "--mode", mode,
        "--method", str(auto.get("method", "rolling")),
        "--bars", str(int(auto.get("bars", 1000))),
        "--train", str(int(auto.get("train", 400))),
        "--test", str(int(auto.get("test", 100))),
        "--embargo", str(int(auto.get("embargo", 5))),
        "--holdout", str(int(research.get("holdout_bars", 200))),
        "--apply",
        "--strategy", *list(strats),
    ]
    if auto.get("optuna", True):
        cmd += ["--optuna", "--trials", str(int(auto.get("trials", 15)))]
    remember("tune", "start walk-forward", how=" ".join(cmd[1:]), extra={"mode": mode})
    print("tune:", " ".join(cmd))
    code = subprocess.call(cmd, cwd=str(ROOT))
    remember("tune", f"walk finished code={code}", how="walk.py", extra={"code": code})
    return code


def pull_news(cfg: dict) -> None:
    try:
        payload = fetch_desk_news(news_cfg=cfg.get("news") or {})
    except Exception as exc:
        remember("news", f"fetch failed {exc}", how="capital_google")
        return
    hot = payload.get("hot") or []
    n = len(payload.get("headlines") or [])
    remember(
        "news",
        f"{n} headlines, hot={len(hot)}",
        how="capital_google",
        extra={"hot": hot[:5], "sample": [h.get("title") for h in (payload.get("headlines") or [])[:5]]},
    )
    if hot:
        print("NEWS HOT:")
        for h in hot[:5]:
            print(" ", h.get("title"))
    else:
        print(f"news clear ({n} headlines)")


def main() -> None:
    load_dotenv(ROOT / ".env")
    cfg = load_cfg()
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["demo", "live"], default=os.getenv("MODE") or cfg.get("mode", "demo"))
    parser.add_argument("--skip-tune", action="store_true", help="disable startup and periodic tuning")
    parser.add_argument("--force-tune", action="store_true", help="run tuning before trading starts")
    parser.add_argument("--tune-on-start", action="store_true", help="run tuning on startup when due")
    parser.add_argument("--tune-only", action="store_true")
    parser.add_argument("--no-web", action="store_true", help="do not start the local dashboard")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    require_login()
    os.environ["CFD_AUTO"] = "1"
    os.environ["MODE"] = args.mode

    remember("start", f"auto {args.mode}", how="auto.py")
    prev = last_tune()
    if prev:
        print("last tune:", prev.get("when"), prev.get("what"))
    for row in last(n=5):
        print(f"  mem {row.get('when','')[:19]} {row.get('kind')} {row.get('what')}")

    auto_cfg = cfg.get("auto") or {}

    if args.tune_only:
        print("tune-only mode")
        code = run_tune(cfg, args.mode)
        if code != 0:
            raise RuntimeError(f"tuning failed with exit code {code}")
        remember("tune", "tune-only exit", how="auto.py")
        return

    tune_on_start = (
        not args.skip_tune
        and (
            args.force_tune
            or args.tune_on_start
            or bool(auto_cfg.get("tune_on_start", False))
        )
    )
    if tune_on_start and should_tune(cfg, args.force_tune, False):
        code = run_tune(cfg, args.mode)
        if code != 0:
            print(f"tune exited {code} — using existing config")
        cfg = load_cfg()
    elif not args.skip_tune:
        print("startup tuning disabled — trading starts immediately")

    cfg_now = load_cfg()
    markets = enabled_markets(cfg_now, args.mode)
    ensure_logs()

    print(f"starting Capital.com {args.mode.upper()} session...")
    broker = make_broker(cfg_now, args.mode, markets)
    live_map = capital_map(markets, broker)
    display_markets = list(markets)
    markets = resolved_markets(markets, live_map)
    sync_market_rules(markets, broker, live_map)
    risk = RiskManager(cfg_now)
    state = load_state()

    loop_event = threading.Event()
    loop_event.set()
    run_lock = threading.RLock()
    desk = None
    if not args.no_web:
        from desk import Desk, install_shared_desk

        desk = Desk(
            cfg=cfg_now,
            mode=args.mode,
            markets=markets,
            display_markets=display_markets,
            broker=broker,
            live_map=live_map,
            risk=risk,
            state=state,
            loop_event=loop_event,
            lock=run_lock,
        )
        install_shared_desk(desk)
        start_background(open_browser=True)
    else:
        print("web dashboard disabled")

    pull_news(cfg_now)

    last_tune_ts = time.time()
    last_news_ts = time.time()
    periodic_retune = bool((cfg_now.get("auto") or {}).get("periodic_retune", False))
    retune = float((cfg_now.get("auto") or {}).get("retune_hours", 12)) * 3600
    news_cfg = cfg_now.get("news") or {}
    news_every = float(
        news_cfg.get(
            "background_refresh_seconds",
            max(300.0, float(news_cfg.get("refresh_seconds", 60) or 60)),
        )
    )

    print("trading loop ready. Ctrl+C stops.")
    print("dashboard Start/Stop now controls this same loop.")

    while True:
        if not loop_event.is_set():
            time.sleep(0.25)
            continue

        if (
            periodic_retune
            and not args.skip_tune
            and retune > 0
            and time.time() - last_tune_ts >= retune
        ):
            print("periodic retune starting...")
            run_tune(load_cfg(), args.mode)
            cfg_now = load_cfg()
            markets = enabled_markets(cfg_now, args.mode)
            live_map = capital_map(markets, broker)
            markets = resolved_markets(markets, live_map)
            sync_market_rules(markets, broker, live_map)
            risk = RiskManager(cfg_now)
            if desk is not None:
                desk.cfg = cfg_now
                desk.markets = markets
                desk.live_map = live_map
                desk.risk = risk
            last_tune_ts = time.time()
            retune = float((cfg_now.get("auto") or {}).get("retune_hours", 12)) * 3600
            news_cfg = cfg_now.get("news") or {}
            news_every = float(
                news_cfg.get(
                    "background_refresh_seconds",
                    max(300.0, float(news_cfg.get("refresh_seconds", 60) or 60)),
                )
            )

        if time.time() - last_news_ts >= news_every:
            pull_news(cfg_now)
            last_news_ts = time.time()

        try:
            with run_lock:
                run_once(cfg_now, args.mode, broker, risk, state, markets, live_map)
        except KeyboardInterrupt:
            remember("stop", "keyboard", how="auto.py")
            print("stopped")
            return
        except Exception as exc:
            remember("error", str(exc), how="run_once")
            print(f"loop error: {exc}")

        if args.once:
            return
        time.sleep(max(1.0, float(cfg_now.get("poll_seconds", 5))))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("stopped")
        sys.exit(0)
    except RuntimeError as exc:
        print(f"startup error: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"unexpected startup error: {type(exc).__name__}: {exc}")
        raise
