import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradebot.protocol import TradeCommand
from tradebot.risk import validate_buy_hard_risk


def money(value: str):
    d = Decimal(value)
    units = int(d)
    nano = int((d - Decimal(units)) * Decimal("1000000000"))
    return {"currency": "rub", "units": str(units), "nano": nano}


class RiskTests(unittest.TestCase):
    def command(self):
        now = datetime.now(timezone.utc)
        return TradeCommand.from_dict(
            {
                "protocol_version": "2",
                "signal_id": "0f154e3a-8112-4aa7-82ba-3c9a98ff0f4f",
                "signal_created_at": now.isoformat(),
                "auth_token": "x",
                "action": "BUY",
                "ticker": "SBER",
                "class_code": "TQBR",
                "instrument_id": "SBER_TQBR",
                "instrument_uid": "uid-sber",
                "instrument_type": "share",
                "execution_capability": True,
                "order_type": "LIMIT",
                "quantity_lots": 1,
                "limit_price": "300",
                "stop_loss": "297",
                "take_profit": "307",
                "time_stop": (
                    now + timedelta(hours=2)
                ).isoformat(),
                "expires_at": (
                    now + timedelta(minutes=5)
                ).isoformat(),
            }
        )

    def test_accepts_small_risk(self):
        check = validate_buy_hard_risk(
            command=self.command(),
            portfolio={
                "totalAmountPortfolio": money("1000000"),
                "dailyYield": money("0"),
                "positions": [],
            },
            preflight_order_price={
                "initialOrderAmount": money("3000"),
                "totalOrderAmount": money("3001.5"),
                "executedCommissionRub": money("1.5"),
            },
            instrument_lot=10,
        )
        self.assertLessEqual(
            check.max_loss_rub,
            check.risk_budget_rub,
        )

    def test_rejects_position_over_ten_percent(self):
        with self.assertRaises(RuntimeError):
            validate_buy_hard_risk(
                command=self.command(),
                portfolio={
                    "totalAmountPortfolio": money("10000"),
                    "dailyYield": money("0"),
                    "positions": [],
                },
                preflight_order_price={
                    "initialOrderAmount": money("3000"),
                    "totalOrderAmount": money("3001.5"),
                    "executedCommissionRub": money("1.5"),
                },
                instrument_lot=10,
            )

    def test_rejects_daily_stop(self):
        with self.assertRaises(RuntimeError):
            validate_buy_hard_risk(
                command=self.command(),
                portfolio={
                    "totalAmountPortfolio": money("1000000"),
                    "dailyYield": money("-8000"),
                    "positions": [],
                },
                preflight_order_price={
                    "initialOrderAmount": money("3000"),
                    "totalOrderAmount": money("3001.5"),
                    "executedCommissionRub": money("1.5"),
                },
                instrument_lot=10,
            )


if __name__ == "__main__":
    unittest.main()
