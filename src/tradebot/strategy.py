"""Capital Preservation Trend v2.

Deterministic pre-review strategy for T-Invest Sandbox share/ETF candidates.

Design goals:
- capital preservation first;
- one coherent edge: long trend + controlled pullback + recovery;
- avoid chasing large moves;
- use 1D/1h for thesis, 15m for confirmation and 5m for execution quality;
- 1m is intentionally not part of the decision;
- quality_score is ranking metadata, never a probability of profit.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


STRATEGY_VERSION = "2.1"
MAX_REVIEW_SPREAD_PERCENT = Decimal("0.08")
MIN_DAILY_TURNOVER_RUB = Decimal("50000000")
MIN_DAILY_ATR_PERCENT = Decimal("0.35")
MAX_DAILY_ATR_PERCENT = Decimal("5.0")
MAX_EXTENSION_ATR = Decimal("1.50")
PULLBACK_TOUCH_ATR = Decimal("0.50")
PULLBACK_BREAK_ATR = Decimal("0.50")
MIN_5M_RELATIVE_VOLUME = Decimal("0.60")\nMIN_RELATIVE_STRENGTH_MARGIN = Decimal("0.005")


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
        "ema20",
        "ema50",
        "rsi14",
        "atr14",
        "vwap",
        "relative_volume",
        "average_volume_20",
        "last_close",
        "recent_high_5",
        "recent_low_5",
    )
    if int(frame.get("candles_count") or 0) < 50:
        return False
    return all(_decimal(frame.get(key)) is not None for key in required)


def _ema20_above_ema50(frame: dict[str, Any]) -> bool:
    fast = _decimal(frame.get("ema20"))
    slow = _decimal(frame.get("ema50"))
    return fast is not None and slow is not None and fast > slow


def _close_above(frame: dict[str, Any], field: str) -> bool:
    close = _decimal(frame.get("last_close"))
    level = _decimal(frame.get(field))
    return close is not None and level is not None and close >= level


def _atr_percent(frame: dict[str, Any]) -> Decimal | None:
    close = _decimal(frame.get("last_close"))
    atr14 = _decimal(frame.get("atr14"))
    if close is None or atr14 is None or close <= 0 or atr14 <= 0:
        return None
    return atr14 / close * Decimal("100")


def _average_turnover(frame: dict[str, Any]) -> Decimal | None:
    close = _decimal(frame.get("last_close"))
    average_volume = _decimal(frame.get("average_volume_20"))
    if close is None or average_volume is None or close <= 0 or average_volume < 0:
        return None
    return close * average_volume


def classify_market_regime(context: dict[str, Any]) -> str:
    """Classify the instrument trend regime using 1D and 1h only."""
    daily = _frame(context, "technical_1d")
    hourly = _frame(context, "technical_1h")
    if not (_required_frame_complete(daily) and _required_frame_complete(hourly)):
        return "UNKNOWN"

    daily_atr_pct = _atr_percent(daily)
    hourly_atr_pct = _atr_percent(hourly)
    if daily_atr_pct is None or hourly_atr_pct is None:
        return "UNKNOWN"
    if daily_atr_pct >= MAX_DAILY_ATR_PERCENT or hourly_atr_pct >= Decimal("3.0"):
        return "HIGH_VOLATILITY"

    daily_up = (
        _ema20_above_ema50(daily)
        and _close_above(daily, "ema20")
        and _close_above(daily, "ema50")
    )
    hourly_up = (
        _ema20_above_ema50(hourly)
        and _close_above(hourly, "ema50")
    )
    if daily_up and hourly_up:
        return "TREND_UP"

    daily_fast = _decimal(daily.get("ema20"))
    daily_slow = _decimal(daily.get("ema50"))
    hourly_fast = _decimal(hourly.get("ema20"))
    hourly_slow = _decimal(hourly.get("ema50"))
    daily_close = _decimal(daily.get("last_close"))
    hourly_close = _decimal(hourly.get("last_close"))
    if all(
        x is not None
        for x in (
            daily_fast,
            daily_slow,
            hourly_fast,
            hourly_slow,
            daily_close,
            hourly_close,
        )
    ):
        if (
            daily_fast < daily_slow
            and daily_close < daily_fast
            and hourly_fast < hourly_slow
            and hourly_close < hourly_fast
        ):
            return "TREND_DOWN"

    return "RANGE"


def assess_long_setup(context: dict[str, Any]) -> dict[str, Any]:
    """Assess a single long setup: established trend -> pullback -> recovery."""
    frames = {
        name: _frame(context, name)
        for name in (
            "technical_5m",
            "technical_15m",
            "technical_1h",
            "technical_1d",
        )
    }
    missing = [
        name
        for name, frame in frames.items()
        if not _required_frame_complete(frame)
    ]
    spread = _decimal(context.get("spread_percent"))
    regime = classify_market_regime(context)

    result: dict[str, Any] = {
        "strategy_version": STRATEGY_VERSION,
        "strategy_name": "CAPITAL_PRESERVATION_TREND_MARKET_RS",
        "market_regime": regime,
        "setup_type": "NONE",
        "review_candidate": False,
        "quality_score": 0,
        "score_is_probability": False,
        "entry_model": "TREND_PULLBACK_RECOVERY",
        "stop_model": "ATR_PLUS_STRUCTURE",
        "profit_model": "LET_WINNERS_RUN_TRAILING_EXIT",
        "reasons": [],
        "warnings": [],
    }

    if missing:
        result["reasons"].append(
            "INCOMPLETE_TECHNICAL_CONTEXT:" + ",".join(missing)
        )
        return result

    if spread is None or spread < 0:
        result["reasons"].append("SPREAD_UNAVAILABLE")
        return result
    if spread > MAX_REVIEW_SPREAD_PERCENT:
        result["reasons"].append(
            f"SPREAD_TOO_WIDE:{spread}>{MAX_REVIEW_SPREAD_PERCENT}"
        )
        return result
    if regime != "TREND_UP":
        result["reasons"].append(f"REGIME_NOT_TREND_UP:{regime}")
        return result

    benchmark = context.get("market_benchmark")
    if not isinstance(benchmark, dict) or benchmark.get("error"):
        result["reasons"].append("MARKET_BENCHMARK_UNAVAILABLE")
        return result
    benchmark_day = benchmark.get("technical_1d")
    if not isinstance(benchmark_day, dict):
        result["reasons"].append("MARKET_BENCHMARK_UNAVAILABLE")
        return result

    benchmark_ema20 = _decimal(benchmark_day.get("ema20"))
    benchmark_ema50 = _decimal(benchmark_day.get("ema50"))
    benchmark_ema50_old = _decimal(benchmark_day.get("ema50_10_ago"))
    benchmark_close = _decimal(benchmark_day.get("last_close"))
    benchmark_return20 = _decimal(benchmark_day.get("return_20"))
    if any(
        x is None
        for x in (
            benchmark_ema20,
            benchmark_ema50,
            benchmark_ema50_old,
            benchmark_close,
            benchmark_return20,
        )
    ):
        result["reasons"].append("MARKET_BENCHMARK_INCOMPLETE")
        return result
    if not (
        benchmark_ema20 > benchmark_ema50
        and benchmark_ema50 > benchmark_ema50_old
        and benchmark_close >= benchmark_ema20
    ):
        result["reasons"].append("MARKET_RISK_OFF")
        return result

    five = frames["technical_5m"]
    fifteen = frames["technical_15m"]
    hour = frames["technical_1h"]
    day = frames["technical_1d"]

    day_ema50 = _decimal(day.get("ema50"))
    day_ema50_old = _decimal(day.get("ema50_10_ago"))
    day_return20 = _decimal(day.get("return_20"))
    if day_ema50 is None or day_ema50_old is None or day_return20 is None:
        result["reasons"].append("DAILY_STRENGTH_CONTEXT_INCOMPLETE")
        return result
    if day_ema50 <= day_ema50_old:
        result["reasons"].append("DAILY_EMA50_NOT_RISING")
        return result
    required_relative_return = max(
        Decimal("0"),
        benchmark_return20,
    ) + MIN_RELATIVE_STRENGTH_MARGIN
    if day_return20 < required_relative_return:
        result["reasons"].append(
            "RELATIVE_STRENGTH_TOO_LOW:"
            f"{day_return20}<{required_relative_return}"
        )
        return result

    turnover = _average_turnover(day)
    if turnover is None:
        result["reasons"].append("DAILY_TURNOVER_UNAVAILABLE")
        return result
    if turnover < MIN_DAILY_TURNOVER_RUB:
        result["reasons"].append(
            f"DAILY_TURNOVER_TOO_LOW:{turnover}<{MIN_DAILY_TURNOVER_RUB}"
        )
        return result

    daily_atr_pct = _atr_percent(day)
    if daily_atr_pct is None:
        result["reasons"].append("DAILY_ATR_UNAVAILABLE")
        return result
    if daily_atr_pct < MIN_DAILY_ATR_PERCENT:
        result["reasons"].append(
            f"DAILY_VOLATILITY_TOO_LOW:{daily_atr_pct}"
        )
        return result
    if daily_atr_pct >= MAX_DAILY_ATR_PERCENT:
        result["reasons"].append(
            f"DAILY_VOLATILITY_TOO_HIGH:{daily_atr_pct}"
        )
        return result

    day_close = _decimal(day.get("last_close"))
    day_ema20 = _decimal(day.get("ema20"))
    day_atr = _decimal(day.get("atr14"))
    assert day_close is not None and day_ema20 is not None and day_atr is not None
    if day_close > day_ema20 + MAX_EXTENSION_ATR * day_atr:
        result["reasons"].append("DAILY_PRICE_OVEREXTENDED")
        return result

    hour_close = _decimal(hour.get("last_close"))
    hour_ema20 = _decimal(hour.get("ema20"))
    hour_ema50 = _decimal(hour.get("ema50"))
    hour_atr = _decimal(hour.get("atr14"))
    hour_low5 = _decimal(hour.get("recent_low_5"))
    assert all(
        x is not None
        for x in (hour_close, hour_ema20, hour_ema50, hour_atr, hour_low5)
    )

    touched_pullback = (
        hour_low5 <= hour_ema20 + PULLBACK_TOUCH_ATR * hour_atr
    )
    held_trend_structure = (
        hour_low5 >= hour_ema50 - PULLBACK_BREAK_ATR * hour_atr
    )
    recovered_hourly = hour_close >= hour_ema20

    if not touched_pullback:
        result["reasons"].append("NO_RECENT_PULLBACK_TO_1H_EMA20")
        return result
    if not held_trend_structure:
        result["reasons"].append("PULLBACK_BROKE_1H_TREND_STRUCTURE")
        return result
    if not recovered_hourly:
        result["reasons"].append("1H_PULLBACK_NOT_RECOVERED")
        return result

    fifteen_close = _decimal(fifteen.get("last_close"))
    fifteen_ema20 = _decimal(fifteen.get("ema20"))
    fifteen_ema50 = _decimal(fifteen.get("ema50"))
    fifteen_vwap = _decimal(fifteen.get("vwap"))
    assert all(
        x is not None
        for x in (
            fifteen_close,
            fifteen_ema20,
            fifteen_ema50,
            fifteen_vwap,
        )
    )
    recovered_15m = (
        fifteen_ema20 >= fifteen_ema50
        and fifteen_close >= fifteen_ema20
        and fifteen_close >= fifteen_vwap
    )
    if not recovered_15m:
        result["reasons"].append("NO_15M_RECOVERY_CONFIRMATION")
        return result

    score = 55

    if spread <= Decimal("0.04"):
        score += 10

    rsi15 = _decimal(fifteen.get("rsi14"))
    if rsi15 is not None and Decimal("45") <= rsi15 <= Decimal("70"):
        score += 10
    elif rsi15 is not None and rsi15 > Decimal("78"):
        result["warnings"].append("15M_RSI_OVERHEATED")

    rv15 = _decimal(fifteen.get("relative_volume"))
    if rv15 is not None and rv15 >= Decimal("0.80"):
        score += 10
    elif rv15 is not None and rv15 < Decimal("0.50"):
        result["warnings"].append("15M_VOLUME_WEAK")

    five_close = _decimal(five.get("last_close"))
    five_vwap = _decimal(five.get("vwap"))
    rv5 = _decimal(five.get("relative_volume"))
    if (
        five_close is not None
        and five_vwap is not None
        and five_close >= five_vwap
    ):
        score += 10
    else:
        result["warnings"].append("5M_TIMING_BELOW_VWAP")

    if rv5 is not None and rv5 >= MIN_5M_RELATIVE_VOLUME:
        score += 5
    else:
        result["warnings"].append("5M_RELATIVE_VOLUME_WEAK")

    result["setup_type"] = "TREND_PULLBACK"
    result["quality_score"] = min(score, 100)
    result["review_candidate"] = True
    result["reasons"].append("QUALIFIED_CAPITAL_PRESERVATION_TREND_V2_1")
    return result
