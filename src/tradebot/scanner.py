from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .tinvest import TInvestSandboxClient


INSTRUMENT_TYPES = (
    "share",
    "etf",
    "bond",
    "currency",
    "futures",
    "option",
    "dfa",
)

MIN_ABS_MOVE_PERCENT = {
    "share": Decimal("0.30"),
    "etf": Decimal("0.20"),
    "bond": Decimal("0.10"),
    "currency": Decimal("0.15"),
    "futures": Decimal("0.30"),
    "option": Decimal("1.00"),
    "dfa": Decimal("0.20"),
}

MAX_LAST_PRICE_AGE_MINUTES = {
    "share": 30,
    "etf": 30,
    "bond": 120,
    "currency": 30,
    "futures": 30,
    "option": 30,
    "dfa": 120,
}


def quote_to_decimal(value: Any) -> Decimal:
    if not isinstance(value, dict):
        return Decimal("0")
    return (
        Decimal(str(value.get("units", "0")))
        + Decimal(str(value.get("nano", 0))) / Decimal("1000000000")
    )


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def ema(values: list[Decimal], period: int) -> Decimal | None:
    if len(values) < period or period <= 0:
        return None
    k = Decimal("2") / Decimal(period + 1)
    current = sum(values[:period]) / Decimal(period)
    for value in values[period:]:
        current = value * k + current * (Decimal("1") - k)
    return current


def rsi(values: list[Decimal], period: int = 14) -> Decimal | None:
    if len(values) < period + 1:
        return None
    deltas = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ][-period:]
    gains = sum((d for d in deltas if d > 0), Decimal("0"))
    losses = sum((-d for d in deltas if d < 0), Decimal("0"))
    avg_gain = gains / Decimal(period)
    avg_loss = losses / Decimal(period)
    if avg_loss == 0:
        return Decimal("100")
    rs = avg_gain / avg_loss
    return Decimal("100") - (
        Decimal("100") / (Decimal("1") + rs)
    )


def atr(candles: list[dict[str, Any]], period: int = 14) -> Decimal | None:
    completed = [c for c in candles if c.get("isComplete", c.get("is_complete", True))]
    if len(completed) < period + 1:
        return None

    trs: list[Decimal] = []
    previous_close: Decimal | None = None
    for candle in completed:
        high = quote_to_decimal(candle.get("high"))
        low = quote_to_decimal(candle.get("low"))
        close = quote_to_decimal(candle.get("close"))
        if high <= 0 or low <= 0 or close <= 0:
            continue
        if previous_close is None:
            tr = high - low
        else:
            tr = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        trs.append(tr)
        previous_close = close

    if len(trs) < period:
        return None
    return sum(trs[-period:]) / Decimal(period)


def vwap(candles: list[dict[str, Any]]) -> Decimal | None:
    numerator = Decimal("0")
    denominator = Decimal("0")
    for candle in candles:
        high = quote_to_decimal(candle.get("high"))
        low = quote_to_decimal(candle.get("low"))
        close = quote_to_decimal(candle.get("close"))
        volume = Decimal(str(candle.get("volume", "0")))
        if high <= 0 or low <= 0 or close <= 0 or volume <= 0:
            continue
        typical = (high + low + close) / Decimal("3")
        numerator += typical * volume
        denominator += volume
    if denominator <= 0:
        return None
    return numerator / denominator


def _chunks(values: list[str], size: int = 100):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _uid(item: dict[str, Any]) -> str:
    return str(item.get("uid") or item.get("instrumentUid") or "")


def _class_code(item: dict[str, Any]) -> str:
    return str(item.get("classCode") or item.get("class_code") or "").upper()


def _eligible(item: dict[str, Any]) -> bool:
    if item.get("apiTradeAvailableFlag") is False:
        return False
    if item.get("buyAvailableFlag") is False:
        return False
    if item.get("forQualInvestorFlag") is True:
        return False
    if not _uid(item):
        return False
    if not str(item.get("ticker") or "").strip():
        return False
    if not _class_code(item):
        return False
    return True


def _price_map(items: list[dict[str, Any]], price_field: str) -> dict[str, dict[str, Any]]:
    result = {}
    for item in items:
        uid = str(item.get("instrumentUid") or item.get("instrument_uid") or "")
        if not uid:
            continue
        result[uid] = item
    return result


def scan_candidates(
    client: TInvestSandboxClient,
    *,
    max_per_type: int = 2,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    all_candidates: list[dict[str, Any]] = []

    for instrument_type in INSTRUMENT_TYPES:
        try:
            universe = [
                item
                for item in client.list_instruments(instrument_type)
                if _eligible(item)
            ]
        except Exception as exc:
            print(
                f"Scanner universe skipped for {instrument_type}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        uids = [_uid(x) for x in universe]
        last_items: list[dict[str, Any]] = []
        close_items: list[dict[str, Any]] = []
        for batch in _chunks(uids):
            if not batch:
                continue
            try:
                last_items.extend(client.get_last_prices(batch))
                close_items.extend(client.get_close_prices(batch))
            except Exception as exc:
                print(
                    f"Scanner price batch skipped for {instrument_type}: "
                    f"{type(exc).__name__}: {exc}"
                )

        last_map = _price_map(last_items, "price")
        close_map = _price_map(close_items, "price")

        type_candidates: list[dict[str, Any]] = []
        for instrument in universe:
            uid = _uid(instrument)
            last = last_map.get(uid)
            close = close_map.get(uid)
            if not last or not close:
                continue

            last_price = quote_to_decimal(last.get("price"))
            close_price = quote_to_decimal(close.get("price"))
            if last_price <= 0 or close_price <= 0:
                continue

            last_time = parse_ts(
                last.get("time")
                or last.get("lastPriceTs")
                or last.get("last_price_ts")
            )
            if last_time is None:
                continue
            age = (now - last_time).total_seconds() / 60
            if age < -2 or age > MAX_LAST_PRICE_AGE_MINUTES[instrument_type]:
                continue

            move_pct = (
                (last_price - close_price)
                / close_price
                * Decimal("100")
            )
            if abs(move_pct) < MIN_ABS_MOVE_PERCENT[instrument_type]:
                continue

            type_candidates.append(
                {
                    "instrument_type": instrument_type,
                    "instrument": instrument,
                    "instrument_uid": uid,
                    "ticker": str(instrument.get("ticker") or "").upper(),
                    "class_code": _class_code(instrument),
                    "last_price": last_price,
                    "close_price": close_price,
                    "last_price_time": last_time,
                    "move_percent": move_pct,
                    "score": abs(move_pct),
                }
            )

        type_candidates.sort(
            key=lambda x: x["score"],
            reverse=True,
        )
        all_candidates.extend(type_candidates[:max_per_type])

    all_candidates.sort(
        key=lambda x: x["score"],
        reverse=True,
    )
    return all_candidates


def _compact_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
    positions = []
    for p in portfolio.get("positions") or []:
        quantity = quote_to_decimal(p.get("quantity"))
        if quantity == 0:
            continue
        positions.append(
            {
                "ticker": p.get("ticker"),
                "class_code": p.get("classCode") or p.get("class_code"),
                "instrument_uid": p.get("instrumentUid") or p.get("instrument_uid"),
                "instrument_type": p.get("instrumentType") or p.get("instrument_type"),
                "quantity": str(quantity),
                "current_price": str(quote_to_decimal(p.get("currentPrice") or p.get("current_price"))),
                "daily_yield": p.get("dailyYield") or p.get("daily_yield"),
            }
        )
    return {
        "total_amount_portfolio": portfolio.get("totalAmountPortfolio")
        or portfolio.get("total_amount_portfolio"),
        "daily_yield": portfolio.get("dailyYield")
        or portfolio.get("daily_yield"),
        "daily_yield_relative": portfolio.get("dailyYieldRelative")
        or portfolio.get("daily_yield_relative"),
        "open_positions": positions,
    }


def _candle_summary(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [
        quote_to_decimal(c.get("close"))
        for c in candles
        if quote_to_decimal(c.get("close")) > 0
    ]
    volumes = [
        Decimal(str(c.get("volume", "0")))
        for c in candles
        if Decimal(str(c.get("volume", "0"))) >= 0
    ]
    avg_volume = (
        sum(volumes[-20:]) / Decimal(len(volumes[-20:]))
        if volumes[-20:]
        else Decimal("0")
    )
    last_volume = volumes[-1] if volumes else Decimal("0")
    relative_volume = (
        last_volume / avg_volume
        if avg_volume > 0
        else Decimal("0")
    )
    return {
        "candles_count": len(candles),
        "ema9": str(ema(closes, 9)) if ema(closes, 9) is not None else None,
        "ema21": str(ema(closes, 21)) if ema(closes, 21) is not None else None,
        "rsi14": str(rsi(closes, 14)) if rsi(closes, 14) is not None else None,
        "atr14": str(atr(candles, 14)) if atr(candles, 14) is not None else None,
        "vwap": str(vwap(candles)) if vwap(candles) is not None else None,
        "relative_volume": str(relative_volume),
        "last_close": str(closes[-1]) if closes else None,
    }


def enrich_candidate(
    client: TInvestSandboxClient,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    uid = candidate["instrument_uid"]
    now = datetime.now(timezone.utc)

    order_book = client.get_order_book(uid, depth=10)
    trading_status = client.get_trading_status(uid)
    candles_5m = client.get_candles(
        instrument_uid=uid,
        from_time=now - timedelta(hours=6),
        to_time=now,
        interval="CANDLE_INTERVAL_5_MIN",
    )
    candles_15m = client.get_candles(
        instrument_uid=uid,
        from_time=now - timedelta(days=2),
        to_time=now,
        interval="CANDLE_INTERVAL_15_MIN",
    )

    bids = order_book.get("bids") or []
    asks = order_book.get("asks") or []
    best_bid = quote_to_decimal(bids[0].get("price")) if bids else Decimal("0")
    best_ask = quote_to_decimal(asks[0].get("price")) if asks else Decimal("0")
    spread_pct = None
    if best_bid > 0 and best_ask > 0:
        mid = (best_bid + best_ask) / Decimal("2")
        if mid > 0:
            spread_pct = (
                (best_ask - best_bid) / mid * Decimal("100")
            )

    instrument = candidate["instrument"]
    return {
        "scanner_version": "1",
        "scanner_role": "CANDIDATE_ONLY_NOT_A_TRADE_DECISION",
        "data_timestamp": now.isoformat(),
        "instrument_name": instrument.get("name"),
        "instrument_uid": uid,
        "ticker": candidate["ticker"],
        "class_code": candidate["class_code"],
        "instrument_type": candidate["instrument_type"],
        "lot": instrument.get("lot"),
        "min_price_increment": instrument.get("minPriceIncrement")
        or instrument.get("min_price_increment"),
        "currency": instrument.get("currency"),
        "real_exchange": instrument.get("realExchange")
        or instrument.get("real_exchange"),
        "last_price": str(candidate["last_price"]),
        "previous_close": str(candidate["close_price"]),
        "move_percent_from_close": str(candidate["move_percent"]),
        "last_price_time": candidate["last_price_time"].isoformat(),
        "bid": str(best_bid) if best_bid > 0 else None,
        "ask": str(best_ask) if best_ask > 0 else None,
        "spread_percent": str(spread_pct) if spread_pct is not None else None,
        "order_book_depth": order_book.get("depth"),
        "orderbook_ts": order_book.get("orderbookTs")
        or order_book.get("orderbook_ts"),
        "trading_status": trading_status,
        "technical_5m": _candle_summary(candles_5m),
        "technical_15m": _candle_summary(candles_15m),
        "portfolio": _compact_portfolio(client.get_portfolio()),
        "raw_order_book_top5": {
            "bids": bids[:5],
            "asks": asks[:5],
        },
    }
