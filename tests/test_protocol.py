import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from tradebot.protocol import (
    TradeCommand,
    make_auth_token,
    verify_auth_token,
    quotation_from_decimal,
)


class ProtocolTests(unittest.TestCase):
    def test_hmac(self):
        secret = "x" * 32
        sid = "0f154e3a-8112-4aa7-82ba-3c9a98ff0f4f"
        token = make_auth_token(secret, sid)
        self.assertTrue(verify_auth_token(secret, sid, token))
        self.assertFalse(verify_auth_token(secret, sid, "bad"))

    def test_quotation(self):
        self.assertEqual(
            quotation_from_decimal(Decimal("300.125")),
            {"units": "300", "nano": 125000000},
        )

    def test_command_rejects_market(self):
        expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        with self.assertRaises(ValueError):
            TradeCommand.from_dict({
                "protocol_version": "1",
                "signal_id": "0f154e3a-8112-4aa7-82ba-3c9a98ff0f4f",
                "auth_token": "x",
                "action": "BUY",
                "ticker": "SBER",
                "class_code": "TQBR",
                "instrument_id": "SBER_TQBR",
                "order_type": "MARKET",
                "quantity_lots": 1,
                "limit_price": "300",
                "expires_at": expires,
            })


if __name__ == "__main__":
    unittest.main()
