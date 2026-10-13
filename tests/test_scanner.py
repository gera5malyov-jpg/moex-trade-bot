import unittest
from datetime import datetime, timezone
from decimal import Decimal

from tradebot.scanner import _candle_summary, _eligible, atr, ema, rsi, vwap, scan_candidates


def q(value: str):
    d = Decimal(value)
    units = int(d)
    nano = int((d - Decimal(units)) * Decimal("1000000000"))
    return {"units": str(units), "nano": nano}


class ScannerMathTests(unittest.TestCase):
    def test_ema(self):
        values = [Decimal(i) for i in range(1, 30)]
        self.assertIsNotNone(ema(values, 9))
        self.assertIsNotNone(ema(values, 21))

    def test_rsi_rising_is_high(self):
        values = [Decimal(i) for i in range(1, 30)]
        self.assertEqual(rsi(values, 14), Decimal("100"))

    def test_atr_and_vwap(self):
        candles = []
        for i in range(1, 25):
            candles.append(
                {
                    "high": q(str(i + 1)),
                    "low": q(str(i)),
                    "close": q(str(i) + ".5"),
                    "volume": "10",
                    "isComplete": True,
                }
            )
        self.assertIsNotNone(atr(candles, 14))
        self.assertIsNotNone(vwap(candles))

    def test_candle_summary_contains_v2_trend_fields(self):
        candles = []
        for i in range(1, 70):
            candles.append(
                {
                    "open": q(str(i)),
                    "high": q(str(i + 1)),
                    "low": q(str(max(i - 1, 1))),
                    "close": q(str(i) + ".5"),
                    "volume": "1000",
                    "isComplete": True,
                }
            )
        summary = _candle_summary(candles)
        self.assertEqual(summary["candles_count"], 69)
        self.assertIsNotNone(summary["ema20"])
        self.assertIsNotNone(summary["ema50"])
        self.assertIsNotNone(summary["ema50_10_ago"])
        self.assertIsNotNone(summary["return_20"])
        self.assertIsNotNone(summary["recent_high_5"])
        self.assertIsNotNone(summary["recent_low_5"])
        self.assertIsNotNone(summary["last_low"])

    def test_exchange_candidates_are_moex_only(self):
        base = {
            "uid": "uid",
            "ticker": "TEST",
            "classCode": "TQBR",
            "apiTradeAvailableFlag": True,
            "buyAvailableFlag": True,
            "forQualInvestorFlag": False,
        }
        moex = dict(base, realExchange="REAL_EXCHANGE_MOEX")
        rts = dict(base, realExchange="REAL_EXCHANGE_RTS")
        self.assertTrue(_eligible(moex, "share"))
        self.assertFalse(_eligible(rts, "share"))

    def test_balanced_share_watchlist_keeps_mild_pullbacks(self):
        class Client:
            def __init__(self):
                prices = {
                    "UP5": "105", "UP2": "102", "UP1": "101",
                    "DOWN0": "99.9", "DOWN1": "99", "DOWN5": "95",
                }
                self.instruments = [
                    {
                        "uid": name, "ticker": name, "classCode": "TQBR",
                        "realExchange": "REAL_EXCHANGE_MOEX",
                        "apiTradeAvailableFlag": True, "buyAvailableFlag": True,
                        "forQualInvestorFlag": False, "liquidityFlag": True,
                    }
                    for name in prices
                ]
                self.prices = prices

            def get_last_prices(self, instruments):
                stamp = datetime.now(timezone.utc).isoformat()
                return [
                    {
                        "instrumentUid": uid,
                        "price": q(price),
                        "time": stamp,
                    }
                    for uid, price in self.prices.items()
                ]

            def get_close_prices(self, instruments):
                return [
                    {"instrumentUid": uid, "price": q("100")}
                    for uid in self.prices
                ]

            def list_instruments(self, instrument_type):
                self.assert_type = instrument_type
                return self.instruments

        items = scan_candidates(
            Client(), max_per_type=4, instrument_types=("share",),
        )
        tickers = {item["ticker"] for item in items}
        self.assertEqual(tickers, {"UP5", "UP2", "DOWN0", "DOWN1"})
        self.assertEqual(len(items), 4)

    def test_dfa_remains_special_analysis_only_universe(self):
        dfa = {
            "uid": "dfa-uid",
            "ticker": "DFA",
            "classCode": "DFA",
            "apiTradeAvailableFlag": True,
            "buyAvailableFlag": True,
            "forQualInvestorFlag": False,
            "realExchange": "REAL_EXCHANGE_DEALER",
        }
        self.assertTrue(_eligible(dfa, "dfa"))


if __name__ == "__main__":
    unittest.main()
