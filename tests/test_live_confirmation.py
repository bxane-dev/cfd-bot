import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import app  # noqa: F401 - keeps legacy top-level imports available

from instruments import MARKETS
from main import creator_gate_decision, queue_live_order_confirmation, resolve_live_order_confirmation


class LiveOrderConfirmationTests(unittest.TestCase):
    def _cfg(self):
        return {
            "live_confirmation": {"max_age_seconds": 60},
            "streamers": {
                "enabled": True,
                "majority_threshold": 0.70,
                "require_consensus": True,
                "veto_opposite": True,
                "fallback_if_no_matching_creators": True,
                "require_complete_platform_check": True,
            },
            "broker": {"comment": "test"},
        }

    def _queue(self, state):
        market = copy.deepcopy(MARKETS["gold"])
        with patch("main.save_state"):
            queued = queue_live_order_confirmation(
                self._cfg(),
                state,
                market,
                "GOLD",
                "rsi_reversion",
                "2026-09-25 08:00:00+00:00",
                "buy",
                0.01,
                98.0,
                104.0,
                100.0,
                {
                    "side": "buy",
                    "confidence": 0.75,
                    "votes": 4,
                    "buy_votes": 3,
                    "sell_votes": 1,
                    "platform_counts": {"youtube": 2, "twitch": 1, "kick": 1},
                    "sources": [],
                },
                currency="CHF",
                risk_cash=1.0,
            )
        self.assertTrue(queued["ok"])
        return market, queued["order_id"]

    def test_reject_never_touches_broker(self):
        state = {"order_guard": {}, "pending_live_orders": {}, "diagnostics": {}}
        market, order_id = self._queue(state)

        class Broker:
            def account(self):
                raise AssertionError("reject must not query or touch broker")

            def market_order(self, *args, **kwargs):
                raise AssertionError("reject must not place an order")

        with (
            patch("main.save_state"),
            patch("main.remember"),
        ):
            result = resolve_live_order_confirmation(
                self._cfg(),
                "live",
                Broker(),
                SimpleNamespace(),
                state,
                [market],
                {"gold": "GOLD"},
                order_id,
                approve=False,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "rejected")
        self.assertNotIn(order_id, state["pending_live_orders"])
        self.assertEqual(state["order_guard"][order_id]["status"], "rejected_by_user")

    def test_expired_approval_never_touches_broker(self):
        state = {"order_guard": {}, "pending_live_orders": {}, "diagnostics": {}}
        market, order_id = self._queue(state)
        state["pending_live_orders"][order_id]["created_time"] = (
            datetime.now(timezone.utc) - timedelta(seconds=120)
        ).isoformat()

        class Broker:
            def account(self):
                raise AssertionError("expired approval must not query broker")

            def market_order(self, *args, **kwargs):
                raise AssertionError("expired approval must not place an order")

        with patch("main.save_state"):
            result = resolve_live_order_confirmation(
                self._cfg(),
                "live",
                Broker(),
                SimpleNamespace(),
                state,
                [market],
                {"gold": "GOLD"},
                order_id,
                approve=True,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "expired")
        self.assertNotIn(order_id, state["pending_live_orders"])

    def test_approved_fresh_order_rechecks_then_submits_once(self):
        state = {"order_guard": {}, "pending_live_orders": {}, "diagnostics": {}, "bot_deal_ids": {}}
        market, order_id = self._queue(state)
        calls = []

        class Broker:
            def account(self):
                return SimpleNamespace(equity=1000.0, currency="CHF", positions=[])

            def quote(self, symbol):
                self.last_symbol = symbol
                return 99.9, 100.1

            def market_order(self, symbol, side, lots, sl, tp, comment):
                calls.append((symbol, side, lots, sl, tp, comment))
                return SimpleNamespace(
                    ok=True,
                    price=100.0,
                    message="status=ACCEPTED",
                    deal_id="deal-live-1",
                )

        class Risk:
            def check_account(self, equity, open_positions, open_index=0):
                return SimpleNamespace(allowed=True, reason="ok")

            def allow_new(self, *args, **kwargs):
                return SimpleNamespace(allowed=True, reason="ok")

            def spread_ok(self, bid, ask, market):
                return SimpleNamespace(allowed=True, reason="spread ok")

            def size_lots(self, *args, **kwargs):
                return SimpleNamespace(allowed=True, reason="ok", lots=0.01)

            def estimate_margin(self, price, lots, market):
                return 0.0

        with (
            patch("main.save_state"),
            patch("main.append_trade"),
            patch("main.remember"),
            patch("main.remember_case"),
            patch(
                "main.streamer_signal",
                return_value={
                    "side": "buy",
                    "confidence": 0.75,
                    "votes": 4,
                    "buy_votes": 3,
                    "sell_votes": 1,
                    "matched_creators": 4,
                    "lookup_succeeded": True,
                    "fallback_without_streamers": False,
                    "platform_counts": {"youtube": 2, "twitch": 1, "kick": 1},
                    "platform_status": {"youtube": {"ok": True, "found": 2}},
                    "sources": [],
                    "reason": "creator majority buy 3/4 (75%)",
                },
            ),
        ):
            result = resolve_live_order_confirmation(
                self._cfg(),
                "live",
                Broker(),
                Risk(),
                state,
                [market],
                {"gold": "GOLD"},
                order_id,
                approve=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:5], ("GOLD", "buy", 0.01, 98.0, 104.0))
        self.assertNotIn(order_id, state["pending_live_orders"])
        self.assertEqual(state["order_guard"][order_id]["status"], "accepted")
        self.assertIn("deal-live-1", state["bot_deal_ids"])

    def test_zero_matching_creators_can_fallback(self):
        ok, reason, mode = creator_gate_decision(
            self._cfg(),
            {
                "side": "neutral",
                "confidence": 0.0,
                "votes": 0,
                "matched_creators": 0,
                "lookup_succeeded": True,
                "fallback_without_streamers": True,
                "reason": "no matching creators found",
            },
            "buy",
        )
        self.assertTrue(ok)
        self.assertEqual(mode, "fallback_no_creators")
        self.assertIn("search again", reason)

    def test_creator_api_outage_does_not_fallback(self):
        ok, reason, mode = creator_gate_decision(
            self._cfg(),
            {
                "side": "neutral",
                "confidence": 0.0,
                "votes": 0,
                "matched_creators": 0,
                "lookup_succeeded": False,
                "fallback_without_streamers": False,
                "reason": "creator lookup unavailable on all configured platforms",
            },
            "buy",
        )
        self.assertFalse(ok)
        self.assertEqual(mode, "lookup_incomplete")
        self.assertIn("unavailable", reason)

    def test_live_approval_rechecks_and_allows_zero_match_fallback(self):
        state = {"order_guard": {}, "pending_live_orders": {}, "diagnostics": {}, "bot_deal_ids": {}}
        market, order_id = self._queue(state)
        calls = []

        class Broker:
            def account(self):
                return SimpleNamespace(equity=1000.0, currency="CHF", positions=[])

            def quote(self, symbol):
                return 99.9, 100.1

            def market_order(self, symbol, side, lots, sl, tp, comment):
                calls.append((symbol, side, lots, sl, tp, comment))
                return SimpleNamespace(
                    ok=True,
                    price=100.0,
                    message="status=ACCEPTED",
                    deal_id="deal-fallback-1",
                )

        class Risk:
            def check_account(self, *args, **kwargs):
                return SimpleNamespace(allowed=True, reason="ok")

            def allow_new(self, *args, **kwargs):
                return SimpleNamespace(allowed=True, reason="ok")

            def spread_ok(self, *args, **kwargs):
                return SimpleNamespace(allowed=True, reason="spread ok")

            def size_lots(self, *args, **kwargs):
                return SimpleNamespace(allowed=True, reason="ok", lots=0.01)

            def estimate_margin(self, *args, **kwargs):
                return 0.0

        fallback = {
            "side": "neutral",
            "confidence": 0.0,
            "votes": 0,
            "buy_votes": 0,
            "sell_votes": 0,
            "matched_creators": 0,
            "lookup_succeeded": True,
            "fallback_without_streamers": True,
            "platform_counts": {},
            "platform_status": {"youtube": {"ok": True, "found": 0}},
            "sources": [],
            "reason": "no matching creators found on available platforms — fallback without creator gate; search will be retried",
        }
        with (
            patch("main.save_state"),
            patch("main.append_trade"),
            patch("main.remember"),
            patch("main.remember_case"),
            patch("main.streamer_signal", return_value=fallback) as refreshed,
        ):
            result = resolve_live_order_confirmation(
                self._cfg(),
                "live",
                Broker(),
                Risk(),
                state,
                [market],
                {"gold": "GOLD"},
                order_id,
                approve=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 1)
        refreshed.assert_called_once()
        self.assertTrue(refreshed.call_args.kwargs["force_refresh"])


if __name__ == "__main__":
    unittest.main()
