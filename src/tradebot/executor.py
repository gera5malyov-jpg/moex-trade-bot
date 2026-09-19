from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .config import Config
from .mailbox import (
    has_sandbox_ready_marker,
    load_risk_baseline,
    send_lifecycle_state_email,
)
from .protocol import (
    PROTOCOL_VERSION,
    TradeCommand,
    parse_iso_utc,
    utc_now,
    verify_auth_token,
)
from .lifecycle import open_protected_long
from .risk import (
    compute_consecutive_losses,
    compute_daily_pnl_rub,
    validate_buy_hard_risk,
)
from .tinvest import TInvestSandboxClient


# Deliberate hard lock. A BUY must not become executable merely because someone
# flips a repository variable. This will be changed only after the sandbox
# protective-order lifecycle (entry fill -> STOP/TAKE -> sibling cancellation ->
# TIME_STOP) is implemented and tested end-to-end.
PROTECTIVE_ORDER_LIFECYCLE_IMPLEMENTED = True

# Until price semantics and protective lifecycle are independently validated,
# automatic execution is restricted to ordinary cash-market shares and ETFs.
EXECUTION_SUPPORTED_TYPES = {"share", "etf"}


def _quotation_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, dict):
        raise RuntimeError(f"{field} is unavailable")
    result = (
        Decimal(str(value.get("units", "0")))
        + Decimal(str(value.get("nano", 0)))
        / Decimal("1000000000")
    )
    if result <= 0:
        raise RuntimeError(f"{field} must be positive")
    return result


def _assert_tick_aligned(
    *,
    value: Decimal,
    tick: Decimal,
    field: str,
) -> None:
    units = value / tick
    if units != units.to_integral_value():
        raise RuntimeError(
            f"{field}={value} is not aligned to min price increment {tick}"
        )


def _validate_executable_instrument(
    *,
    instrument: dict,
    command: TradeCommand,
) -> None:
    real_exchange = str(
        instrument.get("realExchange")
        or instrument.get("real_exchange")
        or ""
    ).upper()
    if real_exchange != "REAL_EXCHANGE_MOEX":
        raise RuntimeError(
            "Execution refused: instrument is not confirmed REAL_EXCHANGE_MOEX"
        )
    if instrument.get("apiTradeAvailableFlag") is False:
        raise RuntimeError("Execution refused: API trading is unavailable")
    if command.action == "BUY" and instrument.get("buyAvailableFlag") is False:
        raise RuntimeError("Execution refused: BUY is unavailable")

    tick = _quotation_decimal(
        instrument.get("minPriceIncrement")
        or instrument.get("min_price_increment"),
        "min price increment",
    )
    if command.limit_price is not None:
        _assert_tick_aligned(
            value=command.limit_price,
            tick=tick,
            field="limit_price",
        )
    if command.stop_loss is not None:
        _assert_tick_aligned(
            value=command.stop_loss,
            tick=tick,
            field="stop_loss",
        )
    if command.take_profit is not None:
        _assert_tick_aligned(
            value=command.take_profit,
            tick=tick,
            field="take_profit",
        )


def _position_quantity(position: dict) -> Decimal:
    value = position.get("quantity")
    if not isinstance(value, dict):
        return Decimal("0")
    return (
        Decimal(str(value.get("units", "0")))
        + Decimal(str(value.get("nano", 0))) / Decimal("1000000000")
    )


def _validate_sector_concentration(
    *,
    client: TInvestSandboxClient,
    new_instrument: dict,
    portfolio: dict,
    instrument_uid: str,
) -> None:
    new_sector = str(new_instrument.get("sector") or "").strip().lower()

    for position in portfolio.get("positions") or []:
        if _position_quantity(position) <= 0:
            continue
        existing_uid = str(
            position.get("instrumentUid")
            or position.get("instrument_uid")
            or ""
        )
        ticker = str(position.get("ticker") or "").upper()
        class_code = str(
            position.get("classCode") or position.get("class_code") or ""
        ).upper()
        if (
            ticker == "RUB000UTSTOM"
            or (ticker.startswith("RUB") and class_code == "CETS")
        ):
            continue
        if not existing_uid:
            raise RuntimeError(
                "Hard risk: existing position UID is unavailable"
            )
        if existing_uid == instrument_uid:
            raise RuntimeError(
                "Hard risk: duplicate exposure to the same instrument"
            )

        existing = client.find_instrument(existing_uid)
        existing_sector = str(
            existing.get("sector") or ""
        ).strip().lower()
        if not new_sector or not existing_sector:
            raise RuntimeError(
                "Hard risk: sector metadata unavailable for concentration check"
            )
        if new_sector == existing_sector:
            raise RuntimeError(
                "Hard risk: sector concentration blocked (" + new_sector + ")"
            )

def validate_command(command: TradeCommand, config: Config) -> None:
    if command.protocol_version != PROTOCOL_VERSION:
        raise ValueError("Unsupported protocol_version")

    if not verify_auth_token(
        config.hmac_secret,
        signal_id=command.signal_id,
        created_at=command.signal_created_at,
        ticker=command.ticker,
        class_code=command.class_code,
        instrument_uid=command.instrument_uid,
        instrument_type=command.instrument_type,
        execution_capability=command.execution_capability,
        token=command.auth_token,
    ):
        raise ValueError("Invalid auth_token or signal identity was changed")

    now = utc_now()
    signal_time = parse_iso_utc(command.signal_created_at)
    if signal_time > now + timedelta(minutes=2):
        raise ValueError("signal_created_at is in the future")
    if signal_time < now - timedelta(minutes=config.signal_max_age_minutes):
        raise ValueError("Signal is stale")

    # Expiry is an execution-safety boundary for broker actions.
    # A signed SKIP cannot place an order, so it may still be journaled after
    # expiry when GitHub Actions scheduling is delayed.
    if command.action != "SKIP":
        if command.expires_at <= now:
            raise ValueError("Command expired")

        max_future = now + timedelta(minutes=config.command_max_age_minutes)
        if command.expires_at > max_future:
            raise ValueError(
                f"expires_at is too far in future; max "
                f"{config.command_max_age_minutes} minutes"
            )

    if command.action != "SKIP" and not command.execution_capability:
        raise RuntimeError(
            "Signal is analysis-only: execution_capability is false"
        )

    if (
        command.action != "SKIP"
        and command.instrument_type not in EXECUTION_SUPPORTED_TYPES
    ):
        raise RuntimeError(
            f"Automatic execution is not yet supported for "
            f"instrument_type={command.instrument_type!r}"
        )

    if command.action != "SKIP" and not config.trading_enabled:
        raise RuntimeError("TRADING_ENABLED is false")

    if (
        command.action == "BUY"
        and command.time_stop is not None
        and command.time_stop <= now
    ):
        raise RuntimeError("BUY locked: time_stop is not in the future")

    if command.action == "BUY" and not PROTECTIVE_ORDER_LIFECYCLE_IMPLEMENTED:
        raise RuntimeError(
            "BUY locked: protective STOP/TAKE/TIME_STOP lifecycle "
            "is not implemented yet"
        )

    if command.action == "BUY" and not has_sandbox_ready_marker(
        imap_host=config.imap_host,
        user=config.mail_user,
        app_password=config.mail_app_password,
        hmac_secret=config.hmac_secret,
        expected_account_name=config.sandbox_account_name,
    ):
        raise RuntimeError(
            "BUY locked: live Sandbox protective lifecycle smoke test "
            "has not produced TRADE-SANDBOX-READY"
        )


def execute_command(command: TradeCommand, config: Config) -> dict:
    validate_command(command, config)

    if command.action == "SKIP":
        return {
            "status": "skipped",
            "signal_id": command.signal_id,
            "reviewer_note": command.reviewer_note,
        }

    # Standalone SELL is intentionally fail-closed for now. Exits from an
    # opened automated position are owned by the protective lifecycle so that
    # STOP/TAKE orders are cancelled/reconciled before a manual-style exit.
    if command.action == "SELL":
        return {
            "status": "execution_blocked",
            "signal_id": command.signal_id,
            "reason": "STANDALONE_SELL_LIFECYCLE_INTEGRATION_NOT_IMPLEMENTED",
        }

    client = TInvestSandboxClient(
        token=config.tinvest_token,
        account_name=config.sandbox_account_name,
    )
    prepared = client.prepare_limit_order(
        ticker=command.ticker,
        class_code=command.class_code,
        instrument_uid=command.instrument_uid,
        side=command.action,
        quantity_lots=command.quantity_lots,
        limit_price=command.limit_price,
    )

    _validate_executable_instrument(
        instrument=prepared["instrument"],
        command=command,
    )

    hard_risk = None
    if command.action == "BUY":
        lot = int(prepared["instrument"].get("lot") or 0)
        _validate_sector_concentration(
            client=client,
            new_instrument=prepared["instrument"],
            portfolio=prepared["portfolio"],
            instrument_uid=command.instrument_uid,
        )

        now = utc_now()
        trading_date = now.astimezone(
            ZoneInfo("Europe/Moscow")
        ).date().isoformat()
        baseline = load_risk_baseline(
            imap_host=config.imap_host,
            user=config.mail_user,
            app_password=config.mail_app_password,
            trading_date=trading_date,
        )
        baseline_time = parse_iso_utc(
            str(baseline.get("generated_at_utc") or "")
        )
        operations = client.get_operations_by_cursor(
            from_time=baseline_time,
            to_time=now,
        )
        daily_pnl = compute_daily_pnl_rub(
            current_portfolio=prepared["portfolio"],
            baseline_payload=baseline,
            operations_since_baseline=operations,
        )
        consecutive_losses = compute_consecutive_losses(operations)

        hard_risk = validate_buy_hard_risk(
            command=command,
            portfolio=prepared["portfolio"],
            preflight_order_price=prepared["preflight_order_price"],
            instrument_lot=lot,
            min_price_increment=_quotation_decimal(
                prepared["instrument"].get("minPriceIncrement")
                or prepared["instrument"].get("min_price_increment"),
                "min price increment",
            ),
            daily_pnl_rub=daily_pnl,
            consecutive_losses=consecutive_losses,
        )

    if command.action == "BUY":
        lifecycle_state = open_protected_long(
            client=client,
            command=command,
            prepared=prepared,
        )
        send_lifecycle_state_email(
            smtp_host=config.smtp_host,
            user=config.mail_user,
            app_password=config.mail_app_password,
            recipient=config.mail_user,
            signal_id=command.signal_id,
            payload=lifecycle_state,
        )
        broker_result: dict = {
            "lifecycle_state": lifecycle_state,
        }
        status = "protected_lifecycle_started"
    else:
        result = client.post_limit_order(
            ticker=command.ticker,
            class_code=command.class_code,
            instrument_uid=command.instrument_uid,
            side=command.action,
            quantity_lots=command.quantity_lots,
            limit_price=command.limit_price,
            # Exactly one executable broker request ID per signal.
            idempotency_seed=f"moex-trade-bot:signal:{command.signal_id}",
            prepared=prepared,
        )
        broker_result = result
        status = "submitted_to_sandbox"

    return {
        "status": status,
        "signal_id": command.signal_id,
        "action": command.action,
        "instrument_uid": command.instrument_uid,
        "quantity_lots": command.quantity_lots,
        "limit_price": str(command.limit_price),
        "hard_risk": (
            {
                "capital_rub": str(hard_risk.capital_rub),
                "daily_yield_rub": str(hard_risk.daily_yield_rub),
                "position_value_rub": str(hard_risk.position_value_rub),
                "max_loss_rub": str(hard_risk.max_loss_rub),
                "risk_budget_rub": str(hard_risk.risk_budget_rub),
                "position_cap_rub": str(hard_risk.position_cap_rub),
                "open_positions": hard_risk.open_positions,
                "net_risk_rub": str(hard_risk.net_risk_rub),
                "net_reward_rub": str(hard_risk.net_reward_rub),
                "risk_reward_net": str(hard_risk.risk_reward_net),
            }
            if hard_risk is not None
            else None
        ),
        "broker_response": broker_result,
    }
