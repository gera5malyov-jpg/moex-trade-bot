import json

from tradebot.config import Config
from tradebot.executor import execute_command
from tradebot.mailbox import (
    has_execution_receipt,
    iter_unseen_command_messages,
    mark_seen,
    send_execution_receipt,
)
from tradebot.protocol import TradeCommand, utc_now


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
                "journal_version": "1",
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
            # Rejected/failed commands remain UNSEEN so the next run can retry
            # transient failures. Deterministic invalid commands will keep
            # failing and are visible in Actions logs for diagnosis.
            print(
                f"Command rejected: {subject}: "
                f"{type(exc).__name__}: {exc}"
            )

    print(
        f"Processed commands: {processed}; "
        f"duplicates ignored: {duplicates}"
    )


if __name__ == "__main__":
    main()
