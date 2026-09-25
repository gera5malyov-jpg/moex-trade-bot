import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradebot.protocol import (
    PROTOCOL_VERSION,
    PROTECTIVE_LIFECYCLE_VERSION,
    SANDBOX_READY_VERSION,
    TradeCommand,
    make_auth_token,
    make_internal_journal_token,
    make_sandbox_readiness_token,
    quotation_from_decimal,
    verify_auth_token,
    verify_internal_journal_token,
    verify_sandbox_readiness_payload,
)


class ProtocolTests(unittest.TestCase):
    def _identity(self):
        return {
            "signal_id": "0f154e3a-8112-4aa7-82ba-3c9a98ff0f4f",
            "created_at": "2026-09-19T17:00:00+00:00",
            "ticker": "SBER",
            "class_code": "TQBR",
            "instrument_uid": "uid-sber",
            "instrument_type": "share",
            "execution_capability": False,
        }

    def test_hmac_binds_signal_identity(self):
        secret = "x" * 32
        identity = self._identity()
        token = make_auth_token(secret, **identity)

        self.assertTrue(
            verify_auth_token(secret, token=token, **identity)
        )

        tampered = dict(identity)
        tampered["ticker"] = "GAZP"
        self.assertFalse(
            verify_auth_token(secret, token=token, **tampered)
        )

        tampered = dict(identity)
        tampered["execution_capability"] = True
        self.assertFalse(
            verify_auth_token(secret, token=token, **tampered)
        )

    def test_signed_sandbox_readiness_rejects_tampering(self):
        secret = "r" * 32
        verified_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "ready_version": SANDBOX_READY_VERSION,
            "environment": "TINVEST_SANDBOX",
            "verified_at_utc": verified_at,
            "sandbox_account_name": "github-moex-trade-bot",
            "sandbox_account_id": "sandbox-account-id",
            "lifecycle_version": PROTECTIVE_LIFECYCLE_VERSION,
            "instrument": "SBER_TQBR",
            "entry_protected": True,
            "stop_loss_active_verified": True,
            "take_profit_active_verified": True,
            "time_stop_force_exit_verified": True,
            "position_after_cleanup_lots": 0,
        }
        payload["readiness_token"] = make_sandbox_readiness_token(
            secret,
            verified_at_utc=verified_at,
            sandbox_account_name="github-moex-trade-bot",
            sandbox_account_id="sandbox-account-id",
            lifecycle_version=PROTECTIVE_LIFECYCLE_VERSION,
            instrument="SBER_TQBR",
        )
        self.assertTrue(
            verify_sandbox_readiness_payload(
                secret,
                payload,
                expected_account_name="github-moex-trade-bot",
                expected_account_id="sandbox-account-id",
            )
        )

        tampered = dict(payload)
        tampered["position_after_cleanup_lots"] = 1
        self.assertFalse(
            verify_sandbox_readiness_payload(
                secret,
                tampered,
                expected_account_name="github-moex-trade-bot",
                expected_account_id="sandbox-account-id",
            )
        )

        tampered = dict(payload)
        tampered["stop_loss_active_verified"] = False
        self.assertFalse(
            verify_sandbox_readiness_payload(
                secret,
                tampered,
                expected_account_name="github-moex-trade-bot",
                expected_account_id="sandbox-account-id",
            )
        )

    def test_internal_journal_hmac_rejects_tampering(self):
        secret = "j" * 32
        payload = {
            "environment": "TINVEST_SANDBOX",
            "signal_id": "abc",
            "status": "PROTECTED",
        }
        payload["journal_token"] = make_internal_journal_token(
            secret,
            purpose="lifecycle_state",
            payload=payload,
        )
        self.assertTrue(
            verify_internal_journal_token(
                secret,
                purpose="lifecycle_state",
                payload=payload,
            )
        )
        tampered = dict(payload)
        tampered["status"] = "CLOSED_POSITION_GONE"
        self.assertFalse(
            verify_internal_journal_token(
                secret,
                purpose="lifecycle_state",
                payload=tampered,
            )
        )

    def test_quotation(self):
        self.assertEqual(
            quotation_from_decimal(Decimal("300.125")),
            {"units": "300", "nano": 125000000},
        )

    def _buy_command(self):
        now = datetime.now(timezone.utc)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "signal_id": "0f154e3a-8112-4aa7-82ba-3c9a98ff0f4f",
            "signal_created_at": now.isoformat(),
            "auth_token": "x",
            "action": "BUY",
            "ticker": "SBER",
            "class_code": "TQBR",
            "instrument_id": "SBER_TQBR",
            "instrument_uid": "uid-sber",
            "instrument_type": "share",
            "execution_capability": False,
            "order_type": "LIMIT",
            "quantity_lots": 1,
            "limit_price": "300",
            "stop_loss": "295",
            "take_profit": "312",
            "time_stop": (now + timedelta(hours=2)).isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "market_regime": "TREND_UP",
            "benchmark_check": "INSUFFICIENT_HISTORY",
            "data_completeness": "FULL",
            "counter_argument": "TEST_COUNTER_ARGUMENT",
            "why_counter_argument_does_not_invalidate": "TEST_REBUTTAL",
        }

    def test_command_rejects_market(self):
        data = self._buy_command()
        data["order_type"] = "MARKET"
        with self.assertRaises(ValueError):
            TradeCommand.from_dict(data)

    def test_command_rejects_instrument_id_mismatch(self):
        data = self._buy_command()
        data["instrument_id"] = "GAZP_TQBR"
        with self.assertRaises(ValueError):
            TradeCommand.from_dict(data)

    def test_buy_requires_protective_plan(self):
        data = self._buy_command()
        del data["stop_loss"]
        with self.assertRaises(ValueError):
            TradeCommand.from_dict(data)

    def test_buy_requires_long_price_ordering(self):
        data = self._buy_command()
        data["stop_loss"] = "301"
        with self.assertRaises(ValueError):
            TradeCommand.from_dict(data)

    def test_buy_rejects_partial_data(self):
        data = self._buy_command()
        data["data_completeness"] = "PARTIAL"
        with self.assertRaises(ValueError):
            TradeCommand.from_dict(data)

    def test_buy_rejects_bad_market_regime(self):
        data = self._buy_command()
        data["market_regime"] = "PANIC"
        with self.assertRaises(ValueError):
            TradeCommand.from_dict(data)

    def test_buy_rejects_event_risk_regime(self):
        data = self._buy_command()
        data["market_regime"] = "EVENT_RISK"
        with self.assertRaises(ValueError):
            TradeCommand.from_dict(data)

    def test_skip_requires_zero_quantity(self):
        data = self._buy_command()
        data.update(
            {
                "action": "SKIP",
                "quantity_lots": 1,
            }
        )
        data.pop("limit_price")
        data.pop("stop_loss")
        data.pop("take_profit")
        data.pop("time_stop")
        with self.assertRaises(ValueError):
            TradeCommand.from_dict(data)


if __name__ == "__main__":
    unittest.main()
