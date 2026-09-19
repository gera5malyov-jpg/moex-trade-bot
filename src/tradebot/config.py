from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _derive_hmac_secret(tinvest_token: str) -> str:
    # Domain-separated key derivation. The T-Invest token itself is never
    # written to signals, logs, or the repository.
    return hashlib.sha256(
        ("moex-trade-bot:command-auth:v1:" + tinvest_token).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class Config:
    tinvest_token: str
    sandbox_account_name: str
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
        tinvest_token = _required("TINVEST_TOKEN")
        mail_user = _required("MAIL_USER")

        return cls(
            tinvest_token=tinvest_token,
            sandbox_account_name=os.getenv(
                "TINVEST_SANDBOX_ACCOUNT_NAME",
                "github-moex-trade-bot",
            ).strip(),
            trading_enabled=os.getenv("TRADING_ENABLED", "false").lower() == "true",
            hmac_secret=_derive_hmac_secret(tinvest_token),
            command_max_age_minutes=int(os.getenv("COMMAND_MAX_AGE_MINUTES", "15")),
            mail_user=mail_user,
            mail_app_password=_required("MAIL_APP_PASSWORD"),
            mail_to=os.getenv("MAIL_TO", "").strip() or mail_user,
            command_allowed_from=(
                os.getenv("COMMAND_ALLOWED_FROM", "").strip() or mail_user
            ),
            imap_host=os.getenv("MAIL_IMAP_HOST", "imap.gmail.com").strip(),
            smtp_host=os.getenv("MAIL_SMTP_HOST", "smtp.gmail.com").strip(),
        )
