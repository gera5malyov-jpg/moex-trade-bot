import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradebot.protocol import TradeCommand
from tradebot.risk import (
    compute_consecutive_losses,
    compute_consecutive_losses_from_lifecycle,
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
                "take_profit": "308",
                "time_stop": (
                    now + timedelta(hours=2)
                ).isoformat(),
                "expires_at": (
                    now + timedelta(minutes=5)
                ).isoformat(),
                "market_regime": "TREND_UP",
                "benchmark_check": "INSUFFICIENT_HISTORY",
                "data_completeness": "FULL",
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
            market_spread_per_unit=Decimal("0.10"),
            weekly_pnl_rub=Decimal("0"),
            monthly_pnl_rub=Decimal("0"),
            week_start_equity_rub=Decimal("1000000"),
            month_start_equity_rub=Decimal("1000000"),
            high_water_mark_rub=Decimal("1000000"),
            consecutive_losses=0,
        )
        self.assertLessEqual(
            check.max_loss_rub,
            check.risk_budget_rub,
        )

    def test_rejects_net_risk_reward_below_two(self):
        data = self.command().to_dict() if hasattr(self.command(), "to_dict") else None
        command = self.command()
        payload = {
            "protocol_version": command.protocol_version,
            "signal_id": command.signal_id,
            "signal_created_at": command.signal_created_at,
            "auth_token": command.auth_token,
            "action": command.action,
            "ticker": command.ticker,
            "class_code": command.class_code,
            "instrument_id": command.instrument_id,
            "instrument_uid": command.instrument_uid,
            "instrument_type": command.instrument_type,
            "execution_capability": command.execution_capability,
            "order_type": command.order_type,
            "quantity_lots": command.quantity_lots,
            "limit_price": str(command.limit_price),
            "stop_loss": str(command.stop_loss),
            "take_profit": "304",
            "time_stop": command.time_stop.isoformat(),
            "expires_at": command.expires_at.isoformat(),
        }
        weak = TradeCommand.from_dict(payload)
        with self.assertRaises(RuntimeError):
            validate_buy_hard_risk(
                command=weak,
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
            market_spread_per_unit=Decimal("0.10"),
            weekly_pnl_rub=Decimal("0"),
            monthly_pnl_rub=Decimal("0"),
            week_start_equity_rub=Decimal("1000000"),
            month_start_equity_rub=Decimal("1000000"),
            high_water_mark_rub=Decimal("1000000"),
            consecutive_losses=0,
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
            market_spread_per_unit=Decimal("0.10"),
            weekly_pnl_rub=Decimal("0"),
            monthly_pnl_rub=Decimal("0"),
            week_start_equity_rub=Decimal("1000000"),
            month_start_equity_rub=Decimal("1000000"),
            high_water_mark_rub=Decimal("1000000"),
            consecutive_losses=0,
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
            market_spread_per_unit=Decimal("0.10"),
            weekly_pnl_rub=Decimal("0"),
            monthly_pnl_rub=Decimal("0"),
            week_start_equity_rub=Decimal("1000000"),
            month_start_equity_rub=Decimal("1000000"),
            high_water_mark_rub=Decimal("1000000"),
            consecutive_losses=0,
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

    def test_lifecycle_consecutive_loss_counter(self):
        states = [
            {
                "lifecycle_kind": "SMOKE_TEST",
                "status": "CLOSED_TIME_STOP",
                "updated_at": "2026-09-19T09:00:00+00:00",
                "realized_pnl_rub": "-100",
            },
            {
                "lifecycle_kind": "STRATEGY",
                "status": "CLOSED_TAKE_PROFIT",
                "updated_at": "2026-09-19T10:00:00+00:00",
                "realized_pnl_rub": "10",
            },
            {
                "lifecycle_kind": "STRATEGY",
                "status": "CLOSED_STOP_LOSS",
                "updated_at": "2026-09-19T11:00:00+00:00",
                "realized_pnl_rub": "-5",
            },
            {
                "lifecycle_kind": "STRATEGY",
                "status": "CLOSED_STOP_LOSS",
                "updated_at": "2026-09-19T12:00:00+00:00",
                "realized_pnl_rub": "-7",
            },
        ]
        self.assertEqual(
            compute_consecutive_losses_from_lifecycle(
                states,
                since_utc="2026-09-19T03:00:00+00:00",
            ),
            2,
        )

    def test_lifecycle_loss_counter_fails_closed_without_pnl(self):
        states = [
            {
                "lifecycle_kind": "STRATEGY",
                "status": "CLOSED_POSITION_GONE",
                "updated_at": "2026-09-19T12:00:00+00:00",
            }
        ]
        with self.assertRaises(RuntimeError):
            compute_consecutive_losses_from_lifecycle(
                states,
                since_utc="2026-09-19T03:00:00+00:00",
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
