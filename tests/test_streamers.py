import unittest

import app  # noqa: F401 - keeps legacy top-level imports available

from streamers import classify_text, consensus_from_items


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


if __name__ == "__main__":
    unittest.main()
