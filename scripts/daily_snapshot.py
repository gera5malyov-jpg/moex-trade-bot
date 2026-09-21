import json
import os
from datetime import datetime, timezone, timedelta
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
    report_date = now_msk.date()
    if os.getenv("DAILY_SNAPSHOT_PREVIOUS_MOSCOW_DAY", "").lower() in {"1", "true", "yes"}:
        report_date = report_date - timedelta(days=1)

    start_msk = datetime.combine(report_date, datetime.min.time(), tzinfo=MOSCOW)
    end_msk = start_msk + timedelta(days=1)
    start_utc = start_msk.astimezone(timezone.utc)
    end_utc = min(end_msk.astimezone(timezone.utc), now_utc)

    portfolio = client.get_portfolio()
    positions = client.get_positions()
    operations = client.get_operations_by_cursor(
        from_time=start_utc,
        to_time=end_utc,
    )

    payload = {
        "snapshot_version": "1",
        "environment": "TINVEST_SANDBOX",
        "trading_date_moscow": report_date.isoformat(),
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
        trading_date=report_date.isoformat(),
        payload=payload,
    )

    print(
        json.dumps(
            {
                "status": "daily_snapshot_sent",
                "trading_date_moscow": report_date.isoformat(),
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
