import json
import os
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from tradebot.config import Config
from tradebot.mailbox import (
    has_recent_signal_for_instrument,
    has_sandbox_ready_marker,
    load_latest_lifecycle_states,
    load_risk_baseline,
    send_signal_email,
)
from tradebot.protocol import Signal, parse_iso_utc
from tradebot.risk import (
    DAILY_STOP,
    MONTHLY_STOP,
    WEEKLY_STOP,
    compute_consecutive_losses_from_lifecycle,
    compute_daily_pnl_rub,
    compute_period_pnl_rub,
    latest_strategy_close_time,
)
from tradebot.scanner import enrich_candidate, scan_candidates
from tradebot.tinvest import TInvestSandboxClient


def money_value(value) -> Decimal:
    if not isinstance(value, dict):
        raise RuntimeError("Portfolio equity is unavailable")
    return (
        Decimal(str(value.get("units", "0")))
        + Decimal(str(value.get("nano", 0)))
        / Decimal("1000000000")
    )


def entry_window_open(now_utc: datetime) -> bool:
    moscow = now_utc.astimezone(ZoneInfo("Europe/Moscow"))
    if moscow.weekday() >= 5:
        return False
    current = moscow.time().replace(tzinfo=None)
    return time(10, 5) <= current < time(17, 45)


def execution_snapshot_ready(
    *,
    candidate: dict,
    context: dict,
    hard_risk_context: dict,
    sandbox_ready: bool,
) -> bool:
    if candidate["instrument_type"] not in {"share", "etf"}:
        return False
    if not sandbox_ready:
        return False
    if hard_risk_context.get("error") is not None:
        return False
    if hard_risk_context.get("daily_pnl_rub") is None:
        return False
    if hard_risk_context.get("weekly_pnl_rub") is None:
        return False
    if hard_risk_context.get("monthly_pnl_rub") is None:
        return False
    if hard_risk_context.get("risk_gate_open") is not True:
        return False
    if hard_risk_context.get("entry_window_open") is not True:
        return False
    losses = hard_risk_context.get("consecutive_losses")
    if losses is None or int(losses) >= 3:
        return False

    instrument = candidate.get("instrument") or {}
    if instrument.get("apiTradeAvailableFlag") is not True:
        return False
    if instrument.get("buyAvailableFlag") is not True:
        return False
    if instrument.get("liquidityFlag") is not True:
        return False
    if instrument.get("blockedTcaFlag") is True:
        return False
    required_tests = (
        instrument.get("requiredTests")
        or instrument.get("required_tests")
        or []
    )
    if required_tests:
        return False

    trading = context.get("trading_status") or {}
    status = str(
        trading.get("tradingStatus")
        or trading.get("trading_status")
        or ""
    ).upper()
    if status != "SECURITY_TRADING_STATUS_NORMAL_TRADING":
        return False
    if trading.get("limitOrderAvailableFlag") is not True:
        return False
    if trading.get("apiTradeAvailableFlag") is False:
        return False

    try:
        if float(context.get("bid") or 0) <= 0:
            return False
        if float(context.get("ask") or 0) <= 0:
            return False
        if float(context.get("spread_percent")) < 0:
            return False
        if float(
            (context.get("account_positions") or {}).get(
                "available_cash_rub", "0"
            )
        ) <= 0:
            return False
    except (TypeError, ValueError):
        return False

    for key in (
        "technical_1m",
        "technical_5m",
        "technical_15m",
        "technical_1h",
        "technical_1d",
    ):
        technical = context.get(key) or {}
        required = (
            "ema9",
            "ema21",
            "rsi14",
            "atr14",
            "vwap",
            "realized_volatility_percent",
            "last_close",
        )
        if int(technical.get("candles_count") or 0) < 22:
            return False
        if any(technical.get(field) is None for field in required):
            return False

    return True


def main():
    cfg = Config.from_env()
    client = TInvestSandboxClient(
        token=cfg.tinvest_token,
        account_name=cfg.sandbox_account_name,
    )

    max_per_type = int(os.getenv("SCANNER_MAX_PER_TYPE", "1"))
    max_signals = int(os.getenv("SCANNER_MAX_SIGNALS", "6"))
    cooldown_minutes = int(os.getenv("SCANNER_COOLDOWN_MINUTES", "45"))

    sandbox_ready = has_sandbox_ready_marker(
        imap_host=cfg.imap_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        hmac_secret=cfg.hmac_secret,
        expected_account_name=cfg.sandbox_account_name,
        expected_account_id=client.account_id,
    )
    now_utc = datetime.now(timezone.utc)
    trading_date = now_utc.astimezone(
        ZoneInfo("Europe/Moscow")
    ).date().isoformat()
    hard_risk_context = {
        "sandbox_ready": sandbox_ready,
        "trading_date_moscow": trading_date,
        "daily_pnl_rub": None,
        "weekly_pnl_rub": None,
        "monthly_pnl_rub": None,
        "high_water_mark_rub": None,
        "drawdown_from_high_water": None,
        "consecutive_losses": None,
        "last_strategy_close_at": None,
        "entry_window_open": entry_window_open(now_utc),
        "risk_gate_open": False,
        "baseline_generated_at_utc": None,
        "error": None,
    }
    try:
        baseline = load_risk_baseline(
            imap_host=cfg.imap_host,
            user=cfg.mail_user,
            app_password=cfg.mail_app_password,
            trading_date=trading_date,
            hmac_secret=cfg.hmac_secret,
            expected_account_name=cfg.sandbox_account_name,
            expected_account_id=client.account_id,
        )
        baseline_time = parse_iso_utc(
            str(baseline.get("generated_at_utc") or "")
        )
        current_portfolio = client.get_portfolio()
        operations = client.get_operations_by_cursor(
            from_time=baseline_time,
            to_time=now_utc,
        )
        week_start_time = parse_iso_utc(
            str(baseline.get("week_start_generated_at_utc") or "")
        )
        month_start_time = parse_iso_utc(
            str(baseline.get("month_start_generated_at_utc") or "")
        )
        week_operations = client.get_operations_by_cursor(
            from_time=week_start_time,
            to_time=now_utc,
        )
        month_operations = client.get_operations_by_cursor(
            from_time=month_start_time,
            to_time=now_utc,
        )
        daily_pnl = compute_daily_pnl_rub(
            current_portfolio=current_portfolio,
            baseline_payload=baseline,
            operations_since_baseline=operations,
        )
        weekly_pnl = compute_period_pnl_rub(
            current_portfolio=current_portfolio,
            start_equity_rub=Decimal(
                str(baseline.get("week_start_equity_rub"))
            ),
            operations_since_start=week_operations,
            period_name="week",
        )
        monthly_pnl = compute_period_pnl_rub(
            current_portfolio=current_portfolio,
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
        last_close = latest_strategy_close_time(
            lifecycle_states,
            since_utc=str(baseline["generated_at_utc"]),
        )
        capital = money_value(
            current_portfolio.get("totalAmountPortfolio")
            or current_portfolio.get("total_amount_portfolio")
        )
        week_start = Decimal(str(baseline.get("week_start_equity_rub")))
        month_start = Decimal(str(baseline.get("month_start_equity_rub")))
        high_water = Decimal(str(baseline.get("high_water_mark_rub")))
        drawdown = (
            (max(high_water, capital) - capital)
            / max(high_water, capital)
        )
        cooldown_open = True
        if consecutive_losses >= 3:
            cooldown_open = False
        elif consecutive_losses >= 2:
            if last_close is None:
                cooldown_open = False
            else:
                cooldown_open = (
                    now_utc >= last_close + timedelta(hours=2)
                )

        risk_gate_open = (
            daily_pnl > -(capital * DAILY_STOP)
            and weekly_pnl > -(week_start * WEEKLY_STOP)
            and monthly_pnl > -(month_start * MONTHLY_STOP)
            and cooldown_open
        )

        hard_risk_context.update(
            {
                "daily_pnl_rub": str(daily_pnl),
                "weekly_pnl_rub": str(weekly_pnl),
                "monthly_pnl_rub": str(monthly_pnl),
                "high_water_mark_rub": str(high_water),
                "drawdown_from_high_water": str(drawdown),
                "consecutive_losses": consecutive_losses,
                "last_strategy_close_at": (
                    last_close.isoformat() if last_close else None
                ),
                "risk_gate_open": risk_gate_open,
                "baseline_generated_at_utc": baseline.get("generated_at_utc"),
                "baseline_version": baseline.get("baseline_version"),
                "week_id": baseline.get("week_id"),
                "month_id": baseline.get("month_id"),
            }
        )
    except Exception as exc:
        hard_risk_context["error"] = f"{type(exc).__name__}: {exc}"

    candidates = scan_candidates(
        client,
        max_per_type=max_per_type,
    )

    supported_candidates = [
        candidate
        for candidate in candidates
        if candidate["instrument_type"] in {"share", "etf"}
    ]
    analysis_only_candidates = [
        candidate
        for candidate in candidates
        if candidate["instrument_type"] not in {"share", "etf"}
    ]
    ordered_candidates = supported_candidates + analysis_only_candidates

    sent = 0
    executable_sent = 0
    for candidate in ordered_candidates:
        if sent >= max_signals:
            break
        try:
            if has_recent_signal_for_instrument(
                imap_host=cfg.imap_host,
                user=cfg.mail_user,
                app_password=cfg.mail_app_password,
                instrument_uid=candidate["instrument_uid"],
                within_minutes=cooldown_minutes,
            ):
                print(
                    f"Candidate skipped by cooldown: "
                    f"{candidate['ticker']} ({candidate['instrument_uid']})"
                )
                continue

            context = enrich_candidate(client, candidate)
            context["hard_risk_context"] = dict(hard_risk_context)
            execution_capability = execution_snapshot_ready(
                candidate=candidate,
                context=context,
                hard_risk_context=hard_risk_context,
                sandbox_ready=sandbox_ready,
            )
            signal = Signal.create(
                secret=cfg.hmac_secret,
                ticker=candidate["ticker"],
                class_code=candidate["class_code"],
                instrument_uid=candidate["instrument_uid"],
                instrument_type=candidate["instrument_type"],
                execution_capability=execution_capability,
                observed_price=candidate["last_price"],
                reason=(
                    "SCANNER CANDIDATE ONLY — "
                    f"{candidate['instrument_type']} "
                    f"{candidate['ticker']} moved "
                    f"{candidate['move_percent']:.4f}% from previous close. "
                    "Requires independent ChatGPT Work review; "
                    f"execution_capability={execution_capability}."
                ),
                context=context,
            )
            send_signal_email(
                smtp_host=cfg.smtp_host,
                user=cfg.mail_user,
                app_password=cfg.mail_app_password,
                recipient=cfg.mail_to,
                signal_id=signal.signal_id,
                json_body=signal.to_json(),
                instrument_uid=signal.instrument_uid,
            )
            print(
                json.dumps(
                    {
                        "signal_id": signal.signal_id,
                        "ticker": signal.ticker,
                        "instrument_type": signal.instrument_type,
                        "execution_capability": signal.execution_capability,
                    },
                    ensure_ascii=False,
                )
            )
            sent += 1
            if execution_capability:
                executable_sent += 1
        except Exception as exc:
            print(
                f"Candidate enrichment/send failed for "
                f"{candidate.get('ticker')}: "
                f"{type(exc).__name__}: {exc}"
            )

    print(
        f"Scanner candidates={len(candidates)}; "
        f"signals sent={sent}; executable-capable={executable_sent}; "
        f"analysis-only={sent - executable_sent}"
    )


if __name__ == "__main__":
    main()
