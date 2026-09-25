import unittest
from unittest.mock import patch

import app  # noqa: F401 - keeps legacy top-level imports available

from instruments import MARKETS
from streamers import classify_text, consensus_from_items, streamer_signal


class StreamerConsensusTests(unittest.TestCase):
    def _cfg(self):
        return {
            "streamers": {
                "min_sources": 3,
                "majority_threshold": 0.70,
                "recent_only": False,
                "max_age_hours": 0,
            }
        }

    def test_seventy_percent_is_directional_consensus(self):
        items = []
        for i in range(7):
            items.append({
                "platform": "youtube" if i % 3 == 0 else "twitch",
                "channel": f"bull_{i}",
                "title": "EURUSD bullish buy signal",
                "published": "live",
                "live": True,
            })
        for i in range(3):
            items.append({
                "platform": "kick",
                "channel": f"bear_{i}",
                "title": "EURUSD bearish sell signal",
                "published": "live",
                "live": True,
            })
        result = consensus_from_items(items, self._cfg())
        self.assertEqual(result["side"], "buy")
        self.assertEqual(result["votes"], 10)
        self.assertAlmostEqual(result["confidence"], 0.70)

    def test_below_seventy_percent_stays_neutral(self):
        items = []
        for i in range(6):
            items.append({
                "platform": "youtube",
                "channel": f"bull_{i}",
                "title": "Gold bullish buy signal",
                "published": "live",
                "live": True,
            })
        for i in range(4):
            items.append({
                "platform": "twitch",
                "channel": f"bear_{i}",
                "title": "Gold bearish sell signal",
                "published": "live",
                "live": True,
            })
        result = consensus_from_items(items, self._cfg())
        self.assertEqual(result["side"], "neutral")
        self.assertAlmostEqual(result["confidence"], 0.60)

    def test_multistream_same_creator_only_votes_once(self):
        items = [
            {
                "platform": platform,
                "channel": "TraderOne",
                "title": "XAUUSD bullish buy signal",
                "published": "live",
                "live": True,
            }
            for platform in ("youtube", "twitch", "kick")
        ]
        result = consensus_from_items(items, self._cfg())
        self.assertEqual(result["votes"], 1)
        self.assertEqual(result["side"], "neutral")

    def test_buy_now_and_sell_now_are_explicit(self):
        self.assertEqual(classify_text("XAUUSD buy now - bullish"), "buy")
        self.assertEqual(classify_text("EURUSD sell now - bearish"), "sell")

    def test_zero_matches_fallback_requires_all_platforms_checked(self):
        cfg = {
            "streamers": {
                "enabled": True,
                "platforms": ["youtube", "twitch", "kick"],
                "min_sources": 3,
                "majority_threshold": 0.70,
                "fallback_if_no_matching_creators": True,
                "recent_only": False,
                "max_age_hours": 0,
            }
        }
        with (
            patch("streamers._youtube_search", return_value=[]),
            patch("streamers._twitch_search", return_value=[]),
            patch("streamers._kick_directory", return_value=[]),
        ):
            result = streamer_signal(cfg, MARKETS["gold"], force_refresh=True)

        self.assertTrue(result["lookup_complete"])
        self.assertTrue(result["fallback_without_streamers"])
        self.assertEqual(result["matched_creators"], 0)

    def test_partial_platform_outage_does_not_fallback(self):
        cfg = {
            "streamers": {
                "enabled": True,
                "platforms": ["youtube", "twitch", "kick"],
                "min_sources": 3,
                "majority_threshold": 0.70,
                "fallback_if_no_matching_creators": True,
                "recent_only": False,
                "max_age_hours": 0,
            }
        }
        with (
            patch("streamers._youtube_search", return_value=[]),
            patch("streamers._twitch_search", side_effect=RuntimeError("twitch unavailable")),
            patch("streamers._kick_directory", return_value=[]),
        ):
            result = streamer_signal(cfg, MARKETS["gold"], force_refresh=True)

        self.assertFalse(result["lookup_complete"])
        self.assertFalse(result["fallback_without_streamers"])
        self.assertIn("incomplete", result["reason"])


if __name__ == "__main__":
    unittest.main()
