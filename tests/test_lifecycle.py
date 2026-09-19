import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradebot.lifecycle import (
    monitor_lifecycle_state,
    open_protected_long,
)
from tradebot.protocol import TradeCommand


class FakeClient:
    def __init__(self, fail_take=False):
        self.fail_take = fail_take
        self.position_lots = 1
        self.cancelled = []
        self.stop_calls = []
        self.stop_orders = []
        self.child_state = {
            "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
        }

    def post_limit_order(self, **kwargs):
        side = kwargs["side"]
        if side == "BUY":
            return {
                "request_order_id": "entry-request",
                "post_order": {
                    "orderId": "entry-exchange",
                    "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
                    "lotsExecuted": "1",
                },
            }
        self.position_lots = 0
        return {
            "request_order_id": "exit-request",
            "post_order": {
                "orderId": "exit-exchange",
                "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
                "lotsExecuted": str(kwargs["quantity_lots"]),
            },
        }

    def post_stop_order(self, **kwargs):
        self.stop_calls.append(kwargs)
        kind = kwargs["stop_order_type"]
        if kind == "STOP_ORDER_TYPE_TAKE_PROFIT" and self.fail_take:
            raise RuntimeError("simulated take-profit failure")
        stop_id = (
            "stop-loss-id"
            if kind == "STOP_ORDER_TYPE_STOP_LOSS"
            else "take-profit-id"
        )
        return {
            "request_order_id": stop_id + "-request",
            "stop_order": {"stopOrderId": stop_id},
        }

    def cancel_stop_order(self, stop_order_id):
        self.cancelled.append(stop_order_id)
        return {"time": datetime.now(timezone.utc).isoformat()}

    def get_position_lots(self, **kwargs):
        return self.position_lots

    def get_order_book(self, instrument_uid, depth):
        return {
            "bids": [{"price": {"units": "299", "nano": 0}}],
            "asks": [{"price": {"units": "300", "nano": 0}}],
        }

    def get_stop_orders(self, **kwargs):
        return list(self.stop_orders)

    def get_order_state(self, order_id):
        return dict(self.child_state)


class LifecycleTests(unittest.TestCase):
    def command(self, time_stop=None):
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
                    time_stop or (now + timedelta(hours=2))
                ).isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
            }
        )

    def prepared(self):
        return {
            "instrument": {"lot": 10},
            "limits": {},
            "preflight_order_price": {},
            "portfolio": {},
        }

    def test_entry_places_protection_for_filled_lots(self):
        client = FakeClient()
        state = open_protected_long(
            client=client,
            command=self.command(),
            prepared=self.prepared(),
        )
        self.assertEqual(state["status"], "PROTECTED")
        self.assertEqual(state["filled_lots"], 1)
        self.assertEqual(state["stop_order_id"], "stop-loss-id")
        self.assertEqual(state["take_order_id"], "take-profit-id")
        self.assertEqual(len(client.stop_calls), 2)
        self.assertTrue(
            all(call["quantity_lots"] == 1 for call in client.stop_calls)
        )

    def test_failed_second_protection_forces_exit(self):
        client = FakeClient(fail_take=True)
        state = open_protected_long(
            client=client,
            command=self.command(),
            prepared=self.prepared(),
        )
        self.assertEqual(state["status"], "CLOSED_FORCE_EXIT")
        self.assertEqual(client.position_lots, 0)
        self.assertIn("stop-loss-id", client.cancelled)

    def test_stop_fill_cancels_take_and_closes(self):
        client = FakeClient()
        state = open_protected_long(
            client=client,
            command=self.command(),
            prepared=self.prepared(),
        )
        client.stop_orders = [
            {
                "stopOrderId": "stop-loss-id",
                "status": "STOP_ORDER_STATUS_EXECUTED",
                "exchangeOrderId": "child-stop-order",
            },
            {
                "stopOrderId": "take-profit-id",
                "status": "STOP_ORDER_STATUS_ACTIVE",
            },
        ]
        updated = monitor_lifecycle_state(
            client=client,
            state=state,
        )
        self.assertEqual(updated["status"], "CLOSED_STOP_LOSS")
        self.assertIn("take-profit-id", client.cancelled)

    def test_time_stop_forces_exit(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        client = FakeClient()
        state = open_protected_long(
            client=client,
            command=self.command(time_stop=past),
            prepared=self.prepared(),
        )
        updated = monitor_lifecycle_state(
            client=client,
            state=state,
            now=datetime.now(timezone.utc),
        )
        self.assertEqual(updated["status"], "CLOSED_TIME_STOP")
        self.assertEqual(client.position_lots, 0)
        self.assertIn("stop-loss-id", client.cancelled)
        self.assertIn("take-profit-id", client.cancelled)


if __name__ == "__main__":
    unittest.main()
