from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any


PROTOCOL_VERSION = "2"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def _positive_decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field} must be a decimal number") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return result


def _signal_auth_payload(
    *,
    signal_id: str,
    created_at: str,
    ticker: str,
    class_code: str,
    instrument_uid: str,
    instrument_type: str,
    execution_capability: bool,
) -> bytes:
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "signal_id": str(signal_id),
        "created_at": str(created_at),
        "ticker": str(ticker).upper().strip(),
        "class_code": str(class_code).upper().strip(),
        "instrument_uid": str(instrument_uid).strip(),
        "instrument_type": str(instrument_type).lower().strip(),
        "execution_capability": bool(execution_capability),
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def make_auth_token(
    secret: str,
    *,
    signal_id: str,
    created_at: str,
    ticker: str,
    class_code: str,
    instrument_uid: str,
    instrument_type: str,
    execution_capability: bool,
) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        _signal_auth_payload(
            signal_id=signal_id,
            created_at=created_at,
            ticker=ticker,
            class_code=class_code,
            instrument_uid=instrument_uid,
            instrument_type=instrument_type,
            execution_capability=execution_capability,
        ),
        hashlib.sha256,
    ).hexdigest()


def verify_auth_token(
    secret: str,
    *,
    signal_id: str,
    created_at: str,
    ticker: str,
    class_code: str,
    instrument_uid: str,
    instrument_type: str,
    execution_capability: bool,
    token: str,
) -> bool:
    expected = make_auth_token(
        secret,
        signal_id=signal_id,
        created_at=created_at,
        ticker=ticker,
        class_code=class_code,
        instrument_uid=instrument_uid,
        instrument_type=instrument_type,
        execution_capability=execution_capability,
    )
    return hmac.compare_digest(expected, str(token))


def quotation_from_decimal(value: Decimal) -> dict[str, Any]:
    if not value.is_finite() or value <= 0:
        raise ValueError("Price must be finite and positive")
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
    instrument_uid: str
    instrument_type: str
    execution_capability: bool
    observed_price: str
    reason: str
    auth_token: str

    @classmethod
    def create(
        cls,
        *,
        secret: str,
        ticker: str,
        class_code: str,
        instrument_uid: str,
        instrument_type: str,
        observed_price: Decimal,
        reason: str,
        execution_capability: bool = False,
    ) -> "Signal":
        signal_id = str(uuid.uuid4())
        created_at = utc_now().isoformat()
        ticker = ticker.upper().strip()
        class_code = class_code.upper().strip()
        instrument_uid = instrument_uid.strip()
        instrument_type = instrument_type.lower().strip()
        if not instrument_uid:
            raise ValueError("instrument_uid is required")
        if not instrument_type:
            raise ValueError("instrument_type is required")
        if not observed_price.is_finite() or observed_price <= 0:
            raise ValueError("observed_price must be finite and positive")

        auth_token = make_auth_token(
            secret,
            signal_id=signal_id,
            created_at=created_at,
            ticker=ticker,
            class_code=class_code,
            instrument_uid=instrument_uid,
            instrument_type=instrument_type,
            execution_capability=execution_capability,
        )
        return cls(
            protocol_version=PROTOCOL_VERSION,
            signal_id=signal_id,
            created_at=created_at,
            ticker=ticker,
            class_code=class_code,
            instrument_id=f"{ticker}_{class_code}",
            instrument_uid=instrument_uid,
            instrument_type=instrument_type,
            execution_capability=execution_capability,
            observed_price=str(observed_price),
            reason=reason.strip(),
            auth_token=auth_token,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class TradeCommand:
    protocol_version: str
    signal_id: str
    signal_created_at: str
    auth_token: str
    action: str
    ticker: str
    class_code: str
    instrument_id: str
    instrument_uid: str
    instrument_type: str
    execution_capability: bool
    order_type: str
    quantity_lots: int
    limit_price: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    time_stop: datetime | None
    expires_at: datetime
    reviewer_note: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TradeCommand":
        required = [
            "protocol_version",
            "signal_id",
            "signal_created_at",
            "auth_token",
            "action",
            "ticker",
            "class_code",
            "instrument_id",
            "instrument_uid",
            "instrument_type",
            "execution_capability",
            "order_type",
            "quantity_lots",
            "expires_at",
        ]
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError(f"Missing command fields: {', '.join(missing)}")

        if str(data["protocol_version"]) != PROTOCOL_VERSION:
            raise ValueError(f"protocol_version must be {PROTOCOL_VERSION}")

        try:
            uuid.UUID(str(data["signal_id"]))
        except Exception as exc:
            raise ValueError("signal_id must be UUID") from exc

        parse_iso_utc(str(data["signal_created_at"]))

        if not isinstance(data["execution_capability"], bool):
            raise ValueError("execution_capability must be boolean")

        action = str(data["action"]).upper().strip()
        if action not in {"BUY", "SELL", "SKIP"}:
            raise ValueError("action must be BUY, SELL or SKIP")

        order_type = str(data["order_type"]).upper().strip()
        if action != "SKIP" and order_type != "LIMIT":
            raise ValueError("Only LIMIT orders are permitted")

        quantity = int(data["quantity_lots"])
        if action == "SKIP":
            if quantity != 0:
                raise ValueError("SKIP quantity_lots must be 0")
        elif quantity <= 0:
            raise ValueError("quantity_lots must be positive")

        limit_price = None
        if action != "SKIP":
            if data.get("limit_price") is None:
                raise ValueError("limit_price is required for BUY/SELL")
            limit_price = _positive_decimal(data["limit_price"], "limit_price")

        stop_loss = None
        take_profit = None
        time_stop = None
        if action == "BUY":
            if data.get("stop_loss") is None:
                raise ValueError("stop_loss is required for BUY")
            if data.get("take_profit") is None:
                raise ValueError("take_profit is required for BUY")
            if data.get("time_stop") is None:
                raise ValueError("time_stop is required for BUY")
            stop_loss = _positive_decimal(data["stop_loss"], "stop_loss")
            take_profit = _positive_decimal(data["take_profit"], "take_profit")
            time_stop = parse_iso_utc(str(data["time_stop"]))
            if not (stop_loss < limit_price < take_profit):
                raise ValueError(
                    "For LONG BUY require stop_loss < limit_price < take_profit"
                )

        ticker = str(data["ticker"]).upper().strip()
        class_code = str(data["class_code"]).upper().strip()
        expected_instrument_id = f"{ticker}_{class_code}"
        instrument_id = str(data["instrument_id"]).upper().strip()
        if instrument_id != expected_instrument_id:
            raise ValueError(
                f"instrument_id mismatch: expected {expected_instrument_id}, "
                f"got {instrument_id}"
            )

        instrument_uid = str(data["instrument_uid"]).strip()
        instrument_type = str(data["instrument_type"]).lower().strip()
        if not instrument_uid:
            raise ValueError("instrument_uid is required")
        if not instrument_type:
            raise ValueError("instrument_type is required")

        return cls(
            protocol_version=PROTOCOL_VERSION,
            signal_id=str(data["signal_id"]),
            signal_created_at=str(data["signal_created_at"]),
            auth_token=str(data["auth_token"]),
            action=action,
            ticker=ticker,
            class_code=class_code,
            instrument_id=instrument_id,
            instrument_uid=instrument_uid,
            instrument_type=instrument_type,
            execution_capability=data["execution_capability"],
            order_type=order_type,
            quantity_lots=quantity,
            limit_price=limit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            time_stop=time_stop,
            expires_at=parse_iso_utc(str(data["expires_at"])),
            reviewer_note=str(data.get("reviewer_note", "")).strip(),
        )

    @classmethod
    def from_json(cls, raw: str) -> "TradeCommand":
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("Command payload must be a JSON object")
        return cls.from_dict(obj)
