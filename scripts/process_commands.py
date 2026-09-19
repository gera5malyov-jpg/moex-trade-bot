import json

from tradebot.config import Config
from tradebot.executor import execute_command
from tradebot.mailbox import (
    has_execution_receipt,
    iter_unseen_command_messages,
    mark_seen,
    send_execution_receipt,
)
from tradebot.protocol import TradeCommand, utc_now, verify_auth_token


def _valid_command_auth(command: TradeCommand, cfg: Config) -> bool:
    return verify_auth_token(
        cfg.hmac_secret,
        signal_id=command.signal_id,
        created_at=command.signal_created_at,
        ticker=command.ticker,
        class_code=command.class_code,
        instrument_uid=command.instrument_uid,
        instrument_type=command.instrument_type,
        execution_capability=command.execution_capability,
        token=command.auth_token,
    )


def _terminal_rejection(exc: Exception) -> bool:
    if isinstance(exc, ValueError):
        return True
    message = str(exc)
    terminal_prefixes = (
        "Hard risk:",
        "BUY locked:",
        "Execution refused:",
        "Automatic execution is not yet supported",
        "Signal is analysis-only:",
        "TRADING_ENABLED is false",
        "BUY refused:",
        "SELL refused:",
        "Resolved ",
        "Instrument resolution",
    )
    return message.startswith(terminal_prefixes)


def _send_rejection_receipt(
    *,
    cfg: Config,
    command: TradeCommand,
    exc: Exception,
) -> None:
    receipt = {
        "journal_version": "2",
        "processed_at": utc_now().isoformat(),
        "signal_id": command.signal_id,
        "signal_created_at": command.signal_created_at,
        "action": command.action,
        "ticker": command.ticker,
        "class_code": command.class_code,
        "instrument_id": command.instrument_id,
        "instrument_uid": command.instrument_uid,
        "instrument_type": command.instrument_type,
        "execution_capability": command.execution_capability,
        "probability_success_percent": (
            str(command.probability_success_percent)
            if command.probability_success_percent is not None
            else None
        ),
        "expected_value_rub": (
            str(command.expected_value_rub)
            if command.expected_value_rub is not None
            else None
        ),
        "market_regime": command.market_regime,
        "counter_argument": command.counter_argument,
        "why_counter_argument_does_not_invalidate": (
            command.why_counter_argument_does_not_invalidate
        ),
        "benchmark_check": command.benchmark_check,
        "data_completeness": command.data_completeness,
        "result": {
            "status": "rejected",
            "error_type": type(exc).__name__,
            "reason": str(exc)[:500],
        },
    }
    send_execution_receipt(
        smtp_host=cfg.smtp_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        recipient=cfg.mail_to,
        signal_id=command.signal_id,
        payload=receipt,
        hmac_secret=cfg.hmac_secret,
    )


def main():
    cfg = Config.from_env()
    processed = 0
    duplicates = 0

    for msg_id, subject, raw_json in iter_unseen_command_messages(
        imap_host=cfg.imap_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        allowed_from=cfg.command_allowed_from,
    ):
        command = None
        try:
            command = TradeCommand.from_json(raw_json)
            expected_subject = f"[TRADE-CMD] {command.signal_id}"
            if subject.strip() != expected_subject:
                raise ValueError(
                    f"Subject mismatch: expected {expected_subject!r}"
                )

            if has_execution_receipt(
                imap_host=cfg.imap_host,
                user=cfg.mail_user,
                app_password=cfg.mail_app_password,
                allowed_from=cfg.mail_user,
                signal_id=command.signal_id,
                hmac_secret=cfg.hmac_secret,
            ):
                print(
                    f"Duplicate command ignored: {command.signal_id}; "
                    "execution receipt already exists"
                )
                mark_seen(
                    imap_host=cfg.imap_host,
                    user=cfg.mail_user,
                    app_password=cfg.mail_app_password,
                    message_id=msg_id,
                )
                duplicates += 1
                continue

            result = execute_command(command, cfg)
            receipt = {
                "journal_version": "2",
                "processed_at": utc_now().isoformat(),
                "signal_id": command.signal_id,
                "signal_created_at": command.signal_created_at,
                "action": command.action,
                "ticker": command.ticker,
                "class_code": command.class_code,
                "instrument_id": command.instrument_id,
                "instrument_uid": command.instrument_uid,
                "instrument_type": command.instrument_type,
                "execution_capability": command.execution_capability,
                "order_type": command.order_type,
                "quantity_lots": command.quantity_lots,
                "limit_price": (
                    str(command.limit_price)
                    if command.limit_price is not None
                    else None
                ),
                "stop_loss": (
                    str(command.stop_loss)
                    if command.stop_loss is not None
                    else None
                ),
                "take_profit": (
                    str(command.take_profit)
                    if command.take_profit is not None
                    else None
                ),
                "time_stop": (
                    command.time_stop.isoformat()
                    if command.time_stop is not None
                    else None
                ),
                "reviewer_note": command.reviewer_note,
                        "probability_success_percent": (
                            str(command.probability_success_percent)
                            if command.probability_success_percent is not None
                            else None
                        ),
                        "expected_value_rub": (
                            str(command.expected_value_rub)
                            if command.expected_value_rub is not None
                            else None
                        ),
                        "market_regime": command.market_regime,
                        "counter_argument": command.counter_argument,
                        "why_counter_argument_does_not_invalidate": (
                            command.why_counter_argument_does_not_invalidate
                        ),
                        "benchmark_check": command.benchmark_check,
                        "data_completeness": command.data_completeness,
                                "result": result,
            }

            # Journal first. Only after a persistent receipt exists do we
            # mark the command email as seen.
            send_execution_receipt(
                smtp_host=cfg.smtp_host,
                user=cfg.mail_user,
                app_password=cfg.mail_app_password,
                recipient=cfg.mail_to,
                signal_id=command.signal_id,
                payload=receipt,
                hmac_secret=cfg.hmac_secret,
            )
            mark_seen(
                imap_host=cfg.imap_host,
                user=cfg.mail_user,
                app_password=cfg.mail_app_password,
                message_id=msg_id,
            )

            print(json.dumps(result, ensure_ascii=False, indent=2))
            processed += 1
        except Exception as exc:
            print(
                f"Command rejected: {subject}: "
                f"{type(exc).__name__}: {exc}"
            )

            # Poison/invalid mail must not be retried forever. A syntactically
            # invalid message is simply quarantined (marked seen). A validly
            # authenticated command with a deterministic rejection gets a
            # signed rejection receipt. Network/API/SMTP failures remain
            # unseen so the next scheduled run can retry safely.
            if command is None:
                mark_seen(
                    imap_host=cfg.imap_host,
                    user=cfg.mail_user,
                    app_password=cfg.mail_app_password,
                    message_id=msg_id,
                )
                continue

            if _terminal_rejection(exc):
                # A wrong subject is not a command execution outcome and must
                # not reserve the signal_id through an execution receipt.
                if (
                    not str(exc).startswith("Subject mismatch:")
                    and _valid_command_auth(command, cfg)
                ):
                    _send_rejection_receipt(
                        cfg=cfg,
                        command=command,
                        exc=exc,
                    )
                mark_seen(
                    imap_host=cfg.imap_host,
                    user=cfg.mail_user,
                    app_password=cfg.mail_app_password,
                    message_id=msg_id,
                )

    print(
        f"Processed commands: {processed}; "
        f"duplicates ignored: {duplicates}"
    )


if __name__ == "__main__":
    main()
