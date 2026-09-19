from __future__ import annotations

from datetime import timedelta

from .config import Config
from .protocol import TradeCommand, utc_now, verify_auth_token
from .tinvest import TInvestSandboxClient


def validate_command(command: TradeCommand, config: Config) -> None:
    if command.protocol_version != "1":
        raise ValueError("Unsupported protocol_version")

    if not verify_auth_token(
        config.hmac_secret, command.signal_id, command.auth_token
    ):
        raise ValueError("Invalid auth_token")

    now = utc_now()
    if command.expires_at <= now:
        raise ValueError("Command expired")

    max_future = now + timedelta(minutes=config.command_max_age_minutes)
    if command.expires_at > max_future:
        raise ValueError(
            f"expires_at is too far in future; max {config.command_max_age_minutes} minutes"
        )

    if command.action != "SKIP" and not config.trading_enabled:
        raise RuntimeError("TRADING_ENABLED is false")


def execute_command(command: TradeCommand, config: Config) -> dict:
    validate_command(command, config)

    if command.action == "SKIP":
        return {
            "status": "skipped",
            "signal_id": command.signal_id,
            "reviewer_note": command.reviewer_note,
        }

    client = TInvestSandboxClient(
        token=config.tinvest_token,
        account_id=config.sandbox_account_id,
    )
    result = client.post_limit_order(
        instrument_id=command.instrument_id,
        side=command.action,
        quantity_lots=command.quantity_lots,
        limit_price=command.limit_price,
        idempotency_seed=(
            f"{command.signal_id}:{command.action}:"
            f"{command.quantity_lots}:{command.limit_price}"
        ),
    )
    return {
        "status": "submitted_to_sandbox",
        "signal_id": command.signal_id,
        "action": command.action,
        "instrument_id": command.instrument_id,
        "quantity_lots": command.quantity_lots,
        "limit_price": str(command.limit_price),
        "broker_response": result,
    }
