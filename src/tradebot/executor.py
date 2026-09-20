from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .config import Config
from .mailbox import (
    has_sandbox_ready_marker,
    load_latest_lifecycle_states,
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
    compute_consecutive_losses_from_lifecycle,
    compute_daily_pnl_rub,
    compute_period_pnl_rub,
    latest_strategy_close_time,
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
    if instrument.get("apiTradeAvailableFlag") is not True:
        raise RuntimeError("Execution refused: API trading is unavailable")
    if command.action == "BUY" and instrument.get("buyAvailableFlag") is not True:
        raise RuntimeError("Execution refused: BUY is unavailable")
    if instrument.get("liquidityFlag") is not True:
        raise RuntimeError("Execution refused: instrument liquidity flag is not true")
    if instrument.get("blockedTcaFlag") is True:
        raise RuntimeError("Execution refused: instrument is TCA-blocked")
    if instrument.get("forQualInvestorFlag") is True:
        raise RuntimeError("Execution refused: qualified-only instrument")
    required_tests = (
        instrument.get("requiredTests")
        or instrument.get("required_tests")
        or []
    )
    if required_tests:
        raise RuntimeError("Execution refused: investor tests are required")
    actual_type = str(
        instrument.get("instrumentType")
        or instrument.get("instrument_type")
        or ""
    ).lower()
    if actual_type != command.instrument_type:
        raise RuntimeError(
            "Execution refused: resolved instrument type mismatch"
        )
    currency = str(instrument.get("currency") or "").lower()
    if currency != "rub":
        raise RuntimeError(
            "Execution refused: automatic share/ETF execution is RUB-only"
        )

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


def _live_execution_market(
    *,
    client: TInvestSandboxClient,
    instrument_uid: str,
    limit_price: Decimal,
    quantity_lots: int,
) -> Decimal:
    book = client.get_order_book(instrument_uid, depth=10)
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        raise RuntimeError("Execution refused: live order book is empty")

    best_bid = _quotation_decimal(bids[0].get("price"), "best bid")
    best_ask = _quotation_decimal(asks[0].get("price"), "best ask")
    if best_ask < best_bid:
        raise RuntimeError("Execution refused: crossed/invalid order book")

    orderbook_ts = str(
        book.get("orderbookTs")
        or book.get("orderbook_ts")
        or ""
    )
    if not orderbook_ts:
        raise RuntimeError("Execution refused: order book timestamp unavailable")
    ts = parse_iso_utc(orderbook_ts)
    age_seconds = (utc_now() - ts).total_seconds()
    if age_seconds < -5 or age_seconds > 60:
        raise RuntimeError("Execution refused: order book is stale")

    if best_ask > limit_price:
        raise RuntimeError(
            "Execution refused: current best ask is above limit_price"
        )

    executable_depth = 0
    for ask in asks:
        price_raw = ask.get("price")
        try:
            price = _quotation_decimal(price_raw, "ask price")
        except RuntimeError:
            continue
        if price > limit_price:
            continue
        try:
            executable_depth += int(ask.get("quantity") or 0)
        except (TypeError, ValueError):
            continue

    minimum_depth = max(quantity_lots * 5, quantity_lots)
    if executable_depth < minimum_depth:
        raise RuntimeError(
            "Execution refused: insufficient executable ask depth "
            f"({executable_depth} < {minimum_depth} lots)"
        )

    return best_ask - best_bid


def _validate_live_trading_status(
    *,
    client: TInvestSandboxClient,
    instrument_uid: str,
) -> None:
    status = client.get_trading_status(instrument_uid)
    trading_status = str(
        status.get("tradingStatus")
        or status.get("trading_status")
        or ""
    ).upper()
    if trading_status != "SECURITY_TRADING_STATUS_NORMAL_TRADING":
        raise RuntimeError(
            "Execution refused: trading status is not NORMAL_TRADING"
        )
    if status.get("limitOrderAvailableFlag") is not True:
        raise RuntimeError(
            "Execution refused: LIMIT orders are not currently available"
        )
    if status.get("apiTradeAvailableFlag") is False:
        raise RuntimeError(
            "Execution refused: API trading status is unavailable"
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

def _validate_new_entry_window(now) -> None:
    moscow = now.astimezone(ZoneInfo("Europe/Moscow"))
    if moscow.weekday() >= 5:
        raise RuntimeError(
            "BUY locked: weekend session is not allowed"
        )
    current = moscow.time().replace(tzinfo=None)
    # MOEX main session begins at 09:50 MSK. Strategy excludes the first
    # 15 minutes and stops accepting new entries 45 minutes before the
    # default 18:30 intraday force-exit.
    if current < time(10, 5) or current >= time(17, 45):
        raise RuntimeError(
            "BUY locked: outside allowed 10:05-17:45 MSK entry window"
        )


def _validate_intraday_time_stop(*, now, time_stop_value) -> None:
    if time_stop_value <= now:
        raise RuntimeError("BUY locked: time_stop is not in the future")
    moscow_now = now.astimezone(ZoneInfo("Europe/Moscow"))
    moscow_stop = time_stop_value.astimezone(
        ZoneInfo("Europe/Moscow")
    )
    if moscow_stop.date() != moscow_now.date():
        raise RuntimeError(
            "BUY locked: overnight automation is disabled until "
            "continuous lifecycle monitoring is implemented"
        )
    if moscow_stop.time().replace(tzinfo=None) > time(18, 30):
        raise RuntimeError(
            "BUY locked: intraday time_stop must be no later than "
            "18:30 MSK"
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

    if command.action == "BUY":
        _validate_new_entry_window(now)

    if command.action == "BUY" and command.time_stop is not None:
        _validate_intraday_time_stop(
            now=now,
            time_stop_value=command.time_stop,
        )

    if command.action == "BUY" and not PROTECTIVE_ORDER_LIFECYCLE_IMPLEMENTED:
        raise RuntimeError(
            "BUY locked: protective STOP/TAKE/TIME_STOP lifecycle "
            "is not implemented yet"
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

    if command.action == "BUY" and not has_sandbox_ready_marker(
        imap_host=config.imap_host,
        user=config.mail_user,
        app_password=config.mail_app_password,
        hmac_secret=config.hmac_secret,
        expected_account_name=config.sandbox_account_name,
        expected_account_id=client.account_id,
    ):
        raise RuntimeError(
            "BUY locked: signed Sandbox readiness is missing or invalid "
            "for the current Sandbox account"
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
    _validate_live_trading_status(
        client=client,
        instrument_uid=command.instrument_uid,
    )
    live_spread = _live_execution_market(
        client=client,
        instrument_uid=command.instrument_uid,
        limit_price=command.limit_price,
        quantity_lots=command.quantity_lots,
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
            hmac_secret=config.hmac_secret,
            expected_account_name=config.sandbox_account_name,
            expected_account_id=client.account_id,
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

        week_start_time = parse_iso_utc(
            str(baseline.get("week_start_generated_at_utc") or "")
        )
        month_start_time = parse_iso_utc(
            str(baseline.get("month_start_generated_at_utc") or "")
        )
        week_operations = client.get_operations_by_cursor(
            from_time=week_start_time,
            to_time=now,
        )
        month_operations = client.get_operations_by_cursor(
            from_time=month_start_time,
            to_time=now,
        )
        weekly_pnl = compute_period_pnl_rub(
            current_portfolio=prepared["portfolio"],
            start_equity_rub=Decimal(
                str(baseline.get("week_start_equity_rub"))
            ),
            operations_since_start=week_operations,
            period_name="week",
        )
        monthly_pnl = compute_period_pnl_rub(
            current_portfolio=prepared["portfolio"],
            start_equity_rub=Decimal(
                str(baseline.get("month_start_equity_rub"))
            ),
            operations_since_start=month_operations,
            period_name="month",
        )
        lifecycle_states = load_latest_lifecycle_states(
            imap_host=config.imap_host,
            user=config.mail_user,
            app_password=config.mail_app_password,
            hmac_secret=config.hmac_secret,
            expected_account_id=client.account_id,
        )
        consecutive_losses = compute_consecutive_losses_from_lifecycle(
            lifecycle_states,
            since_utc=str(baseline["generated_at_utc"]),
        )
        last_close_at = latest_strategy_close_time(
            lifecycle_states,
            since_utc=str(baseline["generated_at_utc"]),
        )

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
            market_spread_per_unit=live_spread,
            daily_pnl_rub=daily_pnl,
            weekly_pnl_rub=weekly_pnl,
            monthly_pnl_rub=monthly_pnl,
            week_start_equity_rub=Decimal(
                str(baseline.get("week_start_equity_rub"))
            ),
            month_start_equity_rub=Decimal(
                str(baseline.get("month_start_equity_rub"))
            ),
            high_water_mark_rub=Decimal(
                str(baseline.get("high_water_mark_rub"))
            ),
            consecutive_losses=consecutive_losses,
            last_strategy_close_at=last_close_at,
            now=now,
        )

        if command.probability_success_percent is not None:
            if command.expected_value_rub is None:
                raise RuntimeError(
                    "Hard risk: calibrated probability requires expected_value_rub"
                )
            probability = (
                command.probability_success_percent / Decimal("100")
            )
            computed_ev = (
                probability * hard_risk.net_reward_rub
                - (Decimal("1") - probability)
                * hard_risk.net_risk_rub
            )
            if (
                abs(command.expected_value_rub - computed_ev)
                > Decimal("0.01")
            ):
                raise RuntimeError(
                    "Hard risk: expected_value_rub does not match "
                    "code-computed calibrated EV"
                )
            minimum_ev = hard_risk.net_risk_rub * Decimal("0.3")
            if computed_ev < minimum_ev:
                raise RuntimeError(
                    "Hard risk: expected value below 0.3 x net risk"
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
            hmac_secret=config.hmac_secret,
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
                "weekly_pnl_rub": str(hard_risk.weekly_pnl_rub),
                "monthly_pnl_rub": str(hard_risk.monthly_pnl_rub),
                "drawdown_from_high_water": str(
                    hard_risk.drawdown_from_high_water
                ),
                "risk_budget_multiplier": str(
                    hard_risk.risk_budget_multiplier
                ),
            }
            if hard_risk is not None
            else None
        ),
        "broker_response": broker_result,
    }
