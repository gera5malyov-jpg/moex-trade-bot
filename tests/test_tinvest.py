import unittest
from decimal import Decimal

from tradebot.tinvest import TInvestSandboxClient


class TInvestPayloadTests(unittest.TestCase):
    def client(self):
        client = object.__new__(TInvestSandboxClient)
        client.account_id = "sandbox-account-id"
        client.calls = []

        def fake_post(url, payload):
            client.calls.append((url, payload))
            if url.endswith("/PostSandboxStopOrder"):
                return {"stopOrderId": "stop-id"}
            if url.endswith("/PostSandboxOrder"):
                return {
                    "orderId": "exchange-order-id",
                    "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
                    "lotsExecuted": "1",
                }
            raise AssertionError(f"Unexpected endpoint: {url}")

        client._post = fake_post
        return client

    def test_take_profit_creates_limit_child(self):
        client = self.client()
        client.post_stop_order(
            instrument_uid="uid",
            quantity_lots=1,
            stop_price=Decimal("310"),
            stop_order_type="STOP_ORDER_TYPE_TAKE_PROFIT",
            idempotency_seed="tp",
        )
        payload = client.calls[-1][1]
        self.assertEqual(
            payload["exchangeOrderType"],
            "EXCHANGE_ORDER_TYPE_LIMIT",
        )
        self.assertEqual(payload["price"], payload["stopPrice"])
        self.assertFalse(payload["confirmMarginTrade"])

    def test_stop_loss_uses_native_broker_stop_semantics(self):
        client = self.client()
        client.post_stop_order(
            instrument_uid="uid",
            quantity_lots=1,
            stop_price=Decimal("295"),
            stop_order_type="STOP_ORDER_TYPE_STOP_LOSS",
            idempotency_seed="sl",
        )
        payload = client.calls[-1][1]
        self.assertNotIn("exchangeOrderType", payload)
        self.assertEqual(payload["price"], payload["stopPrice"])
        self.assertFalse(payload["confirmMarginTrade"])

    def test_entry_is_limit_fill_and_kill_without_margin(self):
        client = self.client()
        prepared = {
            "instrument": {
                "ticker": "SBER",
                "classCode": "TQBR",
                "instrumentType": "share",
            },
            "limits": {},
            "preflight_order_price": {},
            "portfolio": {},
        }
        client.post_limit_order(
            ticker="SBER",
            class_code="TQBR",
            instrument_uid="uid",
            side="BUY",
            quantity_lots=1,
            limit_price=Decimal("300"),
            idempotency_seed="entry",
            prepared=prepared,
        )
        payload = client.calls[-1][1]
        self.assertEqual(payload["orderType"], "ORDER_TYPE_LIMIT")
        self.assertEqual(
            payload["timeInForce"],
            "TIME_IN_FORCE_FILL_AND_KILL",
        )
        self.assertFalse(payload["confirmMarginTrade"])


if __name__ == "__main__":
    unittest.main()
