import unittest

from tradebot.strategy import assess_long_setup


def frame(
    *,
    ema9="101",
    ema21="100",
    rsi="55",
    atr="1",
    vwap="100",
    relvol="1.1",
    close="101",
    high="102",
    low="98",
):
    return {
        "candles_count": 30,
        "ema9": ema9,
        "ema21": ema21,
        "rsi14": rsi,
        "atr14": atr,
        "vwap": vwap,
        "relative_volume": relvol,
        "last_close": close,
        "recent_high": high,
        "recent_low": low,
    }


class StrategyPrecheckTests(unittest.TestCase):
    def test_trend_continuation_qualifies(self):
        context = {
            "spread_percent": "0.05",
            "technical_1d": frame(),
            "technical_1h": frame(),
            "technical_15m": frame(rsi="60"),
            "technical_5m": frame(rsi="58", relvol="0.9"),
            "technical_1m": frame(),
        }
        result = assess_long_setup(context)
        self.assertTrue(result["review_candidate"])
        self.assertEqual(result["market_regime"], "TREND_UP")
        self.assertEqual(result["setup_type"], "TREND_CONTINUATION")
        self.assertFalse(result["score_is_probability"])

    def test_weak_1m_is_warning_not_hard_veto(self):
        context = {
            "spread_percent": "0.05",
            "technical_1d": frame(),
            "technical_1h": frame(),
            "technical_15m": frame(rsi="60"),
            "technical_5m": frame(rsi="58", relvol="0.9"),
            "technical_1m": frame(
                ema9="99",
                ema21="100",
                close="99",
                vwap="100",
            ),
        }
        result = assess_long_setup(context)
        self.assertTrue(result["review_candidate"])
        self.assertIn("1M_TIMING_NOT_CONFIRMED", result["warnings"])

    def test_downtrend_is_rejected(self):
        down = frame(ema9="99", ema21="100", close="99", vwap="100")
        context = {
            "spread_percent": "0.05",
            "technical_1d": down,
            "technical_1h": down,
            "technical_15m": down,
            "technical_5m": down,
            "technical_1m": down,
        }
        result = assess_long_setup(context)
        self.assertFalse(result["review_candidate"])
        self.assertEqual(result["market_regime"], "TREND_DOWN")

    def test_wide_spread_is_rejected(self):
        context = {
            "spread_percent": "0.30",
            "technical_1d": frame(),
            "technical_1h": frame(),
            "technical_15m": frame(),
            "technical_5m": frame(),
            "technical_1m": frame(),
        }
        result = assess_long_setup(context)
        self.assertFalse(result["review_candidate"])
        self.assertTrue(any(x.startswith("SPREAD_TOO_WIDE") for x in result["reasons"]))


if __name__ == "__main__":
    unittest.main()
