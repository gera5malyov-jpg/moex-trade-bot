import unittest
from decimal import Decimal

from tradebot.scanner import atr, ema, rsi, vwap


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


if __name__ == "__main__":
    unittest.main()
