from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import requests

from .protocol import quotation_from_decimal


class TInvestSandboxClient:
    """
    Intentionally hard-wired to the Sandbox host.
    There is no production URL switch in this scaffold.
    """

    BASE = "https://sandbox-invest-public-api.tbank.ru/rest"
    SANDBOX_SERVICE = BASE + "/tinkoff.public.invest.api.contract.v1.SandboxService"
    INSTRUMENTS_SERVICE = BASE + "/tinkoff.public.invest.api.contract.v1.InstrumentsService"

    def __init__(self, token: str, account_id: str, timeout: float = 10.0):
        self.account_id = account_id
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(url, json=payload, timeout=self.timeout)
        if not response.ok:
            raise RuntimeError(
                f"T-Invest API error {response.status_code}: {response.text[:1000]}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected T-Invest response")
        return data

    def find_instrument(self, query: str) -> dict[str, Any]:
        url = self.INSTRUMENTS_SERVICE + "/FindInstrument"
        data = self._post(url, {"query": query, "apiTradeAvailableFlag": True})
        instruments = data.get("instruments") or []
        if not instruments:
            raise RuntimeError(f"Instrument not found or not API-tradable: {query}")

        ticker = query.split("_", 1)[0].upper()
        class_code = query.split("_", 1)[1].upper() if "_" in query else None
        exact = [
            x for x in instruments
            if str(x.get("ticker", "")).upper() == ticker
            and (class_code is None or str(x.get("classCode", "")).upper() == class_code)
        ]
        return (exact or instruments)[0]

    def get_positions(self) -> dict[str, Any]:
        url = self.SANDBOX_SERVICE + "/GetSandboxPositions"
        return self._post(url, {"accountId": self.account_id})

    def _assert_sell_is_covered(self, instrument: dict[str, Any], lots: int) -> None:
        uid = str(instrument.get("uid") or instrument.get("instrumentUid") or "")
        lot = int(instrument.get("lot") or 1)
        if not uid:
            raise RuntimeError("Instrument UID missing; refusing SELL")

        positions = self.get_positions()
        securities = positions.get("securities") or []
        item = next(
            (
                x for x in securities
                if str(x.get("instrumentUid") or x.get("instrument_uid") or "") == uid
            ),
            None,
        )
        if item is None:
            raise RuntimeError("SELL refused: no long position found")

        available_units = int(item.get("balance") or 0)
        required_units = lots * lot
        if available_units < required_units:
            raise RuntimeError(
                f"SELL refused: need {required_units} units, available {available_units}"
            )

    def post_limit_order(
        self,
        *,
        instrument_id: str,
        side: str,
        quantity_lots: int,
        limit_price: Decimal,
        idempotency_seed: str,
    ) -> dict[str, Any]:
        side = side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if quantity_lots <= 0:
            raise ValueError("quantity_lots must be positive")

        instrument = self.find_instrument(instrument_id)
        if side == "SELL":
            self._assert_sell_is_covered(instrument, quantity_lots)

        order_id = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_seed))
        payload = {
            "quantity": str(quantity_lots),
            "price": quotation_from_decimal(limit_price),
            "direction": f"ORDER_DIRECTION_{side}",
            "accountId": self.account_id,
            "orderType": "ORDER_TYPE_LIMIT",
            "orderId": order_id,
            "instrumentId": instrument_id,
            "timeInForce": "TIME_IN_FORCE_DAY",
            "priceType": "PRICE_TYPE_CURRENCY",
            "confirmMarginTrade": False,
        }

        url = self.SANDBOX_SERVICE + "/PostSandboxOrder"
        return self._post(url, payload)
