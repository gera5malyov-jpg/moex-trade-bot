from decimal import Decimal

from tradebot.config import Config
from tradebot.mailbox import send_signal_email
from tradebot.protocol import Signal
from tradebot.tinvest import TInvestSandboxClient


def main():
    cfg = Config.from_env()

    client = TInvestSandboxClient(
        token=cfg.tinvest_token,
        account_name=cfg.sandbox_account_name,
    )
    instrument = client.find_instrument("SBER_TQBR")
    instrument_uid = str(
        instrument.get("uid") or instrument.get("instrumentUid") or ""
    )
    instrument_type = str(
        instrument.get("instrumentType")
        or instrument.get("instrument_type")
        or "share"
    )
    if not instrument_uid:
        raise RuntimeError("SBER_TQBR resolved without instrument UID")

    signal = Signal.create(
        secret=cfg.hmac_secret,
        ticker="SBER",
        class_code="TQBR",
        instrument_uid=instrument_uid,
        instrument_type=instrument_type,
        execution_capability=False,
        observed_price=Decimal("300.00"),
        reason="TEST ONLY — scaffold verification; analysis-only signal",
    )
    send_signal_email(
        smtp_host=cfg.smtp_host,
        user=cfg.mail_user,
        app_password=cfg.mail_app_password,
        recipient=cfg.mail_to,
        signal_id=signal.signal_id,
        json_body=signal.to_json(),
    )
    # Never print auth_token or T-Invest token into public Actions logs.
    print(f"Test signal sent: {signal.signal_id}")


if __name__ == "__main__":
    main()
