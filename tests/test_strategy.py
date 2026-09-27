import unittest

from tradebot.strategy import assess_long_setup


def frame(
    *,
    ema20="100",
    ema50="98",
    ema50_old="97",
    return20="0.08",
    rsi="55",
    atr="2",
    vwap="100",
    relvol="1.0",
    avgvol="1000000",
    close="101",
    high5="103",
    low5="99.5",
):
    return {
        "candles_count": 60,
        "ema9": "100.5",
        "ema20": ema20,
        "ema21": "99.8",
        "ema50": ema50,
        "ema50_10_ago": ema50_old,
        "return_20": return20,
        "rsi14": rsi,
        "atr14": atr,
        "vwap": vwap,
        "relative_volume": relvol,
        "average_volume_20": avgvol,
        "last_close": close,
        "recent_high": high5,
        "recent_low": low5,
        "recent_high_5": high5,
        "recent_low_5": low5,
    }


def benchmark(
    *,
    ema20="100",
    ema50="95",
    ema50_old="94",
    close="102",
    return20="0.05",
):
    return {
        "ticker": "EQMX",
        "technical_1d": frame(
            ema20=ema20,
            ema50=ema50,
            ema50_old=ema50_old,
            close=close,
            return20=return20,
            atr="2",
            avgvol="5000000",
            low5="98",
        ),
    }


class CapitalPreservationTrendV21Tests(unittest.TestCase):
    def valid_context(self):
        return {
            "spread_percent": "0.03",
            "market_benchmark": benchmark(),
            "technical_1d": frame(
                ema20="100",
                ema50="95",
                ema50_old="94",
                return20="0.08",
                close="102",
                atr="2",
                avgvol="1000000",
                low5="98",
            ),
            "technical_1h": frame(
                ema20="100",
                ema50="98",
                ema50_old="97",
                close="101",
                atr="2",
                low5="100",
            ),
            "technical_15m": frame(
                ema20="100",
                ema50="99",
                ema50_old="98.5",
                close="101",
                vwap="100.2",
                rsi="58",
                relvol="0.9",
                low5="99.5",
            ),
            "technical_5m": frame(
                ema20="100",
                ema50="99.5",
                ema50_old="99",
                close="101",
                vwap="100.4",
                relvol="0.8",
                low5="100",
            ),
        }

    def test_trend_pullback_qualifies(self):
        result = assess_long_setup(self.valid_context())
        self.assertTrue(result["review_candidate"])
        self.assertEqual(result["strategy_version"], "2.1")
        self.assertEqual(
            result["strategy_name"],
            "CAPITAL_PRESERVATION_TREND_MARKET_RS",
        )
        self.assertEqual(result["market_regime"], "TREND_UP")
        self.assertEqual(result["setup_type"], "TREND_PULLBACK")
        self.assertFalse(result["score_is_probability"])

    def test_weak_5m_is_warning_not_hard_veto(self):
        context = self.valid_context()
        context["technical_5m"] = frame(
            ema20="100",
            ema50="99.5",
            ema50_old="99",
            close="99.8",
            vwap="100.4",
            relvol="0.4",
            low5="99.5",
        )
        result = assess_long_setup(context)
        self.assertTrue(result["review_candidate"])
        self.assertIn("5M_TIMING_BELOW_VWAP", result["warnings"])
        self.assertIn("5M_RELATIVE_VOLUME_WEAK", result["warnings"])

    def test_market_risk_off_is_rejected(self):
        context = self.valid_context()
        context["market_benchmark"] = benchmark(
            ema20="94",
            ema50="95",
            ema50_old="96",
            close="93",
            return20="-0.05",
        )
        result = assess_long_setup(context)
        self.assertFalse(result["review_candidate"])
        self.assertIn("MARKET_RISK_OFF", result["reasons"])

    def test_relative_strength_too_low_is_rejected(self):
        context = self.valid_context()
        context["technical_1d"] = frame(
            ema20="100",
            ema50="95",
            ema50_old="94",
            return20="0.052",
            close="102",
            atr="2",
            avgvol="1000000",
            low5="98",
        )
        result = assess_long_setup(context)
        self.assertFalse(result["review_candidate"])
        self.assertTrue(
            any(
                x.startswith("RELATIVE_STRENGTH_TOO_LOW")
                for x in result["reasons"]
            )
        )

    def test_no_pullback_is_rejected(self):
        context = self.valid_context()
        context["technical_1h"] = frame(
            ema20="100",
            ema50="98",
            ema50_old="97",
            close="104",
            atr="2",
            low5="103",
        )
        result = assess_long_setup(context)
        self.assertFalse(result["review_candidate"])
        self.assertIn("NO_RECENT_PULLBACK_TO_1H_EMA20", result["reasons"])

    def test_overextended_daily_price_is_rejected(self):
        context = self.valid_context()
        context["technical_1d"] = frame(
            ema20="100",
            ema50="95",
            ema50_old="94",
            return20="0.08",
            close="104",
            atr="2",
            avgvol="1000000",
            low5="99",
        )
        result = assess_long_setup(context)
        self.assertFalse(result["review_candidate"])
        self.assertIn("DAILY_PRICE_OVEREXTENDED", result["reasons"])

    def test_broken_hourly_structure_is_rejected(self):
        context = self.valid_context()
        context["technical_1h"] = frame(
            ema20="100",
            ema50="98",
            ema50_old="97",
            close="101",
            atr="2",
            low5="96",
        )
        result = assess_long_setup(context)
        self.assertFalse(result["review_candidate"])
        self.assertIn(
            "PULLBACK_BROKE_1H_TREND_STRUCTURE",
            result["reasons"],
        )

    def test_downtrend_is_rejected(self):
        down = frame(
            ema20="98",
            ema50="100",
            ema50_old="99",
            return20="-0.05",
            close="97",
            vwap="98",
            low5="96",
        )
        context = {
            "spread_percent": "0.03",
            "market_benchmark": benchmark(),
            "technical_1d": down,
            "technical_1h": down,
            "technical_15m": down,
            "technical_5m": down,
        }
        result = assess_long_setup(context)
        self.assertFalse(result["review_candidate"])
        self.assertEqual(result["market_regime"], "TREND_DOWN")

    def test_wide_spread_is_rejected(self):
        context = self.valid_context()
        context["spread_percent"] = "0.20"
        result = assess_long_setup(context)
        self.assertFalse(result["review_candidate"])
        self.assertTrue(
            any(x.startswith("SPREAD_TOO_WIDE") for x in result["reasons"])
        )

    def test_low_turnover_is_rejected(self):
        context = self.valid_context()
        context["technical_1d"] = frame(
            ema20="100",
            ema50="95",
            ema50_old="94",
            return20="0.08",
            close="102",
            atr="2",
            avgvol="10000",
            low5="98",
        )
        result = assess_long_setup(context)
        self.assertFalse(result["review_candidate"])
        self.assertTrue(
            any(
                x.startswith("DAILY_TURNOVER_TOO_LOW")
                for x in result["reasons"]
            )
        )


if __name__ == "__main__":
    unittest.main()
