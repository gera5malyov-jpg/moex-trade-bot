from __future__ import annotations

import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Config:
    tinvest_token: str
    sandbox_account_id: str
    trading_enabled: bool
    hmac_secret: str
    command_max_age_minutes: int

    mail_user: str
    mail_app_password: str
    mail_to: str
    command_allowed_from: str
    imap_host: str
    smtp_host: str

    @classmethod
    def from_env(cls) -> "Config":
        secret = _required("TRADE_HMAC_SECRET")
        if len(secret) < 32:
            raise RuntimeError("TRADE_HMAC_SECRET must be at least 32 characters")

        return cls(
            tinvest_token=_required("TINVEST_TOKEN"),
            sandbox_account_id=_required("TINVEST_SANDBOX_ACCOUNT_ID"),
            trading_enabled=os.getenv("TRADING_ENABLED", "false").lower() == "true",
            hmac_secret=secret,
            command_max_age_minutes=int(os.getenv("COMMAND_MAX_AGE_MINUTES", "15")),
            mail_user=_required("MAIL_USER"),
            mail_app_password=_required("MAIL_APP_PASSWORD"),
            mail_to=_required("MAIL_TO"),
            command_allowed_from=_required("COMMAND_ALLOWED_FROM"),
            imap_host=os.getenv("MAIL_IMAP_HOST", "imap.gmail.com").strip(),
            smtp_host=os.getenv("MAIL_SMTP_HOST", "smtp.gmail.com").strip(),
        )
