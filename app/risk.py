from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from instruments import Market

STATE_PATH = Path(__file__).resolve().parents[1] / "logs" / "risk_state.json"


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    lots: float = 0.0


class RiskManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.day = date.today()
        self.realized_today = 0.0
        self.start_equity = None
        self.halted = False
        self.losses_in_a_row = 0
        self.cooldown_left = 0
        self.reset_note = ""
        self._load()

    def _profile(self, equity: float | None = None) -> dict:
        r = self.cfg["risk"]
        base = {
            "mode": "standard",
            "trading_enabled": True,
            "risk_per_trade_pct": float(r.get("risk_per_trade_pct", 0) or 0),
            "max_portfolio_allocation_pct": float(r.get("max_portfolio_allocation_pct", 100) or 0),
            "max_open_positions": int(r.get("max_open_positions", 12)),
            "max_positions_per_market": int(r.get("max_positions_per_market", 4)),
            "max_index_positions": int(r.get("max_index_positions", 8)),
            "min_equity": None,
            "target_equity": None,
            "max_min_lot_risk_pct": None,
        }
        bootstrap = r.get("bootstrap") or {}
        if not bool(bootstrap.get("enabled", False)) or equity is None:
            return base

        min_equity = float(bootstrap.get("min_equity", 50) or 50)
        target_equity = float(bootstrap.get("target_equity", 200) or 200)
        if target_equity <= min_equity:
            target_equity = min_equity

        if float(equity) < min_equity:
            return {
                **base,
                "mode": "below_minimum",
                "trading_enabled": False,
                "min_equity": min_equity,
                "target_equity": target_equity,
            }

        if float(equity) < target_equity:
            return {
                **base,
                "mode": "bootstrap",
                "risk_per_trade_pct": float(bootstrap.get("risk_per_trade_pct", base["risk_per_trade_pct"]) or 0),
                "max_portfolio_allocation_pct": float(
                    bootstrap.get("max_portfolio_allocation_pct", base["max_portfolio_allocation_pct"]) or 0
                ),
                "max_open_positions": int(bootstrap.get("max_open_positions", base["max_open_positions"])),
                "max_positions_per_market": int(
                    bootstrap.get("max_positions_per_market", base["max_positions_per_market"])
                ),
                "max_index_positions": int(bootstrap.get("max_index_positions", base["max_index_positions"])),
                "min_equity": min_equity,
                "target_equity": target_equity,
                "max_min_lot_risk_pct": float(bootstrap.get("max_min_lot_risk_pct", 0) or 0),
            }

        base["min_equity"] = min_equity
        base["target_equity"] = target_equity
        return base

    def snapshot(self, equity: float | None = None) -> dict:
        r = self.cfg["risk"]
        profile = self._profile(equity)
        daily_loss_enabled = bool(r.get("daily_loss_enabled", True))
        limit_raw = r.get("max_daily_loss_pct")
        limit_pct = float(limit_raw) if daily_loss_enabled and limit_raw is not None else None
        return {
            "day": str(self.day),
            "halted": self.halted if daily_loss_enabled else False,
            "daily_loss_enabled": daily_loss_enabled,
            "realized_today": self.realized_today,
            "start_equity": self.start_equity,
            "losses_in_a_row": self.losses_in_a_row,
            "cooldown_left": self.cooldown_left,
            "limit_pct": limit_pct,
            "per_trade": profile["risk_per_trade_pct"],
            "max_portfolio_allocation_pct": profile["max_portfolio_allocation_pct"],
            "max_open": profile["max_open_positions"],
            "max_per_market": profile["max_positions_per_market"],
            "max_index": profile["max_index_positions"],
            "profile": profile["mode"],
            "bootstrap_min_equity": profile["min_equity"],
            "bootstrap_target_equity": profile["target_equity"],
            "max_min_lot_risk_pct": profile["max_min_lot_risk_pct"],
            "reset_note": self.reset_note,
        }

    def _load(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        try:
            self.day = date.fromisoformat(raw["day"]) if raw.get("day") else date.today()
        except ValueError:
            self.day = date.today()
        self.realized_today = float(raw.get("realized_today") or 0)
        se = raw.get("start_equity")
        self.start_equity = float(se) if se is not None else None
        self.halted = bool(raw.get("halted"))
        self.losses_in_a_row = int(raw.get("losses_in_a_row") or 0)
        self.cooldown_left = int(raw.get("cooldown_left") or 0)
        self.reset_note = str(raw.get("reset_note") or "")

    def _save(self) -> None:
        STATE_PATH.parent.mkdir(exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(
                {
                    "day": str(self.day),
                    "realized_today": self.realized_today,
                    "start_equity": self.start_equity,
                    "halted": self.halted,
                    "losses_in_a_row": self.losses_in_a_row,
                    "cooldown_left": self.cooldown_left,
                    "reset_note": self.reset_note,
                    "updated": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def reset_if_new_day(self, equity: float) -> None:
        self._load()
        today = date.today()
        if self.day != today:
            self.day = today
            self.realized_today = 0.0
            self.start_equity = equity
            self.halted = False
            self.losses_in_a_row = 0
            self.cooldown_left = 0
            self.reset_note = "auto new calendar day"
            self._save()
        if self.start_equity is None:
            self.start_equity = equity
            self._save()

    def reset_day(self, equity: float | None = None, why: str = "manual") -> dict:
        self._load()
        self.day = date.today()
        self.realized_today = 0.0
        if equity is not None:
            self.start_equity = equity
        self.halted = False
        self.losses_in_a_row = 0
        self.cooldown_left = 0
        self.reset_note = f"{why} @ {datetime.now(timezone.utc).strftime('%H:%M:%SZ')}"
        self._save()
        return self.snapshot()

    def record_realized(self, pnl: float) -> None:
        self._load()
        self.realized_today += pnl
        if pnl < 0:
            self.losses_in_a_row += 1
            self.cooldown_left = int(self.cfg["risk"].get("cooldown_bars_after_loss", 0))
        elif pnl > 0:
            self.losses_in_a_row = 0
        self._save()

    def check_account(self, equity: float, open_positions: int, open_index: int = 0) -> RiskDecision:
        self.reset_if_new_day(equity)
        r = self.cfg["risk"]
        profile = self._profile(equity)
        if not profile["trading_enabled"]:
            return RiskDecision(False, f"equity below bootstrap minimum ({profile['min_equity']:.2f})")
        daily_loss_enabled = bool(r.get("daily_loss_enabled", True))

        if not daily_loss_enabled and self.halted:
            self.halted = False
            self._save()

        if daily_loss_enabled:
            start = self.start_equity or equity
            limit_pct = float(r.get("max_daily_loss_pct", 0) or 0)
            daily_loss_limit = start * (limit_pct / 100.0)
            if self.halted:
                return RiskDecision(False, "halted after daily loss limit — Reset daily loss to resume")
            if daily_loss_limit > 0:
                equity_dd = start - equity
                if self.realized_today <= -daily_loss_limit or equity_dd >= daily_loss_limit:
                    self.halted = True
                    self._save()
                    return RiskDecision(
                        False,
                        f"daily loss limit hit (realized={self.realized_today:.2f} equity_dd={equity_dd:.2f})",
                    )

        if open_positions >= int(profile["max_open_positions"]):
            return RiskDecision(False, f"max open positions ({profile['max_open_positions']})")
        if self.cooldown_left > 0:
            left = self.cooldown_left
            self.cooldown_left -= 1
            self._save()
            return RiskDecision(False, f"cooldown after loss ({left} scans left)")
        return RiskDecision(True, "ok")

    def allow_new(
        self,
        market: Market,
        open_index: int,
        open_market: int = 0,
        open_positions: int = 0,
        equity: float | None = None,
    ) -> RiskDecision:
        profile = self._profile(equity)
        if not profile["trading_enabled"]:
            return RiskDecision(False, f"equity below bootstrap minimum ({profile['min_equity']:.2f})")

        total_cap = int(profile["max_open_positions"])
        if open_positions >= total_cap:
            return RiskDecision(False, f"max open positions ({total_cap})")

        market_cap = int(profile["max_positions_per_market"])
        if open_market >= market_cap:
            return RiskDecision(False, f"max {market.name} positions ({market_cap})")

        index_cap = int(profile["max_index_positions"])
        if market.group == "index" and open_index >= index_cap:
            return RiskDecision(False, f"index correlation cap ({index_cap}) — gold still allowed")

        return RiskDecision(True, "ok")

    def margin_per_lot(self, price: float, market: Market) -> float:
        """Estimated broker margin for one deal-size unit at the given price."""
        price = abs(float(price))
        if price <= 0:
            return 0.0

        factor = market.margin_factor
        unit = (market.margin_factor_unit or "").upper()
        if factor is not None and factor > 0 and unit == "PERCENTAGE":
            return price * max(float(market.contract_size), 1e-12) * (float(factor) / 100.0)

        leverage = float((self.cfg.get("account") or {}).get("leverage", 1) or 1)
        leverage = max(1.0, leverage)
        return price * max(float(market.point_value), 1e-12) / leverage

    def estimate_margin(self, price: float, lots: float, market: Market) -> float:
        return abs(float(lots)) * self.margin_per_lot(price, market)

    def size_lots(
        self,
        equity: float,
        stop_distance: float,
        market: Market,
        *,
        price: float | None = None,
        allocated_margin: float = 0.0,
    ) -> RiskDecision:
        profile = self._profile(equity)
        if not profile["trading_enabled"]:
            return RiskDecision(False, f"equity below bootstrap minimum ({profile['min_equity']:.2f})")
        if equity <= 0 or stop_distance <= 0 or market.point_value <= 0:
            return RiskDecision(False, "invalid equity/stop/point value")

        risk_cash = equity * (float(profile["risk_per_trade_pct"]) / 100.0)
        value_per_price_unit = (
            max(float(market.point_value), 1e-12)
            * max(float(getattr(market, "contract_size", 1.0) or 1.0), 1e-12)
        )
        raw = risk_cash / (stop_distance * value_per_price_unit)
        raw = min(market.max_lot, raw)

        allocation_pct = float(profile["max_portfolio_allocation_pct"])
        if allocation_pct <= 0:
            return RiskDecision(False, "portfolio allocation cap is 0%")

        allocation_lots = float("inf")
        if allocation_pct < 100:
            if price is None or price <= 0:
                return RiskDecision(False, "valid price required for portfolio allocation cap")
            max_margin = equity * (allocation_pct / 100.0)
            remaining_margin = max_margin - max(0.0, float(allocated_margin))
            if remaining_margin <= 0:
                return RiskDecision(False, f"portfolio margin allocation cap ({allocation_pct:g}%) reached")
            margin_per_lot = self.margin_per_lot(price, market)
            if margin_per_lot <= 0:
                return RiskDecision(False, "could not estimate broker margin")
            allocation_lots = remaining_margin / margin_per_lot
            raw = min(raw, allocation_lots)

        step = market.lot_step
        lots = math.floor((raw + 1e-12) / step) * step
        lots = min(lots, market.max_lot)
        lots = round(lots, 8)

        if lots < market.min_lot:
            max_min_lot_risk_pct = float(profile.get("max_min_lot_risk_pct") or 0)
            if profile["mode"] == "bootstrap" and max_min_lot_risk_pct > 0:
                min_lot = float(market.min_lot)
                min_lot_risk = stop_distance * value_per_price_unit * min_lot
                max_min_lot_risk = equity * (max_min_lot_risk_pct / 100.0)
                if min_lot <= allocation_lots + 1e-12 and min_lot_risk <= max_min_lot_risk + 1e-12:
                    return RiskDecision(True, "bootstrap minimum lot", lots=round(min_lot, 8))
                if min_lot_risk > max_min_lot_risk:
                    return RiskDecision(
                        False,
                        f"minimum lot risk exceeds bootstrap cap ({max_min_lot_risk_pct:g}%)",
                    )
            if allocation_pct < 100:
                return RiskDecision(False, f"remaining portfolio margin below minimum lot ({allocation_pct:g}% cap)")
            return RiskDecision(False, "size below min lot")

        return RiskDecision(True, "sized", lots=lots)

    def spread_ok(self, bid: float, ask: float, market: Market) -> RiskDecision:
        if bid <= 0 or ask <= 0 or ask < bid:
            return RiskDecision(False, "bad quotes")
        spread = ask - bid
        if spread > market.max_spread:
            return RiskDecision(
                False,
                f"spread {spread:.{market.digits}f} > max {market.max_spread:.{market.digits}f} on {market.name}",
            )
        return RiskDecision(True, "spread ok")
