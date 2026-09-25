"""Deterministic pre-review strategy for Sandbox share/ETF candidates.

This module does not execute trades and does not estimate a probability of profit.
It only classifies market regime/setup and decides whether a candidate is worth
sending to the independent reviewer.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


MAX_REVIEW_SPREAD_PERCENT = Decimal("0.20")
BREAKOUT_RELATIVE_VOLUME = Decimal("1.20")
BREAKOUT_NEAR_HIGH_FRACTION = Decimal("0.997")


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _frame(context: dict[str, Any], name: str) -> dict[str, Any]:
    value = context.get(name)
    return value if isinstance(value, dict) else {}


def _required_frame_complete(frame: dict[str, Any]) -> bool:
    required = (
        "ema9",
        "ema21",
        "rsi14",
        "atr14",
        "vwap",
        "relative_volume",
        "last_close",
        "recent_high",
        "recent_low",
    )
    if int(frame.get("candles_count") or 0) < 22:
        return False
    return all(_decimal(frame.get(key)) is not None for key in required)


def _ema_direction(frame: dict[str, Any]) -> int:
    fast = _decimal(frame.get("ema9"))
    slow = _decimal(frame.get("ema21"))
    if fast is None or slow is None:
        return 0
    if fast > slow:
        return 1
    if fast < slow:
        return -1
    return 0


def _above_vwap(frame: dict[str, Any]) -> bool:
    close = _decimal(frame.get("last_close"))
    vwap = _decimal(frame.get("vwap"))
    return close is not None and vwap is not None and close >= vwap


def classify_market_regime(context: dict[str, Any]) -> str:
    """Classify technical regime only; EVENT_RISK is assigned by news review."""
    daily = _frame(context, "technical_1d")
    hourly = _frame(context, "technical_1h")
    if not (_required_frame_complete(daily) and _required_frame_complete(hourly)):
        return "UNKNOWN"

    daily_dir = _ema_direction(daily)
    hourly_dir = _ema_direction(hourly)

    daily_close = _decimal(daily.get("last_close"))
    daily_atr = _decimal(daily.get("atr14"))
    hourly_close = _decimal(hourly.get("last_close"))
    hourly_atr = _decimal(hourly.get("atr14"))
    if all(v is not None and v > 0 for v in (daily_close, daily_atr, hourly_close, hourly_atr)):
        daily_atr_pct = daily_atr / daily_close * Decimal("100")
        hourly_atr_pct = hourly_atr / hourly_close * Decimal("100")
        # Deliberately conservative anomaly gate, not an optimized trading threshold.
        if daily_atr_pct >= Decimal("4.0") or hourly_atr_pct >= Decimal("2.0"):
            return "HIGH_VOLATILITY"

    if daily_dir > 0 and hourly_dir > 0:
        return "TREND_UP"
    if daily_dir < 0 and hourly_dir < 0:
        return "TREND_DOWN"
    return "RANGE"


def assess_long_setup(context: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic triage metadata for an independent LONG reviewer.

    quality_score is a ranking score, not a probability or expected return.
    """
    frames = {
        name: _frame(context, name)
        for name in (
            "technical_1m",
            "technical_5m",
            "technical_15m",
            "technical_1h",
            "technical_1d",
        )
    }
    missing = [name for name, frame in frames.items() if not _required_frame_complete(frame)]
    spread = _decimal(context.get("spread_percent"))
    regime = classify_market_regime(context)

    result: dict[str, Any] = {
        "strategy_version": "1",
        "market_regime": regime,
        "setup_type": "NONE",
        "review_candidate": False,
        "quality_score": 0,
        "score_is_probability": False,
        "reasons": [],
        "warnings": [],
    }

    if missing:
        result["reasons"].append("INCOMPLETE_TECHNICAL_CONTEXT:" + ",".join(missing))
        return result
    if spread is None or spread < 0:
        result["reasons"].append("SPREAD_UNAVAILABLE")
        return result
    if spread > MAX_REVIEW_SPREAD_PERCENT:
        result["reasons"].append(
            f"SPREAD_TOO_WIDE:{spread}>{MAX_REVIEW_SPREAD_PERCENT}"
        )
        return result
    if regime in {"TREND_DOWN", "HIGH_VOLATILITY", "UNKNOWN"}:
        result["reasons"].append(f"REGIME_NOT_LONG_FRIENDLY:{regime}")
        return result

    one = frames["technical_1m"]
    five = frames["technical_5m"]
    fifteen = frames["technical_15m"]
    hour = frames["technical_1h"]
    day = frames["technical_1d"]

    score = 0
    if regime == "TREND_UP":
        score += 30
    elif regime == "RANGE":
        score += 10

    if _ema_direction(fifteen) > 0:
        score += 15
    if _ema_direction(five) > 0:
        score += 15
    if _above_vwap(fifteen):
        score += 10
    if _above_vwap(five):
        score += 10

    rsi15 = _decimal(fifteen.get("rsi14"))
    rsi5 = _decimal(five.get("rsi14"))
    rv5 = _decimal(five.get("relative_volume"))
    close15 = _decimal(fifteen.get("last_close"))
    high15 = _decimal(fifteen.get("recent_high"))

    trend_continuation = (
        regime == "TREND_UP"
        and _ema_direction(day) > 0
        and _ema_direction(hour) > 0
        and _ema_direction(fifteen) > 0
        and _ema_direction(five) >= 0
        and _above_vwap(fifteen)
        and _above_vwap(five)
        and rsi15 is not None
        and Decimal("45") <= rsi15 <= Decimal("75")
        and rsi5 is not None
        and Decimal("40") <= rsi5 <= Decimal("78")
        and rv5 is not None
        and rv5 >= Decimal("0.70")
    )

    breakout = (
        regime in {"TREND_UP", "RANGE"}
        and _ema_direction(fifteen) >= 0
        and _ema_direction(five) > 0
        and _above_vwap(fifteen)
        and _above_vwap(five)
        and rv5 is not None
        and rv5 >= BREAKOUT_RELATIVE_VOLUME
        and close15 is not None
        and high15 is not None
        and high15 > 0
        and close15 >= high15 * BREAKOUT_NEAR_HIGH_FRACTION
    )

    mean_reversion = (
        regime in {"TREND_UP", "RANGE"}
        and rsi15 is not None
        and rsi15 <= Decimal("42")
        and rsi5 is not None
        and Decimal("35") <= rsi5 <= Decimal("60")
        and _ema_direction(five) > 0
        and _above_vwap(five)
        and _ema_direction(hour) >= 0
    )

    if breakout:
        result["setup_type"] = "BREAKOUT"
        score += 20
    elif trend_continuation:
        result["setup_type"] = "TREND_CONTINUATION"
        score += 20
    elif mean_reversion:
        result["setup_type"] = "MEAN_REVERSION"
        score += 15
    else:
        result["reasons"].append("NO_CONFIRMED_15M_5M_LONG_SETUP")
        # 1m is deliberately not a hard veto; it only informs timing.
        if _ema_direction(one) < 0 or not _above_vwap(one):
            result["warnings"].append("1M_TIMING_WEAK")
        result["quality_score"] = min(score, 100)
        return result

    if rv5 is not None and rv5 >= Decimal("1.0"):
        score += 5
    if _ema_direction(one) > 0 and _above_vwap(one):
        score += 5
    else:
        result["warnings"].append("1M_TIMING_NOT_CONFIRMED")

    result["quality_score"] = min(score, 100)
    result["review_candidate"] = True
    result["reasons"].append("QUALIFIED_FOR_INDEPENDENT_REVIEW")
    return result
