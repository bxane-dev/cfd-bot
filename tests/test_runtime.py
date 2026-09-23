import copy
import threading
import time
import unittest
from datetime import datetime, timezone

import app  # noqa: F401 - keeps legacy top-level imports available

from broker.capital import CapitalBroker
from broker.base import AccountState, Fill, Position
from instruments import MARKETS
from risk import RiskManager
from streamers import classify_text
from research.walk import pick_winner
from main import (
    capital_map,
    effective_trade_cfg,
    enabled_markets,
    portfolio_adjusted_protection,
    resolved_markets,
    retry_scan_due,
    runtime_lookback_bars,
    sync_manual_trade_protection,
    terminal_scan_due,
)


class RuntimeRegressionTests(unittest.TestCase):
    def test_market_resolution_skips_unavailable_symbol(self):
        markets = [copy.deepcopy(MARKETS["gold"]), copy.deepcopy(MARKETS["us500"])]
        markets[1].epic = ""

        class Broker:
            def resolve_epic(self, term):
                raise RuntimeError("not offered")

        live_map = capital_map(markets, Broker())
        self.assertEqual(live_map["gold"], "GOLD")
        self.assertNotIn("us500", live_map)
        self.assertEqual([m.key for m in resolved_markets(markets, live_map)], ["gold"])

    def test_shared_risk_is_point_seven_percent(self):
        cfg = {"account": {"leverage": 20}, "risk": {"risk_per_trade_pct": 0.7}}
        rm = RiskManager(cfg)
        self.assertEqual(rm.snapshot()["per_trade"], 0.7)

    def test_bootstrap_profile_activates_between_50_and_200(self):
        cfg = {
            "account": {"leverage": 20},
            "risk": {
                "risk_per_trade_pct": 0.7,
                "max_portfolio_allocation_pct": 30.0,
                "max_open_positions": 12,
                "max_positions_per_market": 4,
                "max_index_positions": 8,
                "daily_loss_enabled": False,
                "bootstrap": {
                    "enabled": True,
                    "min_equity": 40.0,
                    "target_equity": 200.0,
                    "risk_per_trade_pct": 2.0,
                    "max_min_lot_risk_pct": 4.0,
                    "max_portfolio_allocation_pct": 80.0,
                    "max_open_positions": 3,
                    "max_positions_per_market": 2,
                    "max_index_positions": 2,
                },
            },
        }
        rm = RiskManager(cfg)

        below = rm.snapshot(39.99)
        bootstrap = rm.snapshot(50.0)
        standard = rm.snapshot(200.0)

        self.assertEqual(below["profile"], "below_minimum")
        self.assertEqual(bootstrap["profile"], "bootstrap")
        self.assertEqual(bootstrap["per_trade"], 2.0)
        self.assertEqual(bootstrap["max_portfolio_allocation_pct"], 80.0)
        self.assertEqual(bootstrap["max_open"], 3)
        self.assertEqual(standard["profile"], "standard")
        self.assertEqual(standard["per_trade"], 0.7)
        self.assertEqual(standard["max_portfolio_allocation_pct"], 30.0)
        self.assertEqual(standard["max_open"], 12)

    def test_bootstrap_blocks_new_entries_below_50(self):
        cfg = {
            "account": {"leverage": 20},
            "risk": {
                "risk_per_trade_pct": 0.7,
                "max_portfolio_allocation_pct": 30.0,
                "max_open_positions": 12,
                "max_positions_per_market": 4,
                "max_index_positions": 8,
                "daily_loss_enabled": False,
                "bootstrap": {
                    "enabled": True,
                    "min_equity": 40.0,
                    "target_equity": 200.0,
                    "risk_per_trade_pct": 2.0,
                    "max_min_lot_risk_pct": 4.0,
                    "max_portfolio_allocation_pct": 80.0,
                    "max_open_positions": 3,
                    "max_positions_per_market": 2,
                    "max_index_positions": 2,
                },
            },
        }
        rm = RiskManager(cfg)
        decision = rm.check_account(39.99, 0)
        self.assertFalse(decision.allowed)
        self.assertIn("below bootstrap minimum", decision.reason)

    def test_bootstrap_can_use_minimum_lot_inside_four_percent_cap(self):
        market = copy.deepcopy(MARKETS["gold"])
        market.contract_size = 1.0
        market.point_value = 1.0
        market.min_lot = 0.01
        market.lot_step = 0.01
        market.max_lot = 2.0
        market.margin_factor = None
        cfg = {
            "account": {"leverage": 20},
            "risk": {
                "risk_per_trade_pct": 0.7,
                "max_portfolio_allocation_pct": 30.0,
                "max_open_positions": 12,
                "max_positions_per_market": 4,
                "max_index_positions": 8,
                "daily_loss_enabled": False,
                "bootstrap": {
                    "enabled": True,
                    "min_equity": 40.0,
                    "target_equity": 200.0,
                    "risk_per_trade_pct": 2.0,
                    "max_min_lot_risk_pct": 4.0,
                    "max_portfolio_allocation_pct": 80.0,
                    "max_open_positions": 3,
                    "max_positions_per_market": 2,
                    "max_index_positions": 2,
                },
            },
        }
        rm = RiskManager(cfg)
        # CHF 50 * 2% = 1.00 target risk. A 120-point stop risks 1.20
        # at the 0.01 minimum lot, which is allowed by the 4% (=2.00) hard cap.
        sized = rm.size_lots(50.0, 120.0, market, price=100.0)
        self.assertTrue(sized.allowed)
        self.assertEqual(sized.reason, "bootstrap minimum lot")
        self.assertAlmostEqual(sized.lots, 0.01)

    def test_manual_trade_gets_bot_sl_tp_once_and_is_not_overwritten(self):
        calls = []

        class Broker:
            def modify_position(self, deal_id, sl, tp, comment="modify"):
                calls.append((deal_id, sl, tp, comment))
                return Fill(
                    1, "GOLD", "buy", 0.01, 0, sl, tp, comment, True, "status=ACCEPTED",
                    deal_id=deal_id,
                    deal_reference="p_test",
                )

        now = datetime.now(timezone.utc).isoformat()
        state = {
            "manual_protection_initialized": True,
            "bot_deal_ids": {},
            "manual_protection": {},
            "recommendations": {
                "gold": {
                    "time": now,
                    "market": "gold",
                    "side": "buy",
                    "sl": 98.0,
                    "tp": 106.0,
                }
            },
        }
        cfg = {
            "manual_trade_protection": {
                "enabled": True,
                "max_recommendation_age_seconds": 300,
                "require_matching_side": True,
                "ignore_existing_on_first_start": True,
            }
        }
        pos = Position(
            ticket=123,
            symbol="GOLD",
            side="buy",
            lots=0.01,
            entry=100.0,
            sl=0.0,
            tp=0.0,
            deal_id="manual-1",
        )

        class Risk:
            def snapshot(self, equity):
                return {"per_trade": 2.0}

        changed = sync_manual_trade_protection(
            cfg, Broker(), state, [pos], equity=100.0, risk=Risk()
        )
        self.assertTrue(changed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3], ("manual-1", 98.0, 106.0))
        self.assertEqual(state["manual_protection"]["manual-1"]["status"], "applied_once")

        # Simulate the user manually changing SL/TP after the bot's first sync.
        pos.sl = 99.0
        pos.tp = 108.0
        changed_again = sync_manual_trade_protection(
            cfg, Broker(), state, [pos], equity=100.0, risk=Risk()
        )
        self.assertFalse(changed_again)
        self.assertEqual(len(calls), 1)

    def test_portfolio_based_protection_scales_cash_risk_and_preserves_rr(self):
        market = copy.deepcopy(MARKETS["gold"])
        market.point_value = 1.0
        market.contract_size = 1.0
        market.digits = 2

        class Risk:
            def snapshot(self, equity):
                return {"per_trade": 2.0}

        pos = Position(
            ticket=777,
            symbol="GOLD",
            side="buy",
            lots=1.0,
            entry=100.0,
            sl=0.0,
            tp=0.0,
            deal_id="manual-portfolio",
        )
        rec = {
            "side": "buy",
            "price": 100.0,
            "sl": 95.0,
            "tp": 110.0,
        }
        adjusted = portfolio_adjusted_protection(100.0, Risk(), market, pos, rec)
        self.assertIsNotNone(adjusted)
        self.assertEqual(adjusted["risk_cash"], 2.0)
        self.assertEqual(adjusted["sl"], 98.0)
        self.assertEqual(adjusted["tp"], 104.0)
        self.assertAlmostEqual(adjusted["reward_risk"], 2.0)
        self.assertTrue(adjusted["portfolio_adjusted"])

    def test_manual_trade_sync_ignores_bot_owned_deal(self):
        class Broker:
            def modify_position(self, deal_id, sl, tp, comment="modify"):
                raise AssertionError("bot-owned position must not be modified")

        state = {
            "manual_protection_initialized": True,
            "bot_deal_ids": {"bot-1": {"market": "gold"}},
            "manual_protection": {},
            "recommendations": {
                "gold": {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "side": "buy",
                    "sl": 98.0,
                    "tp": 106.0,
                }
            },
        }
        cfg = {"manual_trade_protection": {"enabled": True}}
        pos = Position(
            ticket=124,
            symbol="GOLD",
            side="buy",
            lots=0.01,
            entry=100.0,
            sl=98.0,
            tp=106.0,
            deal_id="bot-1",
        )
        self.assertFalse(sync_manual_trade_protection(cfg, Broker(), state, [pos]))

    def test_capital_position_modify_is_confirmed(self):
        broker = CapitalBroker.__new__(CapitalBroker)
        broker._invalidate_account_cache = lambda: None
        requests = []

        def fake_request(method, path, payload=None, query=None):
            requests.append((method, path, payload))
            if method == "PUT" and path == "/api/v1/positions/deal-1":
                return {"dealReference": "p_test"}
            if method == "GET" and path == "/api/v1/confirms/p_test":
                return {"dealStatus": "ACCEPTED", "dealId": "deal-1"}
            raise AssertionError((method, path))

        broker._request = fake_request
        result = broker.modify_position("deal-1", 98.0, 106.0)
        self.assertTrue(result.ok)
        self.assertEqual(result.deal_id, "deal-1")
        self.assertEqual(
            requests[0],
            ("PUT", "/api/v1/positions/deal-1", {"stopLevel": 98.0, "profitLevel": 106.0}),
        )

    def test_capital_stream_quote_cache_honors_freshness(self):
        broker = CapitalBroker.__new__(CapitalBroker)
        broker._stream_lock = threading.Lock()
        broker._stream_quotes = {
            "GOLD": {
                "bid": 100.0,
                "ask": 100.2,
                "received_at": time.monotonic(),
            }
        }
        quote = broker.stream_quote("GOLD", max_age=5.0)
        self.assertIsNotNone(quote)
        self.assertEqual(quote[:2], (100.0, 100.2))
        broker._stream_quotes["GOLD"]["received_at"] = time.monotonic() - 10
        self.assertIsNone(broker.stream_quote("GOLD", max_age=5.0))

    def test_retry_scan_throttles_recent_nonterminal_block(self):
        now = datetime(2026, 9, 23, 14, 0, 10, tzinfo=timezone.utc)
        state = {
            "diagnostics": {
                "gold": {
                    "time": "2026-09-23T14:00:00+00:00",
                    "status": "blocked",
                    "terminal": False,
                }
            }
        }
        self.assertFalse(retry_scan_due(state, "gold", 15, now))
        self.assertTrue(
            retry_scan_due(
                state,
                "gold",
                15,
                datetime(2026, 9, 23, 14, 0, 16, tzinfo=timezone.utc),
            )
        )

    def test_runtime_lookback_is_small_except_vwap(self):
        cfg = {
            "execution": {"runtime_lookback_bars": 180},
            "strategy": {
                "default": "ema_pullback",
                "per_market": {"france40": "vwap", "gold": "rsi_reversion"},
            },
        }
        self.assertEqual(
            runtime_lookback_bars(cfg, MARKETS["gold"], "1m", 400),
            180,
        )
        self.assertEqual(
            runtime_lookback_bars(cfg, MARKETS["france40"], "1m", 400),
            600,
        )
        self.assertEqual(
            runtime_lookback_bars(cfg, MARKETS["gold"], "5m", 400),
            400,
        )

    def test_terminal_bar_scheduler_skips_redundant_scans(self):
        state = {
            "bar_state": {
                "gold": {
                    "bar": "2026-09-22T14:00:00+00:00",
                    "terminal": True,
                }
            }
        }
        self.assertFalse(
            terminal_scan_due(
                state,
                "gold",
                "1m",
                datetime(2026, 9, 22, 14, 1, 30, tzinfo=timezone.utc),
            )
        )
        self.assertTrue(
            terminal_scan_due(
                state,
                "gold",
                "1m",
                datetime(2026, 9, 22, 14, 2, 2, tzinfo=timezone.utc),
            )
        )

    def test_account_cache_reuses_recent_snapshot(self):
        broker = CapitalBroker.__new__(CapitalBroker)
        cached = AccountState(1000.0, 1000.0, "EUR", [])
        broker._last_account_state = cached
        broker._last_account_at = time.monotonic()
        broker.account = lambda: (_ for _ in ()).throw(AssertionError("network fetch"))
        self.assertIs(broker.account_cached(6.0), cached)

    def test_unconfirmed_order_is_not_reported_as_success(self):
        broker = CapitalBroker.__new__(CapitalBroker)

        def fake_request(method, path, payload=None, query=None):
            if method == "POST" and path == "/api/v1/positions":
                return {"dealReference": "o_test"}
            raise RuntimeError("confirmation temporarily unavailable")

        broker._request = fake_request
        fill = broker.market_order("GOLD", "buy", 1.0, 100.0, 110.0, "test")
        self.assertFalse(fill.ok)
        self.assertIn("UNCONFIRMED", fill.message)

    def test_confirmed_order_can_succeed(self):
        broker = CapitalBroker.__new__(CapitalBroker)

        def fake_request(method, path, payload=None, query=None):
            if method == "POST":
                return {"dealReference": "o_test"}
            if path == "/api/v1/confirms/o_test":
                return {
                    "dealStatus": "ACCEPTED",
                    "dealId": "deal-1",
                    "level": 105.0,
                }
            raise AssertionError(path)

        broker._request = fake_request
        fill = broker.market_order("GOLD", "buy", 1.0, 100.0, 110.0, "test")
        self.assertTrue(fill.ok)
        self.assertEqual(fill.price, 105.0)

    def test_contract_size_is_used_in_risk_sizing(self):
        market = copy.deepcopy(MARKETS["gold"])
        market.contract_size = 50.0
        market.min_lot = 0.01
        market.lot_step = 0.01
        market.max_lot = 10.0
        cfg = {
            "account": {"leverage": 20},
            "risk": {
                "risk_per_trade_pct": 0.4,
                "max_portfolio_allocation_pct": 100.0,
                "daily_loss_enabled": False,
                "max_open_positions": 12,
                "max_positions_per_market": 4,
                "max_index_positions": 8,
            },
        }
        rm = RiskManager(cfg)
        sized = rm.size_lots(10_000.0, 1.0, market, price=100.0)
        self.assertTrue(sized.allowed)
        self.assertAlmostEqual(sized.lots, 0.8)

    def test_streamer_negation_is_not_a_directional_vote(self):
        self.assertEqual(classify_text("Gold not bullish - do not buy"), "neutral")
        self.assertEqual(classify_text("Nasdaq bearish short setup"), "sell")
        self.assertEqual(classify_text("Gold bullish buy setup"), "buy")

    def test_demo_frequency_overrides_do_not_change_live(self):
        cfg = {
            "mode": "demo",
            "execution": {
                "demo_market_scope": "all",
                "demo_frequency": {
                    "quality": {"min_reward_risk": 1.0},
                    "news": {"min_confidence": 0.68},
                },
            },
            "quality": {"min_reward_risk": 1.05},
            "news": {"min_confidence": 0.62},
            "markets": {
                "gold": {"enabled": True, "live_enabled": True},
                "us500": {"enabled": True, "live_enabled": False},
            },
        }
        demo = effective_trade_cfg(cfg, "demo")
        live = effective_trade_cfg(cfg, "live")
        self.assertEqual(demo["quality"]["min_reward_risk"], 1.0)
        self.assertEqual(demo["news"]["min_confidence"], 0.68)
        self.assertEqual(live["quality"]["min_reward_risk"], 1.05)
        self.assertEqual(live["news"]["min_confidence"], 0.62)

    def test_no_profitable_strategy_returns_no_winner(self):
        rows = [
            {
                "name": "a",
                "error": None,
                "trades": 10,
                "pnl": -5.0,
                "expectancy": -0.5,
                "pf": 0.8,
                "score": -1.0,
                "win_rate": 40.0,
            }
        ]
        self.assertIsNone(pick_winner(rows))


if __name__ == "__main__":
    unittest.main()
