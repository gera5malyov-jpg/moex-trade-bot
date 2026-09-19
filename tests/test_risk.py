import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradebot.protocol import TradeCommand
from tradebot.risk import (
    compute_consecutive_losses,
    compute_daily_pnl_rub,
    validate_buy_hard_risk,
)


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
            min_price_increment=Decimal("0.01"),
            consecutive_losses=0,
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
            min_price_increment=Decimal("0.01"),
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
            min_price_increment=Decimal("0.01"),
            )

    def test_computes_daily_pnl_from_equity_baseline(self):
        pnl = compute_daily_pnl_rub(
            current_portfolio={
                "totalAmountPortfolio": money("1001200"),
            },
            baseline_payload={
                "environment": "TINVEST_SANDBOX",
                "generated_at_utc": "2026-09-19T03:00:00+00:00",
                "portfolio": {
                    "totalAmountPortfolio": money("1000000"),
                },
            },
            operations_since_baseline={"items": []},
        )
        self.assertEqual(pnl, Decimal("1200"))

    def test_baseline_pnl_rejects_funding_after_baseline(self):
        with self.assertRaises(RuntimeError):
            compute_daily_pnl_rub(
                current_portfolio={
                    "totalAmountPortfolio": money("1001200"),
                },
                baseline_payload={
                    "environment": "TINVEST_SANDBOX",
                    "generated_at_utc": "2026-09-19T03:00:00+00:00",
                    "portfolio": {
                        "totalAmountPortfolio": money("1000000"),
                    },
                },
                operations_since_baseline={
                    "items": [{"type": "OPERATION_TYPE_INPUT"}],
                },
            )

    def test_consecutive_loss_counter(self):
        operations = {
            "items": [
                {"type": "OPERATION_TYPE_SELL", "date": "2026-09-19T10:00:00Z", "yield": money("10")},
                {"type": "OPERATION_TYPE_SELL", "date": "2026-09-19T11:00:00Z", "yield": money("-5")},
                {"type": "OPERATION_TYPE_SELL", "date": "2026-09-19T12:00:00Z", "yield": money("-7")},
            ]
        }
        self.assertEqual(compute_consecutive_losses(operations), 2)


if __name__ == "__main__":
    unittest.main()
