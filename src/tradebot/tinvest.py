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


def _quotation_to_decimal(value: Any) -> Decimal:
    if not isinstance(value, dict):
        raise RuntimeError("Quotation value is unavailable")
    result = (
        Decimal(str(value.get("units", "0")))
        + Decimal(str(value.get("nano", 0)))
        / Decimal("1000000000")
    )
    return result


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

        query_upper = query.upper()
        search_query = query
        if "_" in query_upper:
            ticker, _class_code = query_upper.rsplit("_", 1)
            search_query = ticker

        url = self.INSTRUMENTS_SERVICE + "/FindInstrument"
        data = self._post(
            url,
            {"query": search_query, "apiTradeAvailableFlag": True},
        )
        instruments = data.get("instruments") or []
        if not instruments:
            raise RuntimeError(f"Instrument not found or not API-tradable: {query}")

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


    def get_order_state(
        self,
        order_id: str,
        *,
        order_id_type: str = "ORDER_ID_TYPE_EXCHANGE",
    ) -> dict[str, Any]:
        return self._post(
            self.SANDBOX_SERVICE + "/GetSandboxOrderState",
            {
                "accountId": self.account_id,
                "orderId": order_id,
                "priceType": "PRICE_TYPE_CURRENCY",
                "orderIdType": order_id_type,
            },
        )

    def get_stop_orders(
        self,
        *,
        status: str = "STOP_ORDER_STATUS_ALL",
        from_time: datetime | None = None,
        to_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "accountId": self.account_id,
            "status": status,
        }
        if from_time is not None:
            payload["from"] = from_time.isoformat()
        if to_time is not None:
            payload["to"] = to_time.isoformat()
        data = self._post(
            self.SANDBOX_SERVICE + "/GetSandboxStopOrders",
            payload,
        )
        return data.get("stopOrders") or data.get("stop_orders") or []

    def post_stop_order(
        self,
        *,
        instrument_uid: str,
        quantity_lots: int,
        stop_price: Decimal,
        stop_order_type: str,
        idempotency_seed: str,
    ) -> dict[str, Any]:
        if quantity_lots <= 0:
            raise ValueError("quantity_lots must be positive")
        if not stop_price.is_finite() or stop_price <= 0:
            raise ValueError("stop_price must be finite and positive")
        if stop_order_type not in {
            "STOP_ORDER_TYPE_STOP_LOSS",
            "STOP_ORDER_TYPE_TAKE_PROFIT",
        }:
            raise ValueError("Unsupported protective stop order type")

        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_seed))
        payload: dict[str, Any] = {
            "quantity": str(quantity_lots),
            "price": quotation_from_decimal(stop_price),
            "stopPrice": quotation_from_decimal(stop_price),
            "direction": "STOP_ORDER_DIRECTION_SELL",
            "accountId": self.account_id,
            "expirationType": "STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL",
            "stopOrderType": stop_order_type,
            "instrumentId": instrument_uid,
            "priceType": "PRICE_TYPE_CURRENCY",
            "orderId": request_id,
            "confirmMarginTrade": False,
        }
        if stop_order_type == "STOP_ORDER_TYPE_TAKE_PROFIT":
            # Profit-taking remains a LIMIT exit.
            payload["exchangeOrderType"] = "EXCHANGE_ORDER_TYPE_LIMIT"
            payload["takeProfitType"] = "TAKE_PROFIT_TYPE_REGULAR"
        else:
            # STOP_ORDER_TYPE_STOP_LOSS is the broker-native emergency stop.
            # Do not force exchangeOrderType here: T-Invest documents that
            # field for the child order of take-profit. The live Sandbox smoke
            # test must validate actual stop-loss activation semantics.
            pass

        result = self._post(
            self.SANDBOX_SERVICE + "/PostSandboxStopOrder",
            payload,
        )
        return {
            "request_order_id": request_id,
            "stop_order": result,
        }

    def cancel_stop_order(self, stop_order_id: str) -> dict[str, Any]:
        return self._post(
            self.SANDBOX_SERVICE + "/CancelSandboxStopOrder",
            {
                "accountId": self.account_id,
                "stopOrderId": stop_order_id,
            },
        )

    def get_position_lots(
        self,
        *,
        instrument_uid: str,
        lot_size: int,
    ) -> int:
        if lot_size <= 0:
            raise ValueError("lot_size must be positive")
        portfolio = self.get_portfolio()
        for position in portfolio.get("positions") or []:
            uid = str(
                position.get("instrumentUid")
                or position.get("instrument_uid")
                or ""
            )
            if uid != instrument_uid:
                continue

            qlots = position.get("quantityLots") or position.get("quantity_lots")
            if isinstance(qlots, dict):
                quantity_lots = (
                    Decimal(str(qlots.get("units", "0")))
                    + Decimal(str(qlots.get("nano", 0)))
                    / Decimal("1000000000")
                )
                if quantity_lots < 0 or quantity_lots != quantity_lots.to_integral_value():
                    raise RuntimeError("Unexpected non-integer long position lots")
                return int(quantity_lots)

            quantity = position.get("quantity")
            if isinstance(quantity, dict):
                units = (
                    Decimal(str(quantity.get("units", "0")))
                    + Decimal(str(quantity.get("nano", 0)))
                    / Decimal("1000000000")
                )
                lots = units / Decimal(lot_size)
                if lots < 0 or lots != lots.to_integral_value():
                    raise RuntimeError("Unexpected non-integer long position")
                return int(lots)
        return 0

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
