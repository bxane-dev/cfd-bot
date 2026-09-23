from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

CAPITAL_EPICS = {
    "germany40": "DE40",
    "ustech100": "US100",
    "wallstreet30": "US30",
    "gold": "GOLD",
    "DE40": "DE40",
    "US100": "US100",
    "US30": "US30",
    "GOLD": "GOLD",
}


@dataclass
class Market:
    key: str
    name: str
    epic: str
    live_aliases: list[str]
    point_value: float
    digits: int
    typical_spread: float
    max_spread: float
    session: str
    session_windows: list[tuple[str, str]]
    fast_ema: int
    slow_ema: int
    atr_period: int
    sl_atr: float
    tp_atr: float
    lot_step: float
    min_lot: float
    max_lot: float
    group: str
    search_term: str | None = None
    contract_size: float = 1.0
    margin_factor: float | None = None
    margin_factor_unit: str = ""
    broker_rules: bool = False
    opening_hours: dict[str, list[str]] = field(default_factory=dict)
    opening_zone: str = "UTC"


MARKETS: dict[str, Market] = {
    "germany40": Market(
        key="germany40",
        name="Germany 40",
        epic="DE40",
        live_aliases=["DE40", "GER40", "germany40"],
        point_value=1.0,
        digits=1,
        typical_spread=1.6,
        max_spread=4.0,
        session="Europe cash + US overlap",
        session_windows=[("08:00", "21:30")],
        fast_ema=12,
        slow_ema=48,
        atr_period=14,
        sl_atr=1.6,
        tp_atr=2.2,
        lot_step=0.01,
        min_lot=0.01,
        max_lot=1.0,
        group="index",
    ),
    "ustech100": Market(
        key="ustech100",
        name="US Tech 100",
        epic="US100",
        live_aliases=["US100", "USTEC", "ustech100"],
        point_value=1.0,
        digits=1,
        typical_spread=2.0,
        max_spread=6.0,
        session="US cash",
        session_windows=[("14:30", "22:15")],
        fast_ema=8,
        slow_ema=34,
        atr_period=14,
        sl_atr=1.8,
        tp_atr=2.4,
        lot_step=0.01,
        min_lot=0.01,
        max_lot=1.0,
        group="index",
    ),
    "wallstreet30": Market(
        key="wallstreet30",
        name="Wall Street 30",
        epic="US30",
        live_aliases=["US30", "DJ30", "wallstreet30"],
        point_value=1.0,
        digits=1,
        typical_spread=2.4,
        max_spread=8.0,
        session="US cash",
        session_windows=[("14:30", "22:15")],
        fast_ema=12,
        slow_ema=48,
        atr_period=14,
        sl_atr=1.6,
        tp_atr=2.2,
        lot_step=0.01,
        min_lot=0.01,
        max_lot=1.0,
        group="index",
    ),
    "gold": Market(
        key="gold",
        name="Gold",
        epic="GOLD",
        live_aliases=["GOLD", "XAUUSD", "gold"],
        point_value=1.0,
        digits=2,
        typical_spread=0.35,
        max_spread=1.20,
        session="weekday almost 24h",
        session_windows=[("01:15", "21:45")],
        fast_ema=10,
        slow_ema=40,
        atr_period=14,
        sl_atr=1.7,
        tp_atr=2.3,
        lot_step=0.01,
        min_lot=0.01,
        max_lot=2.0,
        group="metal",
    ),
    "us500": Market(
        key="us500", name="US 500", epic="", live_aliases=["US500", "us500"],
        point_value=1.0, digits=1, typical_spread=0.8, max_spread=4.0,
        session="US cash", session_windows=[("14:30", "22:15")],
        fast_ema=12, slow_ema=48, atr_period=14, sl_atr=1.6, tp_atr=2.2,
        lot_step=0.01, min_lot=0.01, max_lot=1.0, group="index", search_term="US500",
    ),
    "crudeoil": Market(
        key="crudeoil", name="US Crude Oil", epic="", live_aliases=["crudeoil"],
        point_value=1.0, digits=2, typical_spread=0.05, max_spread=0.30,
        session="weekday energy session", session_windows=[("01:00", "22:45")],
        fast_ema=10, slow_ema=40, atr_period=14, sl_atr=1.8, tp_atr=2.5,
        lot_step=0.01, min_lot=0.01, max_lot=2.0, group="energy", search_term="Oil - Crude",
    ),
    "eurusd": Market(
        key="eurusd", name="EUR/USD", epic="", live_aliases=["EURUSD", "eurusd"],
        point_value=1.0, digits=5, typical_spread=0.00010, max_spread=0.00080,
        session="forex weekday", session_windows=[("00:05", "23:55")],
        fast_ema=10, slow_ema=40, atr_period=14, sl_atr=1.5, tp_atr=2.1,
        lot_step=0.01, min_lot=0.01, max_lot=2.0, group="forex", search_term="EUR/USD",
    ),
    "gbpusd": Market(
        key="gbpusd", name="GBP/USD", epic="", live_aliases=["GBPUSD", "gbpusd"],
        point_value=1.0, digits=5, typical_spread=0.00015, max_spread=0.00100,
        session="forex weekday", session_windows=[("00:05", "23:55")],
        fast_ema=10, slow_ema=45, atr_period=14, sl_atr=1.6, tp_atr=2.2,
        lot_step=0.01, min_lot=0.01, max_lot=2.0, group="forex", search_term="GBP/USD",
    ),
    "usdchf": Market(
        key="usdchf", name="USD/CHF", epic="", live_aliases=["USDCHF", "usdchf"],
        point_value=1.0, digits=5, typical_spread=0.00016, max_spread=0.00100,
        session="forex weekday", session_windows=[("00:05", "23:55")],
        fast_ema=10, slow_ema=40, atr_period=14, sl_atr=1.5, tp_atr=2.0,
        lot_step=0.01, min_lot=0.01, max_lot=2.0, group="forex", search_term="USD/CHF",
    ),
    "naturalgas": Market(
        key="naturalgas", name="Natural Gas", epic="", live_aliases=["NATURALGAS", "naturalgas"],
        point_value=1.0, digits=3, typical_spread=0.010, max_spread=0.080,
        session="weekday energy session", session_windows=[("01:00", "22:45")],
        fast_ema=10, slow_ema=40, atr_period=14, sl_atr=2.0, tp_atr=2.8,
        lot_step=0.01, min_lot=0.01, max_lot=2.0, group="energy", search_term="Natural Gas",
    ),
    "silver": Market(
        key="silver", name="Silver", epic="", live_aliases=["SILVER", "XAGUSD", "silver"],
        point_value=1.0, digits=3, typical_spread=0.030, max_spread=0.150,
        session="weekday metals session", session_windows=[("01:15", "21:45")],
        fast_ema=10, slow_ema=40, atr_period=14, sl_atr=1.8, tp_atr=2.5,
        lot_step=0.01, min_lot=0.01, max_lot=2.0, group="metal", search_term="Silver",
    ),
    "usdjpy": Market(
        key="usdjpy", name="USD/JPY", epic="", live_aliases=["USDJPY", "usdjpy"],
        point_value=1.0, digits=3, typical_spread=0.015, max_spread=0.080,
        session="forex weekday", session_windows=[("00:05", "23:55")],
        fast_ema=10, slow_ema=40, atr_period=14, sl_atr=1.5, tp_atr=2.0,
        lot_step=0.01, min_lot=0.01, max_lot=2.0, group="forex", search_term="USD/JPY",
    ),
    "copper": Market(
        key="copper", name="Copper", epic="", live_aliases=["COPPER", "copper"],
        point_value=1.0, digits=4, typical_spread=0.0040, max_spread=0.0250,
        session="weekday metals session", session_windows=[("01:00", "22:45")],
        fast_ema=10, slow_ema=40, atr_period=14, sl_atr=1.8, tp_atr=2.4,
        lot_step=0.01, min_lot=0.01, max_lot=2.0, group="metal", search_term="Copper",
    ),
    "audusd": Market(
        key="audusd", name="AUD/USD", epic="", live_aliases=["AUDUSD", "audusd"],
        point_value=1.0, digits=5, typical_spread=0.00014, max_spread=0.00100,
        session="forex weekday", session_windows=[("00:05", "23:55")],
        fast_ema=10, slow_ema=40, atr_period=14, sl_atr=1.5, tp_atr=2.0,
        lot_step=0.01, min_lot=0.01, max_lot=2.0, group="forex", search_term="AUD/USD",
    ),
    "uk100": Market(
        key="uk100", name="UK 100", epic="", live_aliases=["UK100", "uk100"],
        point_value=1.0, digits=1, typical_spread=1.0, max_spread=5.0,
        session="UK cash + overlap", session_windows=[("08:00", "21:30")],
        fast_ema=12, slow_ema=48, atr_period=14, sl_atr=1.6, tp_atr=2.2,
        lot_step=0.01, min_lot=0.01, max_lot=1.0, group="index", search_term="UK100",
    ),
    "japan225": Market(
        key="japan225", name="Japan 225", epic="", live_aliases=["J225", "japan225"],
        point_value=1.0, digits=1, typical_spread=8.0, max_spread=30.0,
        session="weekday broad CFD session", session_windows=[("01:15", "21:45")],
        fast_ema=10, slow_ema=40, atr_period=14, sl_atr=1.8, tp_atr=2.4,
        lot_step=0.01, min_lot=0.01, max_lot=1.0, group="index", search_term="J225",
    ),
    "hongkong50": Market(
        key="hongkong50", name="Hong Kong 50", epic="", live_aliases=["HK50", "hongkong50"],
        point_value=1.0, digits=1, typical_spread=8.0, max_spread=35.0,
        session="weekday broad CFD session", session_windows=[("01:15", "21:45")],
        fast_ema=10, slow_ema=40, atr_period=14, sl_atr=1.9, tp_atr=2.5,
        lot_step=0.01, min_lot=0.01, max_lot=1.0, group="index", search_term="HK50",
    ),
    "france40": Market(
        key="france40", name="France 40", epic="", live_aliases=["FR40", "france40"],
        point_value=1.0, digits=1, typical_spread=1.2, max_spread=5.0,
        session="Europe cash + overlap", session_windows=[("08:00", "21:30")],
        fast_ema=12, slow_ema=48, atr_period=14, sl_atr=1.6, tp_atr=2.2,
        lot_step=0.01, min_lot=0.01, max_lot=1.0, group="index", search_term="FR40",
    ),
    "switzerland20": Market(
        key="switzerland20", name="Switzerland 20", epic="", live_aliases=["SW20", "switzerland20"],
        point_value=1.0, digits=1, typical_spread=2.0, max_spread=8.0,
        session="Europe cash + overlap", session_windows=[("08:00", "21:30")],
        fast_ema=12, slow_ema=48, atr_period=14, sl_atr=1.6, tp_atr=2.2,
        lot_step=0.01, min_lot=0.01, max_lot=1.0, group="index", search_term="SW20",
    ),
    "eurjpy": Market(
        key="eurjpy", name="EUR/JPY", epic="", live_aliases=["EURJPY", "eurjpy"],
        point_value=1.0, digits=3, typical_spread=0.020, max_spread=0.100,
        session="forex weekday", session_windows=[("00:05", "23:55")],
        fast_ema=10, slow_ema=40, atr_period=14, sl_atr=1.6, tp_atr=2.1,
        lot_step=0.01, min_lot=0.01, max_lot=2.0, group="forex", search_term="EUR/JPY",
    ),
    "gbpjpy": Market(
        key="gbpjpy", name="GBP/JPY", epic="", live_aliases=["GBPJPY", "gbpjpy"],
        point_value=1.0, digits=3, typical_spread=0.030, max_spread=0.150,
        session="forex weekday", session_windows=[("00:05", "23:55")],
        fast_ema=10, slow_ema=45, atr_period=14, sl_atr=1.7, tp_atr=2.3,
        lot_step=0.01, min_lot=0.01, max_lot=2.0, group="forex", search_term="GBP/JPY",
    ),
}


def _rule_value(block: dict, key: str, fallback: float) -> float:
    item = block.get(key) or {}
    try:
        value = float(item.get("value"))
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def apply_broker_details(market: Market, payload: dict) -> dict:
    """Apply Capital.com instrument/dealing rules to a runtime Market object."""
    instrument = payload.get("instrument") or {}
    rules = payload.get("dealingRules") or {}
    snapshot = payload.get("snapshot") or {}

    market.min_lot = _rule_value(rules, "minDealSize", market.min_lot)
    market.max_lot = _rule_value(rules, "maxDealSize", market.max_lot)
    market.lot_step = _rule_value(rules, "minSizeIncrement", market.lot_step)

    try:
        contract_size = float(instrument.get("lotSize") or market.contract_size)
        if contract_size > 0:
            market.contract_size = contract_size
    except (TypeError, ValueError):
        pass

    try:
        factor = instrument.get("marginFactor")
        market.margin_factor = float(factor) if factor is not None else market.margin_factor
    except (TypeError, ValueError):
        pass
    market.margin_factor_unit = str(
        instrument.get("marginFactorUnit") or market.margin_factor_unit or ""
    ).upper()

    try:
        digits = int(snapshot.get("decimalPlacesFactor"))
        if digits >= 0:
            market.digits = digits
    except (TypeError, ValueError):
        pass

    opening = instrument.get("openingHours") or {}
    if isinstance(opening, dict) and opening:
        market.opening_zone = str(opening.get("zone") or "UTC")
        market.opening_hours = {
            str(k).lower(): list(v)
            for k, v in opening.items()
            if k != "zone" and isinstance(v, list)
        }

    market.broker_rules = True
    return {
        "min_lot": market.min_lot,
        "max_lot": market.max_lot,
        "lot_step": market.lot_step,
        "contract_size": market.contract_size,
        "margin_factor": market.margin_factor,
        "margin_factor_unit": market.margin_factor_unit,
        "digits": market.digits,
        "opening_zone": market.opening_zone,
    }


def market_by_key(key: str) -> Market:
    if key not in MARKETS:
        raise KeyError(f"unknown market {key}")
    return MARKETS[key]


def market_by_symbol(symbol: str, markets: dict[str, Market] | None = None) -> Market | None:
    markets = markets or MARKETS
    for m in markets.values():
        if symbol == m.epic or symbol == m.key or symbol in m.live_aliases:
            return m
    return None


def _hhmm(value: str) -> tuple[int, int]:
    """Parse broker/session times in HH:MM or HH:MM:SS form."""
    parts = str(value).strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"invalid session time: {value!r}")
    return int(parts[0]), int(parts[1])


def _minutes_in_window(minutes: int, start_s: str, end_s: str) -> bool:
    sh, sm = _hhmm(start_s)
    eh, em = _hhmm(end_s)
    start = sh * 60 + sm
    end = eh * 60 + em
    if end >= start:
        return start <= minutes <= end
    return minutes >= start or minutes <= end


def in_session(market: Market, now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(BERLIN)

    if market.opening_hours:
        try:
            zone = ZoneInfo(market.opening_zone or "UTC")
        except Exception:
            zone = ZoneInfo("UTC")
        local = now.astimezone(zone)
        day = local.strftime("%a").lower()[:3]
        minutes = local.hour * 60 + local.minute
        windows = market.opening_hours.get(day) or []
        for raw in windows:
            try:
                start_s, end_s = [x.strip() for x in str(raw).split("-", 1)]
            except ValueError:
                continue
            if _minutes_in_window(minutes, start_s, end_s):
                return True, f"{market.name} broker session open ({raw} {market.opening_zone})"
        shown = ", ".join(windows) or "closed"
        return False, f"{market.name} broker session closed ({shown} {market.opening_zone})"

    local = now.astimezone(BERLIN)
    if local.weekday() >= 5:
        return False, f"{market.name} weekend closed"
    minutes = local.hour * 60 + local.minute
    for start_s, end_s in market.session_windows:
        if _minutes_in_window(minutes, start_s, end_s):
            return True, f"{market.name} session open ({start_s}-{end_s} Berlin)"
    windows = ", ".join(f"{a}-{b}" for a, b in market.session_windows)
    return False, f"{market.name} outside session ({windows} Berlin)"
