from decimal import Decimal

from tradebot.config import Config
from tradebot.mailbox import send_signal_email
from tradebot.protocol import Signal


def main():
    cfg = Config.from_env()
    signal = Signal.create(
        secret=cfg.hmac_secret,
        ticker="SBER",
        class_code="TQBR",
        observed_price=Decimal("300.00"),
        reason="TEST ONLY — scaffold verification; not an investment signal",
    )
    send_signal_email(
        smtp_host=cfg.smtp_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        recipient=cfg.mail_to,
        signal_id=signal.signal_id,
        json_body=signal.to_json(),
    )
    # Do not print auth_token into public GitHub Actions logs.
    print(f"Test signal sent: {signal.signal_id}")


if __name__ == "__main__":
    main()
