"""Unit tests for read-only Trader Skill activity summaries."""

import unittest

from tradebot.trader_skill_metrics import summarize_daily_trade_activity


def op(op_id, kind, units, nano, state="OPERATION_STATE_EXECUTED", currency="rub"):
    return {
        "id": op_id,
        "type": kind,
        "state": state,
        "payment": {"currency": currency, "units": str(units), "nano": nano},
        "commission": {"currency": "rub", "units": "0", "nano": -147850000},
    }


class TraderSkillMetricsTests(unittest.TestCase):
    def test_smoke_round_trip_is_not_classified_as_strategy_profit(self):
        records = [
            op("buy", "OPERATION_TYPE_BUY", -295, -700000000),
            op("buyfee", "OPERATION_TYPE_BROKER_FEE", 0, -147850000),
            op("sell", "OPERATION_TYPE_SELL", 295, 690000000),
            op("sellfee", "OPERATION_TYPE_BROKER_FEE", 0, -147845000),
        ]
        result = summarize_daily_trade_activity({"items": records})
        self.assertEqual(result["executed_buys"], 1)
        self.assertEqual(result["executed_sells"], 1)
        self.assertEqual(result["explicit_broker_fee_cashflow_rub"], "-0.295695")
        self.assertEqual(result["net_execution_cashflow_rub"], "-0.305695")
        self.assertIsNone(result["strategy_trade_count"])
        self.assertIsNone(result["strategy_realized_pnl_rub"])
        self.assertIn("UNCLASSIFIED", result["classification"])

    def test_empty_day_is_not_called_profitable(self):
        result = summarize_daily_trade_activity({"items": []})
        self.assertEqual(result["classification"], "NO_EXECUTED_TRADES")
        self.assertEqual(result["executed_buys"], 0)
        self.assertIsNone(result["strategy_realized_pnl_rub"])

    def test_duplicate_operation_id_is_not_double_counted(self):
        record = op("fee", "OPERATION_TYPE_BROKER_FEE", 0, -200000000)
        result = summarize_daily_trade_activity({"items": [record, record]})
        self.assertEqual(result["explicit_broker_fee_operations"], 1)
        self.assertEqual(result["explicit_broker_fee_cashflow_rub"], "-0.2")

    def test_pending_or_non_rub_operations_are_not_counted_as_pnl(self):
        result = summarize_daily_trade_activity({"items": [
            op("pending", "OPERATION_TYPE_BUY", -100, 0, "OPERATION_STATE_PROGRESS"),
            op("usd", "OPERATION_TYPE_SELL", 50, 0, currency="usd"),
        ]})
        self.assertEqual(result["executed_buys"], 0)
        self.assertEqual(result["executed_sells"], 1)
        self.assertEqual(result["missing_rub_payment_count"], 1)
        self.assertIsNone(result["net_execution_cashflow_rub"])

    def test_unexpected_api_response_is_fail_safe(self):
        self.assertEqual(
            summarize_daily_trade_activity({"items": {"bad": "shape"}})["executed_buys"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
