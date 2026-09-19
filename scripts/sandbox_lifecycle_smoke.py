import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from zoneinfo import ZoneInfo

from tradebot.config import Config
from tradebot.lifecycle import FINAL_STATUSES, monitor_lifecycle_state, open_protected_long
from tradebot.mailbox import (
    has_sandbox_ready_marker,
    load_latest_lifecycle_states,
    load_risk_baseline,
    send_lifecycle_state_email,
    send_sandbox_ready_email,
)
from tradebot.protocol import (
    PROTECTIVE_LIFECYCLE_VERSION,
    SANDBOX_READY_VERSION,
    TradeCommand,
    make_sandbox_readiness_token,
    parse_iso_utc,
)
from tradebot.risk import (
    compute_consecutive_losses_from_lifecycle,
    compute_daily_pnl_rub,
    compute_period_pnl_rub,
    latest_strategy_close_time,
    validate_buy_hard_risk,
)
from tradebot.tinvest import TInvestSandboxClient


MOSCOW = ZoneInfo("Europe/Moscow")
SMOKE_DATE = "2026-09-21"


def q(value):
    if not isinstance(value, dict):
        return Decimal("0")
    return (
        Decimal(str(value.get("units", "0")))
        + Decimal(str(value.get("nano", 0))) / Decimal("1000000000")
    )


def round_tick(value: Decimal, tick: Decimal, rounding) -> Decimal:
    if tick <= 0:
        raise RuntimeError("Smoke test: min price increment unavailable")
    units = (value / tick).to_integral_value(rounding=rounding)
    return units * tick


def non_cash_positions(portfolio: dict) -> list[dict]:
    result = []
    for p in portfolio.get("positions") or []:
        ticker = str(p.get("ticker") or "").upper()
        class_code = str(p.get("classCode") or p.get("class_code") or "").upper()
        quantity = q(p.get("quantity"))
        if quantity <= 0:
            continue
        if ticker == "RUB000UTSTOM" or (
            ticker.startswith("RUB") and class_code == "CETS"
        ):
            continue
        result.append(p)
    return result


def build_command(
    *,
    ticker: str,
    class_code: str,
    instrument_uid: str,
    entry: Decimal,
    stop: Decimal,
    take: Decimal,
) -> TradeCommand:
    now = datetime.now(timezone.utc)
    return TradeCommand.from_dict(
        {
            "protocol_version": "2",
            "signal_id": str(uuid.uuid4()),
            "signal_created_at": now.isoformat(),
            "auth_token": "SANDBOX_LIFECYCLE_SMOKE_ONLY",
            "action": "BUY",
            "ticker": ticker,
            "class_code": class_code,
            "instrument_id": f"{ticker}_{class_code}",
            "instrument_uid": instrument_uid,
            "instrument_type": "share",
            "execution_capability": True,
            "order_type": "LIMIT",
            "quantity_lots": 1,
            "limit_price": str(entry),
            "stop_loss": str(stop),
            "take_profit": str(take),
            "time_stop": (now + timedelta(minutes=20)).isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "probability_success_percent": None,
            "expected_value_rub": None,
            "market_regime": "UNKNOWN",
            "counter_argument": "SMOKE_TEST_NOT_A_STRATEGY_ENTRY",
            "why_counter_argument_does_not_invalidate": (
                "Lifecycle verification only; excluded from strategy PnL streak."
            ),
            "benchmark_check": "INSUFFICIENT_HISTORY",
            "data_completeness": "FULL",
            "reviewer_note": "SANDBOX_LIFECYCLE_SMOKE_TEST",
        }
    )


def main():
    cfg = Config.from_env()
    now = datetime.now(timezone.utc)
    moscow_date = now.astimezone(MOSCOW).date().isoformat()

    if moscow_date != SMOKE_DATE:
        print(f"Smoke test not scheduled for {moscow_date}; expected {SMOKE_DATE}")
        return

    client = TInvestSandboxClient(
        token=cfg.tinvest_token,
        account_name=cfg.sandbox_account_name,
    )

    if has_sandbox_ready_marker(
        imap_host=cfg.imap_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        hmac_secret=cfg.hmac_secret,
        expected_account_name=cfg.sandbox_account_name,
        expected_account_id=client.account_id,
    ):
        print("Sandbox readiness marker already exists; smoke test skipped")
        return

    portfolio = client.get_portfolio()
    if non_cash_positions(portfolio):
        raise RuntimeError(
            "Smoke test refuses to start while a non-cash position exists"
        )

    baseline = load_risk_baseline(
        imap_host=cfg.imap_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        trading_date=moscow_date,
        hmac_secret=cfg.hmac_secret,
        expected_account_name=cfg.sandbox_account_name,
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
        current_portfolio=portfolio,
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
        current_portfolio=portfolio,
        start_equity_rub=Decimal(
            str(baseline.get("week_start_equity_rub"))
        ),
        operations_since_start=week_operations,
        period_name="week",
    )
    monthly_pnl = compute_period_pnl_rub(
        current_portfolio=portfolio,
        start_equity_rub=Decimal(
            str(baseline.get("month_start_equity_rub"))
        ),
        operations_since_start=month_operations,
        period_name="month",
    )
    lifecycle_states = load_latest_lifecycle_states(
        imap_host=cfg.imap_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        hmac_secret=cfg.hmac_secret,
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

    instrument = client.find_instrument("SBER_TQBR")
    if str(instrument.get("realExchange") or "") != "REAL_EXCHANGE_MOEX":
        raise RuntimeError("Smoke test instrument is not confirmed MOEX")
    uid = str(instrument.get("uid") or instrument.get("instrumentUid") or "")
    ticker = str(instrument.get("ticker") or "").upper()
    class_code = str(instrument.get("classCode") or "").upper()
    tick = q(instrument.get("minPriceIncrement"))
    if not uid or ticker != "SBER" or class_code != "TQBR":
        raise RuntimeError("Smoke test exact instrument resolution failed")

    last_error = None
    protected_state = None
    hard_risk = None

    for attempt in range(1, 4):
        try:
            book = client.get_order_book(uid, depth=1)
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            if not bids or not asks:
                raise RuntimeError("Smoke test order book is empty")
            best_ask = q(asks[0].get("price"))
            if best_ask <= 0:
                raise RuntimeError("Smoke test best ask is unavailable")

            entry = round_tick(
                best_ask + tick,
                tick,
                ROUND_CEILING,
            )
            stop = round_tick(
                entry * Decimal("0.995"),
                tick,
                ROUND_FLOOR,
            )
            take = round_tick(
                entry * Decimal("1.015"),
                tick,
                ROUND_CEILING,
            )
            command = build_command(
                ticker=ticker,
                class_code=class_code,
                instrument_uid=uid,
                entry=entry,
                stop=stop,
                take=take,
            )

            prepared = client.prepare_limit_order(
                ticker=ticker,
                class_code=class_code,
                instrument_uid=uid,
                side="BUY",
                quantity_lots=1,
                limit_price=entry,
            )
            hard_risk = validate_buy_hard_risk(
                command=command,
                portfolio=prepared["portfolio"],
                preflight_order_price=prepared["preflight_order_price"],
                instrument_lot=int(prepared["instrument"].get("lot") or 0),
                min_price_increment=tick,
                market_spread_per_unit=(best_ask - q(bids[0].get("price"))),
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

            state = open_protected_long(
                client=client,
                command=command,
                prepared=prepared,
            )
            send_lifecycle_state_email(
                smtp_host=cfg.smtp_host,
                user=cfg.mail_user,
                app_password=cfg.mail_app_password,
                recipient=cfg.mail_user,
                signal_id=command.signal_id,
                payload=state,
                hmac_secret=cfg.hmac_secret,
            )
            if state.get("status") == "PROTECTED":
                protected_state = state
                break

            remaining_after_attempt = client.get_position_lots(
                instrument_uid=uid,
                lot_size=int(prepared["instrument"].get("lot") or 0),
            )
            if remaining_after_attempt != 0:
                raise RuntimeError(
                    "Smoke attempt did not establish protection and left "
                    f"{remaining_after_attempt} lots open; refusing retry"
                )

            if state.get("status") in FINAL_STATUSES:
                last_error = RuntimeError(
                    f"Smoke entry attempt {attempt} ended as {state.get('status')}"
                )
            else:
                last_error = RuntimeError(
                    f"Smoke entry attempt {attempt} is not protected: {state.get('status')}"
                )
        except Exception as exc:
            remaining_after_error = client.get_position_lots(
                instrument_uid=uid,
                lot_size=int(instrument.get("lot") or 0),
            )
            if remaining_after_error != 0:
                raise RuntimeError(
                    "Smoke attempt failed while a position remains open; "
                    f"remaining_lots={remaining_after_error}"
                ) from exc
            last_error = exc

    if protected_state is None:
        raise RuntimeError(f"Smoke test could not establish protected entry: {last_error}")

    active_stops = client.get_stop_orders(status="STOP_ORDER_STATUS_ACTIVE")
    active_ids = {
        str(item.get("stopOrderId") or item.get("stop_order_id") or "")
        for item in active_stops
    }
    if protected_state["stop_order_id"] not in active_ids:
        raise RuntimeError("Smoke STOP_LOSS is not active at broker")
    if protected_state["take_order_id"] not in active_ids:
        raise RuntimeError("Smoke TAKE_PROFIT is not active at broker")

    cleanup_state = dict(protected_state)
    cleanup_state["time_stop"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    final_state = monitor_lifecycle_state(
        client=client,
        state=cleanup_state,
        now=datetime.now(timezone.utc),
    )
    for _ in range(10):
        if str(final_state.get("status") or "") in FINAL_STATUSES:
            break
        time.sleep(2)
        final_state = monitor_lifecycle_state(
            client=client,
            state=final_state,
            now=datetime.now(timezone.utc),
        )

    send_lifecycle_state_email(
        smtp_host=cfg.smtp_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        recipient=cfg.mail_user,
        signal_id=str(final_state["signal_id"]),
        payload=final_state,
        hmac_secret=cfg.hmac_secret,
    )

    remaining = client.get_position_lots(
        instrument_uid=uid,
        lot_size=int(protected_state["lot_size"]),
    )
    if remaining != 0:
        raise RuntimeError(
            f"Smoke cleanup left {remaining} lots open; readiness denied"
        )
    if str(final_state.get("status") or "") not in FINAL_STATUSES:
        raise RuntimeError(
            f"Smoke cleanup did not reach final lifecycle state: {final_state.get('status')}"
        )

    verified_at_utc = datetime.now(timezone.utc).isoformat()
    readiness_payload = {
        "ready_version": SANDBOX_READY_VERSION,
        "environment": "TINVEST_SANDBOX",
        "verified_at_utc": verified_at_utc,
        "sandbox_account_name": cfg.sandbox_account_name,
        "sandbox_account_id": client.account_id,
        "lifecycle_version": PROTECTIVE_LIFECYCLE_VERSION,
        "smoke_date_moscow": moscow_date,
        "instrument": "SBER_TQBR",
        "entry_protected": True,
        "stop_loss_active_verified": True,
        "take_profit_active_verified": True,
        "time_stop_force_exit_verified": True,
        "position_after_cleanup_lots": 0,
        "daily_pnl_rub_at_test": str(daily_pnl),
        "consecutive_losses_at_test": consecutive_losses,
        "risk_budget_rub": str(
            hard_risk.risk_budget_rub if hard_risk else ""
        ),
        "note": "Sandbox-only readiness marker. Not valid for production.",
    }
    readiness_payload["readiness_token"] = make_sandbox_readiness_token(
        cfg.hmac_secret,
        verified_at_utc=verified_at_utc,
        sandbox_account_name=cfg.sandbox_account_name,
        sandbox_account_id=client.account_id,
        lifecycle_version=PROTECTIVE_LIFECYCLE_VERSION,
        instrument="SBER_TQBR",
    )
    send_sandbox_ready_email(
        smtp_host=cfg.smtp_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        payload=readiness_payload,
    )

    print(json.dumps({
        "status": "sandbox_lifecycle_smoke_passed",
        "signal_id": final_state["signal_id"],
        "final_status": final_state["status"],
        "remaining_lots": remaining,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
