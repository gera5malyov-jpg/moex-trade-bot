import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tradebot.config import Config
from tradebot.mailbox import send_daily_data_email
from tradebot.tinvest import TInvestSandboxClient


MOSCOW = ZoneInfo("Europe/Moscow")


def main():
    cfg = Config.from_env()
    client = TInvestSandboxClient(
        token=cfg.tinvest_token,
        account_name=cfg.sandbox_account_name,
    )

    now_utc = datetime.now(timezone.utc)
    now_msk = now_utc.astimezone(MOSCOW)
    start_msk = now_msk.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    start_utc = start_msk.astimezone(timezone.utc)

    portfolio = client.get_portfolio()
    positions = client.get_positions()
    operations = client.get_operations_by_cursor(
        from_time=start_utc,
        to_time=now_utc,
    )

    payload = {
        "snapshot_version": "1",
        "environment": "TINVEST_SANDBOX",
        "trading_date_moscow": now_msk.date().isoformat(),
        "generated_at_utc": now_utc.isoformat(),
        "generated_at_moscow": now_msk.isoformat(),
        "sandbox_account_name": cfg.sandbox_account_name,
        "portfolio": portfolio,
        "positions": positions,
        "operations": operations,
        "note": (
            "Raw broker snapshot for ChatGPT daily report. "
            "Do not infer missing values."
        ),
    }

    send_daily_data_email(
        smtp_host=cfg.smtp_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        recipient=cfg.mail_to,
        trading_date=now_msk.date().isoformat(),
        payload=payload,
    )

    print(
        json.dumps(
            {
                "status": "daily_snapshot_sent",
                "trading_date_moscow": now_msk.date().isoformat(),
                "operation_count": len(
                    operations.get("items")
                    or operations.get("operations")
                    or []
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
