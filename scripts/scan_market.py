import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tradebot.config import Config
from tradebot.mailbox import (
    has_recent_signal_for_instrument,
    has_sandbox_ready_marker,
    load_risk_baseline,
    send_signal_email,
)
from tradebot.protocol import Signal, parse_iso_utc
from tradebot.risk import compute_consecutive_losses, compute_daily_pnl_rub
from tradebot.scanner import enrich_candidate, scan_candidates
from tradebot.tinvest import TInvestSandboxClient


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
    losses = hard_risk_context.get("consecutive_losses")
    if losses is None or int(losses) >= 3:
        return False

    instrument = candidate.get("instrument") or {}
    if instrument.get("apiTradeAvailableFlag") is not True:
        return False
    if instrument.get("buyAvailableFlag") is not True:
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
    )
    now_utc = datetime.now(timezone.utc)
    trading_date = now_utc.astimezone(
        ZoneInfo("Europe/Moscow")
    ).date().isoformat()
    hard_risk_context = {
        "sandbox_ready": sandbox_ready,
        "trading_date_moscow": trading_date,
        "daily_pnl_rub": None,
        "consecutive_losses": None,
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
        )
        baseline_time = parse_iso_utc(
            str(baseline.get("generated_at_utc") or "")
        )
        current_portfolio = client.get_portfolio()
        operations = client.get_operations_by_cursor(
            from_time=baseline_time,
            to_time=now_utc,
        )
        hard_risk_context.update(
            {
                "daily_pnl_rub": str(
                    compute_daily_pnl_rub(
                        current_portfolio=current_portfolio,
                        baseline_payload=baseline,
                        operations_since_baseline=operations,
                    )
                ),
                "consecutive_losses": compute_consecutive_losses(operations),
                "baseline_generated_at_utc": baseline.get("generated_at_utc"),
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
