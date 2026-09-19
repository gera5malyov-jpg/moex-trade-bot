import json

from tradebot.config import Config
from tradebot.executor import execute_command
from tradebot.mailbox import iter_unseen_command_messages, mark_seen
from tradebot.protocol import TradeCommand


def main():
    cfg = Config.from_env()
    processed = 0

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

            result = execute_command(command, cfg)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            mark_seen(
                imap_host=cfg.imap_host,
                user=cfg.mail_user,
                app_password=cfg.mail_app_password,
                message_id=msg_id,
            )
            processed += 1
        except Exception as exc:
            print(
                f"Command rejected: {subject}: "
                f"{type(exc).__name__}: {exc}"
            )

    print(f"Processed commands: {processed}")


if __name__ == "__main__":
    main()
