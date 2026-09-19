import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tradebot.config import Config
from tradebot.mailbox import (
    has_risk_baseline,
    send_risk_baseline_email,
)
from tradebot.tinvest import TInvestSandboxClient


MOSCOW = ZoneInfo("Europe/Moscow")


def main():
    cfg = Config.from_env()
    now_utc = datetime.now(timezone.utc)
    now_msk = now_utc.astimezone(MOSCOW)
    trading_date = now_msk.date().isoformat()

    client = TInvestSandboxClient(
        token=cfg.tinvest_token,
        account_name=cfg.sandbox_account_name,
    )

    if has_risk_baseline(
        imap_host=cfg.imap_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        trading_date=trading_date,
        hmac_secret=cfg.hmac_secret,
        expected_account_name=cfg.sandbox_account_name,
        expected_account_id=client.account_id,
    ):
        print(json.dumps({
            "status": "risk_baseline_exists",
            "trading_date_moscow": trading_date,
        }, ensure_ascii=False))
        return
    portfolio = client.get_portfolio()

    payload = {
        "baseline_version": "2",
        "environment": "TINVEST_SANDBOX",
        "trading_date_moscow": trading_date,
        "generated_at_utc": now_utc.isoformat(),
        "generated_at_moscow": now_msk.isoformat(),
        "sandbox_account_name": cfg.sandbox_account_name,
        "sandbox_account_id": client.account_id,
        "portfolio": portfolio,
        "note": (
            "Start-of-session equity baseline for fail-closed daily P&L. "
            "Any funding/withdrawal after this timestamp blocks BUY."
        ),
    }

    send_risk_baseline_email(
        smtp_host=cfg.smtp_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        recipient=cfg.mail_user,
        trading_date=trading_date,
        payload=payload,
        hmac_secret=cfg.hmac_secret,
    )

    print(json.dumps({
        "status": "risk_baseline_sent",
        "trading_date_moscow": trading_date,
        "generated_at_utc": now_utc.isoformat(),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
