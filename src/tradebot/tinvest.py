from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests

from .protocol import quotation_from_decimal


def _tbank_ca_bundle() -> str:
    path = Path(__file__).resolve().parents[2] / "certs" / "RussianTrustedRootCA.pem"
    if not path.is_file():
        raise RuntimeError(f"T-Bank CA certificate not found: {path}")
    return str(path)


def _as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    return int(value)


class TInvestSandboxClient:
    """
    Hard-wired to T-Invest Sandbox. There is intentionally no production host
    switch in this repository.
    """

    BASE = "https://sandbox-invest-public-api.tbank.ru/rest"
    SANDBOX_SERVICE = (
        BASE + "/tinkoff.public.invest.api.contract.v1.SandboxService"
    )
    INSTRUMENTS_SERVICE = (
        BASE + "/tinkoff.public.invest.api.contract.v1.InstrumentsService"
    )
    MARKET_DATA_SERVICE = (
        BASE + "/tinkoff.public.invest.api.contract.v1.MarketDataService"
    )

    def __init__(
        self,
        token: str,
        account_name: str = "github-moex-trade-bot",
        account_id: str | None = None,
        timeout: float = 10.0,
    ):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = _tbank_ca_bundle()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        self.account_id = account_id or self._find_account_id(account_name)

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(url, json=payload, timeout=self.timeout)
        if not response.ok:
            raise RuntimeError(
                f"T-Invest API error {response.status_code}: "
                f"{response.text[:1000]}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected T-Invest response")
        return data

    def _find_account_id(self, account_name: str) -> str:
        url = self.SANDBOX_SERVICE + "/GetSandboxAccounts"
        data = self._post(url, {"status": "ACCOUNT_STATUS_OPEN"})
        accounts = data.get("accounts") or []

        exact = [
            account
            for account in accounts
            if str(account.get("name", "")).strip() == account_name
        ]
        if len(exact) != 1:
            raise RuntimeError(
                f'Expected exactly one sandbox account named "{account_name}", '
                f"found {len(exact)}"
            )

        account_id = str(
            exact[0].get("id") or exact[0].get("accountId") or ""
        )
        if not account_id:
            raise RuntimeError("Sandbox account found but account ID is missing")
        return account_id

    def list_instruments(self, instrument_type: str) -> list[dict[str, Any]]:
        endpoint_map = {
            "share": "Shares",
            "etf": "Etfs",
            "bond": "Bonds",
            "currency": "Currencies",
            "futures": "Futures",
            "option": "Options",
            "dfa": "Dfas",
        }
        endpoint = endpoint_map.get(instrument_type)
        if endpoint is None:
            raise ValueError(f"Unsupported instrument_type: {instrument_type}")

        payload: dict[str, Any]
        if instrument_type == "dfa":
            payload = {}
        else:
            payload = {"instrumentStatus": "INSTRUMENT_STATUS_BASE"}

        data = self._post(
            self.INSTRUMENTS_SERVICE + "/" + endpoint,
            payload,
        )
        instruments = data.get("instruments") or []
        if not isinstance(instruments, list):
            raise RuntimeError("Unexpected instruments response")
        return instruments

    def get_last_prices(self, instrument_uids: list[str]) -> list[dict[str, Any]]:
        if len(instrument_uids) > 100:
            raise ValueError("GetLastPrices batch must contain <= 100 instruments")
        data = self._post(
            self.MARKET_DATA_SERVICE + "/GetLastPrices",
            {
                "instrumentId": instrument_uids,
                "lastPriceType": "LAST_PRICE_EXCHANGE",
                "instrumentStatus": "INSTRUMENT_STATUS_BASE",
            },
        )
        return data.get("lastPrices") or data.get("last_prices") or []

    def get_close_prices(self, instrument_uids: list[str]) -> list[dict[str, Any]]:
        if len(instrument_uids) > 100:
            raise ValueError("GetClosePrices batch must contain <= 100 instruments")
        data = self._post(
            self.MARKET_DATA_SERVICE + "/GetClosePrices",
            {
                "instruments": [
                    {"instrumentId": uid}
                    for uid in instrument_uids
                ],
                "instrumentStatus": "INSTRUMENT_STATUS_BASE",
            },
        )
        return data.get("closePrices") or data.get("close_prices") or []

    def get_order_book(
        self,
        instrument_uid: str,
        depth: int = 10,
    ) -> dict[str, Any]:
        if depth not in {1, 10, 20, 30, 40, 50}:
            raise ValueError("Unsupported order book depth")
        return self._post(
            self.MARKET_DATA_SERVICE + "/GetOrderBook",
            {"instrumentId": instrument_uid, "depth": depth},
        )

    def get_trading_status(self, instrument_uid: str) -> dict[str, Any]:
        return self._post(
            self.MARKET_DATA_SERVICE + "/GetTradingStatus",
            {"instrumentId": instrument_uid},
        )

    def get_candles(
        self,
        *,
        instrument_uid: str,
        from_time: datetime,
        to_time: datetime,
        interval: str,
    ) -> list[dict[str, Any]]:
        data = self._post(
            self.MARKET_DATA_SERVICE + "/GetCandles",
            {
                "instrumentId": instrument_uid,
                "from": from_time.isoformat(),
                "to": to_time.isoformat(),
                "interval": interval,
                "candleSourceType": "CANDLE_SOURCE_EXCHANGE",
            },
        )
        return data.get("candles") or []

    def find_instrument(self, query: str) -> dict[str, Any]:
        """
        Exact-only instrument resolution. Never fall back to the first fuzzy
        FindInstrument result.
        """
        query = query.strip()
        if not query:
            raise ValueError("Instrument query is empty")

        url = self.INSTRUMENTS_SERVICE + "/FindInstrument"
        data = self._post(
            url,
            {"query": query, "apiTradeAvailableFlag": True},
        )
        instruments = data.get("instruments") or []
        if not instruments:
            raise RuntimeError(f"Instrument not found or not API-tradable: {query}")

        query_upper = query.upper()
        exact_uid = [
            x
            for x in instruments
            if str(x.get("uid") or x.get("instrumentUid") or "") == query
        ]

        exact_ticker_class: list[dict[str, Any]] = []
        if "_" in query_upper:
            ticker, class_code = query_upper.rsplit("_", 1)
            exact_ticker_class = [
                x
                for x in instruments
                if str(x.get("ticker", "")).upper() == ticker
                and str(x.get("classCode") or x.get("class_code") or "").upper()
                == class_code
            ]

        exact_figi = [
            x
            for x in instruments
            if str(x.get("figi", "")).upper() == query_upper
        ]

        exact = exact_uid or exact_ticker_class or exact_figi
        if len(exact) != 1:
            raise RuntimeError(
                f"Instrument resolution must be exact and unique: {query}; "
                f"exact matches={len(exact)}"
            )
        return exact[0]

    def resolve_instrument(
        self,
        *,
        ticker: str,
        class_code: str,
        instrument_uid: str,
    ) -> dict[str, Any]:
        instrument = self.find_instrument(instrument_uid)
        actual_uid = str(
            instrument.get("uid") or instrument.get("instrumentUid") or ""
        )
        actual_ticker = str(instrument.get("ticker") or "").upper()
        actual_class = str(
            instrument.get("classCode")
            or instrument.get("class_code")
            or ""
        ).upper()

        if actual_uid != instrument_uid:
            raise RuntimeError("Resolved instrument UID mismatch")
        if actual_ticker != ticker.upper():
            raise RuntimeError(
                f"Resolved ticker mismatch: expected {ticker}, got {actual_ticker}"
            )
        if actual_class != class_code.upper():
            raise RuntimeError(
                f"Resolved class_code mismatch: expected {class_code}, "
                f"got {actual_class}"
            )
        return instrument

    def get_positions(self) -> dict[str, Any]:
        url = self.SANDBOX_SERVICE + "/GetSandboxPositions"
        return self._post(url, {"accountId": self.account_id})

    def get_operations_by_cursor(
        self,
        *,
        from_time: datetime,
        to_time: datetime,
        limit: int = 1000,
    ) -> dict[str, Any]:
        if limit < 3 or limit > 1000:
            raise ValueError("operations page limit must be between 3 and 1000")

        url = self.SANDBOX_SERVICE + "/GetSandboxOperationsByCursor"
        items: list[dict[str, Any]] = []
        cursor = ""

        while True:
            payload: dict[str, Any] = {
                "accountId": self.account_id,
                "from": from_time.isoformat(),
                "to": to_time.isoformat(),
                "limit": limit,
                "state": "OPERATION_STATE_EXECUTED",
                "withoutCommissions": False,
                "withoutTrades": False,
                "withoutOvernights": False,
            }
            if cursor:
                payload["cursor"] = cursor

            page = self._post(url, payload)
            page_items = page.get("items") or []
            items.extend(page_items)

            has_next = bool(
                page.get("hasNext")
                if "hasNext" in page
                else page.get("has_next", False)
            )
            next_cursor = str(
                page.get("nextCursor")
                or page.get("next_cursor")
                or ""
            )
            if not has_next:
                break
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError("Operations pagination returned invalid cursor")
            cursor = next_cursor

        return {
            "items": items,
            "count": len(items),
            "from": from_time.isoformat(),
            "to": to_time.isoformat(),
        }

    def get_portfolio(self) -> dict[str, Any]:
        url = self.SANDBOX_SERVICE + "/GetSandboxPortfolio"
        return self._post(
            url,
            {"accountId": self.account_id, "currency": "RUB"},
        )

    def get_max_lots(
        self,
        *,
        instrument_uid: str,
        limit_price: Decimal,
    ) -> dict[str, Any]:
        url = self.SANDBOX_SERVICE + "/GetSandboxMaxLots"
        return self._post(
            url,
            {
                "accountId": self.account_id,
                "instrumentId": instrument_uid,
                "price": quotation_from_decimal(limit_price),
            },
        )

    def get_order_price(
        self,
        *,
        instrument_uid: str,
        side: str,
        quantity_lots: int,
        limit_price: Decimal,
    ) -> dict[str, Any]:
        url = self.SANDBOX_SERVICE + "/GetSandboxOrderPrice"
        return self._post(
            url,
            {
                "accountId": self.account_id,
                "instrumentId": instrument_uid,
                "price": quotation_from_decimal(limit_price),
                "direction": f"ORDER_DIRECTION_{side}",
                "quantity": str(quantity_lots),
            },
        )

    def _assert_own_funds_or_position(
        self,
        *,
        instrument_uid: str,
        side: str,
        quantity_lots: int,
        limit_price: Decimal,
    ) -> dict[str, Any]:
        limits = self.get_max_lots(
            instrument_uid=instrument_uid,
            limit_price=limit_price,
        )
        if side == "BUY":
            own = limits.get("buyLimits") or limits.get("buy_limits") or {}
            allowed = _as_int(
                own.get("buyMaxLots") or own.get("buy_max_lots")
            )
            if quantity_lots > allowed:
                raise RuntimeError(
                    f"BUY refused: {quantity_lots} lots requested, "
                    f"{allowed} lots available on own funds"
                )
        else:
            own = limits.get("sellLimits") or limits.get("sell_limits") or {}
            allowed = _as_int(
                own.get("sellMaxLots") or own.get("sell_max_lots")
            )
            if quantity_lots > allowed:
                raise RuntimeError(
                    f"SELL refused: {quantity_lots} lots requested, "
                    f"{allowed} lots available in own position"
                )
        return limits

    def prepare_limit_order(
        self,
        *,
        ticker: str,
        class_code: str,
        instrument_uid: str,
        side: str,
        quantity_lots: int,
        limit_price: Decimal,
    ) -> dict[str, Any]:
        side = side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if quantity_lots <= 0:
            raise ValueError("quantity_lots must be positive")
        if not limit_price.is_finite() or limit_price <= 0:
            raise ValueError("limit_price must be finite and positive")

        instrument = self.resolve_instrument(
            ticker=ticker,
            class_code=class_code,
            instrument_uid=instrument_uid,
        )
        limits = self._assert_own_funds_or_position(
            instrument_uid=instrument_uid,
            side=side,
            quantity_lots=quantity_lots,
            limit_price=limit_price,
        )
        preflight = self.get_order_price(
            instrument_uid=instrument_uid,
            side=side,
            quantity_lots=quantity_lots,
            limit_price=limit_price,
        )
        return {
            "instrument": instrument,
            "limits": limits,
            "preflight_order_price": preflight,
            "portfolio": self.get_portfolio(),
        }

    def post_limit_order(
        self,
        *,
        ticker: str,
        class_code: str,
        instrument_uid: str,
        side: str,
        quantity_lots: int,
        limit_price: Decimal,
        idempotency_seed: str,
        prepared: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        side = side.upper()
        if prepared is None:
            prepared = self.prepare_limit_order(
                ticker=ticker,
                class_code=class_code,
                instrument_uid=instrument_uid,
                side=side,
                quantity_lots=quantity_lots,
                limit_price=limit_price,
            )

        instrument = prepared["instrument"]
        limits = prepared["limits"]
        preflight = prepared["preflight_order_price"]

        order_id = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_seed))
        payload = {
            "quantity": str(quantity_lots),
            "price": quotation_from_decimal(limit_price),
            "direction": f"ORDER_DIRECTION_{side}",
            "accountId": self.account_id,
            "orderType": "ORDER_TYPE_LIMIT",
            "orderId": order_id,
            "instrumentId": instrument_uid,
            # Do not leave an analyzed price resting until the end of day.
            "timeInForce": "TIME_IN_FORCE_FILL_AND_KILL",
            "priceType": "PRICE_TYPE_CURRENCY",
            "confirmMarginTrade": False,
        }

        url = self.SANDBOX_SERVICE + "/PostSandboxOrder"
        result = self._post(url, payload)
        return {
            "instrument": {
                "uid": instrument_uid,
                "ticker": str(instrument.get("ticker") or ""),
                "class_code": str(
                    instrument.get("classCode")
                    or instrument.get("class_code")
                    or ""
                ),
                "instrument_type": str(
                    instrument.get("instrumentType")
                    or instrument.get("instrument_type")
                    or ""
                ),
            },
            "own_funds_or_position_limits": limits,
            "preflight_order_price": preflight,
            "post_order": result,
            "request_order_id": order_id,
        }
