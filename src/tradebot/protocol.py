from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def make_auth_token(secret: str, signal_id: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        signal_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_auth_token(secret: str, signal_id: str, token: str) -> bool:
    expected = make_auth_token(secret, signal_id)
    return hmac.compare_digest(expected, token)


def quotation_from_decimal(value: Decimal) -> dict[str, Any]:
    if value <= 0:
        raise ValueError("Price must be positive")
    quantized = value.quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
    units = int(quantized)
    nano = int((quantized - Decimal(units)) * Decimal("1000000000"))
    return {"units": str(units), "nano": nano}


@dataclass(frozen=True)
class Signal:
    protocol_version: str
    signal_id: str
    created_at: str
    ticker: str
    class_code: str
    instrument_id: str
    observed_price: str
    reason: str
    auth_token: str

    @classmethod
    def create(
        cls,
        *,
        secret: str,
        ticker: str,
        observed_price: Decimal,
        reason: str,
        class_code: str = "TQBR",
    ) -> "Signal":
        signal_id = str(uuid.uuid4())
        ticker = ticker.upper().strip()
        class_code = class_code.upper().strip()
        return cls(
            protocol_version="1",
            signal_id=signal_id,
            created_at=utc_now().isoformat(),
            ticker=ticker,
            class_code=class_code,
            instrument_id=f"{ticker}_{class_code}",
            observed_price=str(observed_price),
            reason=reason.strip(),
            auth_token=make_auth_token(secret, signal_id),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class TradeCommand:
    protocol_version: str
    signal_id: str
    auth_token: str
    action: str
    ticker: str
    class_code: str
    instrument_id: str
    order_type: str
    quantity_lots: int
    limit_price: Decimal | None
    expires_at: datetime
    reviewer_note: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TradeCommand":
        required = [
            "protocol_version", "signal_id", "auth_token", "action",
            "ticker", "class_code", "instrument_id", "order_type",
            "quantity_lots", "expires_at"
        ]
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError(f"Missing command fields: {', '.join(missing)}")

        try:
            uuid.UUID(str(data["signal_id"]))
        except Exception as exc:
            raise ValueError("signal_id must be UUID") from exc

        action = str(data["action"]).upper()
        if action not in {"BUY", "SELL", "SKIP"}:
            raise ValueError("action must be BUY, SELL or SKIP")

        order_type = str(data["order_type"]).upper()
        if action != "SKIP" and order_type != "LIMIT":
            raise ValueError("Only LIMIT orders are permitted in this scaffold")

        quantity = int(data["quantity_lots"])
        if action != "SKIP" and quantity <= 0:
            raise ValueError("quantity_lots must be positive")

        limit_price = None
        if action != "SKIP":
            if data.get("limit_price") is None:
                raise ValueError("limit_price is required for BUY/SELL")
            limit_price = Decimal(str(data["limit_price"]))
            if limit_price <= 0:
                raise ValueError("limit_price must be positive")

        ticker = str(data["ticker"]).upper().strip()
        class_code = str(data["class_code"]).upper().strip()
        expected_instrument_id = f"{ticker}_{class_code}"
        instrument_id = str(data["instrument_id"]).upper().strip()
        if instrument_id != expected_instrument_id:
            raise ValueError(
                f"instrument_id mismatch: expected {expected_instrument_id}, got {instrument_id}"
            )

        return cls(
            protocol_version=str(data["protocol_version"]),
            signal_id=str(data["signal_id"]),
            auth_token=str(data["auth_token"]),
            action=action,
            ticker=ticker,
            class_code=class_code,
            instrument_id=instrument_id,
            order_type=order_type,
            quantity_lots=quantity,
            limit_price=limit_price,
            expires_at=parse_iso_utc(str(data["expires_at"])),
            reviewer_note=str(data.get("reviewer_note", "")).strip(),
        )

    @classmethod
    def from_json(cls, raw: str) -> "TradeCommand":
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("Command payload must be a JSON object")
        return cls.from_dict(obj)
