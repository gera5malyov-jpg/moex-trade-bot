import json
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from tradebot.config import Config
from tradebot.mailbox import (
    has_risk_baseline,
    load_latest_risk_baseline,
    send_risk_baseline_email,
)
from tradebot.tinvest import TInvestSandboxClient


MOSCOW = ZoneInfo("Europe/Moscow")


def money_value(value) -> Decimal:
    if not isinstance(value, dict):
        raise RuntimeError("Baseline portfolio equity is unavailable")
    result = (
        Decimal(str(value.get("units", "0")))
        + Decimal(str(value.get("nano", 0)))
        / Decimal("1000000000")
    )
    if not result.is_finite() or result <= 0:
        raise RuntimeError("Baseline portfolio equity must be positive")
    return result


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
    current_equity = money_value(
        portfolio.get("totalAmountPortfolio")
        or portfolio.get("total_amount_portfolio")
    )

    previous = load_latest_risk_baseline(
        imap_host=cfg.imap_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        hmac_secret=cfg.hmac_secret,
        expected_account_name=cfg.sandbox_account_name,
        expected_account_id=client.account_id,
    )

    iso = now_msk.isocalendar()
    week_id = f"{iso.year}-W{iso.week:02d}"
    month_id = now_msk.strftime("%Y-%m")

    high_water = current_equity
    week_start_equity = current_equity
    week_start_generated_at = now_utc.isoformat()
    month_start_equity = current_equity
    month_start_generated_at = now_utc.isoformat()

    if previous:
        try:
            previous_high = Decimal(
                str(
                    previous.get("high_water_mark_rub")
                    or money_value(
                        (previous.get("portfolio") or {}).get(
                            "totalAmountPortfolio"
                        )
                        or (previous.get("portfolio") or {}).get(
                            "total_amount_portfolio"
                        )
                    )
                )
            )
            if previous_high.is_finite():
                high_water = max(high_water, previous_high)
        except Exception:
            pass

        if str(previous.get("week_id") or "") == week_id:
            week_start_equity = Decimal(
                str(previous.get("week_start_equity_rub"))
            )
            week_start_generated_at = str(
                previous.get("week_start_generated_at_utc") or ""
            )
            if not week_start_generated_at:
                raise RuntimeError(
                    "Previous weekly baseline timestamp is unavailable"
                )

        if str(previous.get("month_id") or "") == month_id:
            month_start_equity = Decimal(
                str(previous.get("month_start_equity_rub"))
            )
            month_start_generated_at = str(
                previous.get("month_start_generated_at_utc") or ""
            )
            if not month_start_generated_at:
                raise RuntimeError(
                    "Previous monthly baseline timestamp is unavailable"
                )

    payload = {
        "baseline_version": "3",
        "environment": "TINVEST_SANDBOX",
        "trading_date_moscow": trading_date,
        "generated_at_utc": now_utc.isoformat(),
        "generated_at_moscow": now_msk.isoformat(),
        "sandbox_account_name": cfg.sandbox_account_name,
        "sandbox_account_id": client.account_id,
        "portfolio": portfolio,
        "high_water_mark_rub": str(high_water),
        "week_id": week_id,
        "week_start_equity_rub": str(week_start_equity),
        "week_start_generated_at_utc": week_start_generated_at,
        "month_id": month_id,
        "month_start_equity_rub": str(month_start_equity),
        "month_start_generated_at_utc": month_start_generated_at,
        "note": (
            "Signed daily/weekly/monthly Sandbox equity baseline. "
            "Funding/withdrawal after a relevant period baseline blocks BUY."
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
