"""Read-only historical validation for Capital Preservation Trend.

No broker orders are created. The script deliberately uses completed daily
candles and enters no earlier than the next session open to avoid same-bar
look-ahead. It compares a few economically motivated variants rather than
optimizing dozens of thresholds on one history sample.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from tradebot.tinvest import TInvestSandboxClient


START_CAPITAL = Decimal("1000000")
RISK_PER_TRADE = Decimal("0.0020")
MAX_POSITION_SHARE = Decimal("0.10")
ROUND_TRIP_COST = Decimal("0.0015")
MIN_DAILY_TURNOVER_RUB = Decimal("50000000")
MIN_ATR_PERCENT = Decimal("0.35")
MAX_ATR_PERCENT = Decimal("5.0")
MAX_EXTENSION_ATR = Decimal("1.50")
PULLBACK_TOUCH_ATR = Decimal("0.50")
PULLBACK_BREAK_ATR = Decimal("0.50")
BENCHMARK_QUERY = "EQMX_TQBR"

DEFAULT_UNIVERSE = (
    "SBER_TQBR",
    "GAZP_TQBR",
    "LKOH_TQBR",
    "NVTK_TQBR",
    "YDEX_TQBR",
    "OZON_TQBR",
    "PLZL_TQBR",
    "MGNT_TQBR",
    "LENT_TQBR",
    "X5_TQBR",
)


@dataclass(frozen=True)
class Variant:
    name: str
    relative_strength_margin: Decimal
    stop_atr: Decimal
    trail_atr: Decimal
    max_hold_sessions: int
    require_recovery_break: bool


VARIANTS = (
    Variant(
        name="MARKET_RS_PULLBACK",
        relative_strength_margin=Decimal("0.005"),
        stop_atr=Decimal("2.2"),
        trail_atr=Decimal("2.0"),
        max_hold_sessions=30,
        require_recovery_break=False,
    ),
    Variant(
        name="MARKET_RS_RECOVERY",
        relative_strength_margin=Decimal("0.005"),
        stop_atr=Decimal("2.2"),
        trail_atr=Decimal("2.0"),
        max_hold_sessions=30,
        require_recovery_break=True,
    ),
    Variant(
        name="MARKET_RS_RECOVERY_WIDE",
        relative_strength_margin=Decimal("0.005"),
        stop_atr=Decimal("2.5"),
        trail_atr=Decimal("2.5"),
        max_hold_sessions=40,
        require_recovery_break=True,
    ),
)


@dataclass(frozen=True)
class BreakoutVariant:
    name: str
    entry_lookback: int
    exit_lookback: int
    stop_atr: Decimal
    trail_atr: Decimal
    max_hold_sessions: int


BREAKOUT_VARIANTS = (
    BreakoutVariant(
        name="BREAKOUT_20_10",
        entry_lookback=20,
        exit_lookback=10,
        stop_atr=Decimal("2.0"),
        trail_atr=Decimal("2.5"),
        max_hold_sessions=60,
    ),
    BreakoutVariant(
        name="BREAKOUT_55_20",
        entry_lookback=55,
        exit_lookback=20,
        stop_atr=Decimal("2.0"),
        trail_atr=Decimal("3.0"),
        max_hold_sessions=90,
    ),
)


def qdec(value: Any) -> Decimal:
    if not isinstance(value, dict):
        return Decimal("0")
    return (
        Decimal(str(value.get("units", "0")))
        + Decimal(str(value.get("nano", 0))) / Decimal("1000000000")
    )


@dataclass(frozen=True)
class Bar:
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Trade:
    ticker: str
    entry_time: datetime
    exit_time: datetime
    entry: Decimal
    exit: Decimal
    initial_stop: Decimal
    gross_return: Decimal
    net_return: Decimal
    r_multiple: Decimal
    holding_sessions: int
    exit_reason: str


def as_bar(candle: dict[str, Any]) -> Bar | None:
    if not candle.get("isComplete", candle.get("is_complete", True)):
        return None
    raw_time = str(candle.get("time") or "")
    if not raw_time:
        return None
    try:
        ts = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    o, h, l, c = (
        qdec(candle.get("open")),
        qdec(candle.get("high")),
        qdec(candle.get("low")),
        qdec(candle.get("close")),
    )
    if min(o, h, l, c) <= 0:
        return None
    return Bar(
        time=ts.astimezone(timezone.utc),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal(str(candle.get("volume", "0"))),
    )


def ema_series(values: list[Decimal], period: int) -> list[Decimal | None]:
    out: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / Decimal(period)
    out[period - 1] = seed
    k = Decimal("2") / Decimal(period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (Decimal("1") - k)
        out[i] = prev
    return out


def atr_series(bars: list[Bar], period: int = 14) -> list[Decimal | None]:
    trs: list[Decimal] = []
    out: list[Decimal | None] = [None] * len(bars)
    for i, bar in enumerate(bars):
        previous_close = bars[i - 1].close if i > 0 else None
        tr = bar.high - bar.low
        if previous_close is not None:
            tr = max(
                tr,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        trs.append(tr)
        if i + 1 >= period:
            out[i] = sum(trs[i - period + 1 : i + 1]) / Decimal(period)
    return out


def rolling_average(values: list[Decimal], period: int) -> list[Decimal | None]:
    out: list[Decimal | None] = [None] * len(values)
    running = Decimal("0")
    for i, value in enumerate(values):
        running += value
        if i >= period:
            running -= values[i - period]
        if i + 1 >= period:
            out[i] = running / Decimal(period)
    return out


def fetch_daily_history(
    client: TInvestSandboxClient,
    instrument_uid: str,
    start: datetime,
    end: datetime,
) -> list[Bar]:
    candles = client.get_candles(
        instrument_uid=instrument_uid,
        from_time=start,
        to_time=end,
        interval="CANDLE_INTERVAL_DAY",
    )
    bars = [bar for candle in candles if (bar := as_bar(candle)) is not None]
    bars.sort(key=lambda x: x.time)
    dedup: dict[datetime, Bar] = {bar.time: bar for bar in bars}
    return list(sorted(dedup.values(), key=lambda x: x.time))


def indicator_pack(bars: list[Bar]) -> dict[str, list[Decimal | None]]:
    closes = [x.close for x in bars]
    volumes = [x.volume for x in bars]
    return {
        "ema20": ema_series(closes, 20),
        "ema50": ema_series(closes, 50),
        "atr14": atr_series(bars, 14),
        "avgvol20": rolling_average(volumes, 20),
    }


def benchmark_states(bars: list[Bar]) -> dict[date, dict[str, Decimal | bool]]:
    ind = indicator_pack(bars)
    states: dict[date, dict[str, Decimal | bool]] = {}
    for i, bar in enumerate(bars):
        if i < 60:
            continue
        e20 = ind["ema20"][i]
        e50 = ind["ema50"][i]
        e50_old = ind["ema50"][i - 10]
        if e20 is None or e50 is None or e50_old is None:
            continue
        ret20 = bar.close / bars[i - 20].close - Decimal("1")
        trend_up = (
            e20 > e50
            and e50 > e50_old
            and bar.close >= e20
        )
        states[bar.time.date()] = {
            "trend_up": trend_up,
            "return_20": ret20,
        }
    return states


def backtest_symbol(
    ticker: str,
    bars: list[Bar],
    benchmark: dict[date, dict[str, Decimal | bool]],
    variant: Variant,
) -> list[Trade]:
    if len(bars) < 90:
        return []

    ind = indicator_pack(bars)
    trades: list[Trade] = []
    i = 60
    while i < len(bars) - 1:
        e20 = ind["ema20"][i]
        e50 = ind["ema50"][i]
        e50_old = ind["ema50"][i - 10]
        atrv = ind["atr14"][i]
        avgv = ind["avgvol20"][i]
        if None in (e20, e50, e50_old, atrv, avgv):
            i += 1
            continue
        assert e20 is not None and e50 is not None and e50_old is not None
        assert atrv is not None and avgv is not None
        bar = bars[i]

        market = benchmark.get(bar.time.date())
        if not market or market.get("trend_up") is not True:
            i += 1
            continue
        benchmark_ret20 = market.get("return_20")
        if not isinstance(benchmark_ret20, Decimal):
            i += 1
            continue

        symbol_ret20 = bar.close / bars[i - 20].close - Decimal("1")
        required_relative = max(Decimal("0"), benchmark_ret20) + (
            variant.relative_strength_margin
        )

        atr_pct = atrv / bar.close * Decimal("100")
        turnover = avgv * bar.close
        recent_low5 = min(x.low for x in bars[max(0, i - 4) : i + 1])
        relative_volume = bar.volume / avgv if avgv > 0 else Decimal("0")

        valid = (
            e20 > e50
            and e50 > e50_old
            and bar.close >= e20
            and bar.close >= e50
            and symbol_ret20 >= required_relative
            and MIN_ATR_PERCENT <= atr_pct < MAX_ATR_PERCENT
            and turnover >= MIN_DAILY_TURNOVER_RUB
            and relative_volume >= Decimal("0.60")
            and bar.close <= e20 + MAX_EXTENSION_ATR * atrv
            and recent_low5 <= e20 + PULLBACK_TOUCH_ATR * atrv
            and recent_low5 >= e50 - PULLBACK_BREAK_ATR * atrv
        )
        if variant.require_recovery_break and i > 0:
            valid = valid and bar.close > bars[i - 1].high

        if not valid:
            i += 1
            continue

        entry_i = i + 1
        entry_bar = bars[entry_i]
        entry = entry_bar.open
        if entry <= 0:
            i += 1
            continue

        structure_stop = recent_low5 - Decimal("0.25") * atrv
        volatility_stop = entry - variant.stop_atr * atrv
        stop = max(structure_stop, volatility_stop)
        if stop <= 0 or stop >= entry:
            i += 1
            continue

        initial_risk = entry - stop
        trail = stop
        exit_price: Decimal | None = None
        exit_i: int | None = None
        exit_reason = ""

        max_exit_i = min(
            len(bars) - 1,
            entry_i + variant.max_hold_sessions,
        )
        j = entry_i
        while j <= max_exit_i:
            current = bars[j]
            if current.low <= trail:
                exit_price = trail
                exit_i = j
                exit_reason = "TRAIL_STOP" if trail > stop else "INITIAL_STOP"
                break

            current_e20 = ind["ema20"][j]
            current_e50 = ind["ema50"][j]
            current_atr = ind["atr14"][j]
            if current_e20 is not None and current_atr is not None:
                candidate_trail = current_e20 - variant.trail_atr * current_atr
                if candidate_trail > trail and candidate_trail < current.close:
                    trail = candidate_trail

            if (
                current_e50 is not None
                and current.close < current_e50
                and j + 1 < len(bars)
            ):
                exit_price = bars[j + 1].open
                exit_i = j + 1
                exit_reason = "TREND_BREAK"
                break

            if j == max_exit_i:
                exit_price = current.close
                exit_i = j
                exit_reason = "TIME_EXIT"
                break
            j += 1

        if exit_price is None or exit_i is None:
            i += 1
            continue

        gross = exit_price / entry - Decimal("1")
        net = gross - ROUND_TRIP_COST
        risk_pct = initial_risk / entry
        r_multiple = net / risk_pct if risk_pct > 0 else Decimal("0")
        trades.append(
            Trade(
                ticker=ticker,
                entry_time=entry_bar.time,
                exit_time=bars[exit_i].time,
                entry=entry,
                exit=exit_price,
                initial_stop=stop,
                gross_return=gross,
                net_return=net,
                r_multiple=r_multiple,
                holding_sessions=exit_i - entry_i + 1,
                exit_reason=exit_reason,
            )
        )
        i = max(exit_i + 1, i + 1)

    return trades


def backtest_breakout_symbol(
    ticker: str,
    bars: list[Bar],
    benchmark: dict[date, dict[str, Decimal | bool]],
    variant: BreakoutVariant,
) -> list[Trade]:
    if len(bars) < max(100, variant.entry_lookback + 30):
        return []

    ind = indicator_pack(bars)
    trades: list[Trade] = []
    i = max(60, variant.entry_lookback)
    while i < len(bars) - 1:
        e20 = ind["ema20"][i]
        e50 = ind["ema50"][i]
        e50_old = ind["ema50"][i - 10]
        atrv = ind["atr14"][i]
        avgv = ind["avgvol20"][i]
        if None in (e20, e50, e50_old, atrv, avgv):
            i += 1
            continue
        assert e20 is not None and e50 is not None and e50_old is not None
        assert atrv is not None and avgv is not None
        bar = bars[i]

        market = benchmark.get(bar.time.date())
        if not market or market.get("trend_up") is not True:
            i += 1
            continue
        benchmark_ret20 = market.get("return_20")
        if not isinstance(benchmark_ret20, Decimal):
            i += 1
            continue

        symbol_ret20 = bar.close / bars[i - 20].close - Decimal("1")
        atr_pct = atrv / bar.close * Decimal("100")
        turnover = avgv * bar.close
        relative_volume = bar.volume / avgv if avgv > 0 else Decimal("0")
        prior_high = max(
            x.high
            for x in bars[i - variant.entry_lookback : i]
        )

        valid = (
            e20 > e50
            and e50 > e50_old
            and bar.close > prior_high
            and symbol_ret20 > max(Decimal("0"), benchmark_ret20)
            and MIN_ATR_PERCENT <= atr_pct < MAX_ATR_PERCENT
            and turnover >= MIN_DAILY_TURNOVER_RUB
            and relative_volume >= Decimal("0.80")
        )
        if not valid:
            i += 1
            continue

        entry_i = i + 1
        entry_bar = bars[entry_i]
        entry = entry_bar.open
        if entry <= 0:
            i += 1
            continue

        stop = entry - variant.stop_atr * atrv
        if stop <= 0 or stop >= entry:
            i += 1
            continue

        initial_risk = entry - stop
        trail = stop
        highest_close = entry
        exit_price: Decimal | None = None
        exit_i: int | None = None
        exit_reason = ""

        max_exit_i = min(
            len(bars) - 1,
            entry_i + variant.max_hold_sessions,
        )
        j = entry_i
        while j <= max_exit_i:
            current = bars[j]
            if current.low <= trail:
                exit_price = trail
                exit_i = j
                exit_reason = "ATR_TRAIL"
                break

            highest_close = max(highest_close, current.close)
            current_atr = ind["atr14"][j]
            if current_atr is not None:
                candidate_trail = highest_close - variant.trail_atr * current_atr
                if candidate_trail > trail and candidate_trail < current.close:
                    trail = candidate_trail

            if j >= variant.exit_lookback:
                prior_exit_low = min(
                    x.low
                    for x in bars[j - variant.exit_lookback : j]
                )
                if current.close < prior_exit_low and j + 1 < len(bars):
                    exit_price = bars[j + 1].open
                    exit_i = j + 1
                    exit_reason = "DONCHIAN_EXIT"
                    break

            current_e50 = ind["ema50"][j]
            if (
                current_e50 is not None
                and current.close < current_e50
                and j + 1 < len(bars)
            ):
                exit_price = bars[j + 1].open
                exit_i = j + 1
                exit_reason = "TREND_BREAK"
                break

            if j == max_exit_i:
                exit_price = current.close
                exit_i = j
                exit_reason = "TIME_EXIT"
                break
            j += 1

        if exit_price is None or exit_i is None:
            i += 1
            continue

        gross = exit_price / entry - Decimal("1")
        net = gross - ROUND_TRIP_COST
        risk_pct = initial_risk / entry
        r_multiple = net / risk_pct if risk_pct > 0 else Decimal("0")
        trades.append(
            Trade(
                ticker=ticker,
                entry_time=entry_bar.time,
                exit_time=bars[exit_i].time,
                entry=entry,
                exit=exit_price,
                initial_stop=stop,
                gross_return=gross,
                net_return=net,
                r_multiple=r_multiple,
                holding_sessions=exit_i - entry_i + 1,
                exit_reason=exit_reason,
            )
        )
        i = max(exit_i + 1, i + 1)

    return trades


def portfolio_metrics(trades: list[Trade]) -> dict[str, Any]:
    capital = START_CAPITAL
    peak = capital
    max_drawdown = Decimal("0")
    wins = 0
    gross_profit = Decimal("0")
    gross_loss = Decimal("0")
    total_r = Decimal("0")
    capital_returns: list[Decimal] = []

    for trade in sorted(trades, key=lambda x: (x.exit_time, x.ticker)):
        stop_distance_pct = (trade.entry - trade.initial_stop) / trade.entry
        if stop_distance_pct <= 0:
            continue
        position_share = min(
            MAX_POSITION_SHARE,
            RISK_PER_TRADE / stop_distance_pct,
        )
        contribution = position_share * trade.net_return
        capital_returns.append(contribution)
        capital *= Decimal("1") + contribution
        peak = max(peak, capital)
        drawdown = (peak - capital) / peak if peak > 0 else Decimal("0")
        max_drawdown = max(max_drawdown, drawdown)
        total_r += trade.r_multiple
        if contribution > 0:
            wins += 1
            gross_profit += contribution
        elif contribution < 0:
            gross_loss += -contribution

    count = len(capital_returns)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    return {
        "start_capital_rub": str(START_CAPITAL),
        "end_capital_rub": str(capital.quantize(Decimal("0.01"))),
        "net_pnl_percent": str(
            ((capital / START_CAPITAL - Decimal("1")) * Decimal("100")).quantize(
                Decimal("0.001")
            )
        ),
        "max_drawdown_percent": str(
            (max_drawdown * Decimal("100")).quantize(Decimal("0.001"))
        ),
        "trades": count,
        "win_rate_percent": str(
            (
                Decimal(wins) / Decimal(count) * Decimal("100")
                if count
                else Decimal("0")
            ).quantize(Decimal("0.01"))
        ),
        "profit_factor": (
            str(profit_factor.quantize(Decimal("0.001")))
            if profit_factor is not None
            else None
        ),
        "average_r": str(
            (
                total_r / Decimal(len(trades))
                if trades
                else Decimal("0")
            ).quantize(Decimal("0.001"))
        ),
    }


def split_metrics(trades: list[Trade], cutoff: datetime) -> dict[str, Any]:
    return {
        "earlier_sample": portfolio_metrics(
            [x for x in trades if x.entry_time < cutoff]
        ),
        "recent_holdout": portfolio_metrics(
            [x for x in trades if x.entry_time >= cutoff]
        ),
    }


def backtest_weekly_rotation(
    client: TInvestSandboxClient,
    universe: tuple[str, ...],
    benchmark_bars: list[Bar],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Weekly top-3 relative-strength rotation with a 30% exposure ceiling."""
    one_way_cost = ROUND_TRIP_COST / Decimal("2")
    per_position_weight = Decimal("0.10")
    top_n = 3
    momentum_lookback = 60
    rebalance_every = 5

    all_bars: dict[str, list[Bar]] = {}
    all_ind: dict[str, dict[str, list[Decimal | None]]] = {}
    date_maps: dict[str, dict[date, int]] = {}

    for query in universe:
        try:
            instrument = client.find_instrument(query)
            uid = str(
                instrument.get("uid")
                or instrument.get("instrumentUid")
                or ""
            )
            ticker = str(instrument.get("ticker") or query).upper()
            bars = fetch_daily_history(client, uid, start, end)
            if len(bars) < momentum_lookback + 20:
                continue
            all_bars[ticker] = bars
            all_ind[ticker] = indicator_pack(bars)
            date_maps[ticker] = {
                bar.time.date(): i for i, bar in enumerate(bars)
            }
        except Exception:
            continue

    benchmark_ind = indicator_pack(benchmark_bars)
    benchmark_idx = {
        bar.time.date(): i for i, bar in enumerate(benchmark_bars)
    }

    capital = START_CAPITAL
    peak = capital
    max_drawdown = Decimal("0")
    weights: dict[str, Decimal] = {}
    equity_curve: list[tuple[datetime, Decimal]] = []
    turnover_cost_total = Decimal("0")
    rebalances = 0
    position_days = 0

    for bi in range(61, len(benchmark_bars)):
        day_bar = benchmark_bars[bi]
        d = day_bar.time.date()

        # Apply close-to-close P&L from positions selected at the prior close.
        if bi > 0 and weights:
            daily_portfolio_return = Decimal("0")
            for ticker, weight in list(weights.items()):
                idx = date_maps.get(ticker, {}).get(d)
                if idx is None or idx <= 0:
                    continue
                bars = all_bars[ticker]
                previous = bars[idx - 1].close
                current = bars[idx].close
                if previous > 0:
                    daily_portfolio_return += weight * (
                        current / previous - Decimal("1")
                    )
                    position_days += 1
            capital *= Decimal("1") + daily_portfolio_return

        peak = max(peak, capital)
        drawdown = (peak - capital) / peak if peak > 0 else Decimal("0")
        max_drawdown = max(max_drawdown, drawdown)
        equity_curve.append((day_bar.time, capital))

        if bi % rebalance_every != 0:
            continue

        b20 = benchmark_ind["ema20"][bi]
        b50 = benchmark_ind["ema50"][bi]
        b50old = benchmark_ind["ema50"][bi - 10]
        market_on = (
            b20 is not None
            and b50 is not None
            and b50old is not None
            and b20 > b50
            and b50 > b50old
            and day_bar.close >= b20
        )

        selected: list[str] = []
        if market_on:
            ranked: list[tuple[Decimal, str]] = []
            for ticker, bars in all_bars.items():
                idx = date_maps[ticker].get(d)
                if idx is None or idx < momentum_lookback:
                    continue
                ind = all_ind[ticker]
                e20 = ind["ema20"][idx]
                e50 = ind["ema50"][idx]
                e50old = ind["ema50"][idx - 10]
                avgv = ind["avgvol20"][idx]
                if None in (e20, e50, e50old, avgv):
                    continue
                assert e20 is not None and e50 is not None
                assert e50old is not None and avgv is not None
                bar = bars[idx]
                momentum = (
                    bar.close / bars[idx - momentum_lookback].close
                    - Decimal("1")
                )
                turnover = avgv * bar.close
                if (
                    e20 > e50
                    and e50 > e50old
                    and bar.close >= e20
                    and momentum > 0
                    and turnover >= MIN_DAILY_TURNOVER_RUB
                ):
                    ranked.append((momentum, ticker))
            ranked.sort(reverse=True)
            selected = [ticker for _, ticker in ranked[:top_n]]

        new_weights = {
            ticker: per_position_weight
            for ticker in selected
        }
        all_names = set(weights) | set(new_weights)
        traded_weight = sum(
            abs(new_weights.get(t, Decimal("0")) - weights.get(t, Decimal("0")))
            for t in all_names
        )
        if traded_weight > 0:
            cost = traded_weight * one_way_cost
            turnover_cost_total += cost
            capital *= Decimal("1") - cost
        weights = new_weights
        rebalances += 1

    peak = max(peak, capital)
    max_drawdown = max(
        max_drawdown,
        (peak - capital) / peak if peak > 0 else Decimal("0"),
    )

    cutoff = start + (end - start) * 0.70
    holdout_points = [
        (ts, eq) for ts, eq in equity_curve if ts >= cutoff
    ]
    if holdout_points:
        holdout_start = holdout_points[0][1]
        holdout_end = holdout_points[-1][1]
        holdout_peak = holdout_start
        holdout_dd = Decimal("0")
        for _, eq in holdout_points:
            holdout_peak = max(holdout_peak, eq)
            if holdout_peak > 0:
                holdout_dd = max(
                    holdout_dd,
                    (holdout_peak - eq) / holdout_peak,
                )
        holdout_return = (
            holdout_end / holdout_start - Decimal("1")
            if holdout_start > 0
            else Decimal("0")
        )
    else:
        holdout_return = Decimal("0")
        holdout_dd = Decimal("0")

    return {
        "name": "WEEKLY_TOP3_RELATIVE_STRENGTH",
        "parameters": {
            "momentum_lookback_sessions": momentum_lookback,
            "rebalance_every_sessions": rebalance_every,
            "top_n": top_n,
            "weight_per_position_percent": "10",
            "max_equity_exposure_percent": "30",
            "market_filter": "EQMX EMA20>EMA50, EMA50 rising, close>=EMA20",
            "cash_when_market_filter_off": True,
        },
        "full_period": {
            "start_capital_rub": str(START_CAPITAL),
            "end_capital_rub": str(capital.quantize(Decimal("0.01"))),
            "net_pnl_percent": str(
                (
                    (capital / START_CAPITAL - Decimal("1"))
                    * Decimal("100")
                ).quantize(Decimal("0.001"))
            ),
            "max_drawdown_percent": str(
                (max_drawdown * Decimal("100")).quantize(Decimal("0.001"))
            ),
            "estimated_turnover_cost_percent_of_initial": str(
                (turnover_cost_total * Decimal("100")).quantize(
                    Decimal("0.001")
                )
            ),
            "rebalances": rebalances,
            "position_days": position_days,
        },
        "recent_holdout": {
            "net_pnl_percent": str(
                (holdout_return * Decimal("100")).quantize(Decimal("0.001"))
            ),
            "max_drawdown_percent": str(
                (holdout_dd * Decimal("100")).quantize(Decimal("0.001"))
            ),
        },
        "final_holdings": weights,
    }


def main() -> None:
    token = os.environ["TINVEST_TOKEN"]
    account_name = os.getenv(
        "TINVEST_SANDBOX_ACCOUNT_NAME",
        "github-moex-trade-bot",
    )
    client = TInvestSandboxClient(token=token, account_name=account_name)

    end = datetime.now(timezone.utc)
    days = int(os.getenv("BACKTEST_DAYS", "400"))
    start = end - timedelta(days=days)
    cutoff = start + timedelta(days=int(days * 0.70))
    universe = tuple(
        x.strip().upper()
        for x in os.getenv(
            "BACKTEST_UNIVERSE",
            ",".join(DEFAULT_UNIVERSE),
        ).split(",")
        if x.strip()
    )

    benchmark_instrument = client.find_instrument(BENCHMARK_QUERY)
    benchmark_uid = str(
        benchmark_instrument.get("uid")
        or benchmark_instrument.get("instrumentUid")
        or ""
    )
    benchmark_bars = fetch_daily_history(client, benchmark_uid, start, end)
    benchmark = benchmark_states(benchmark_bars)

    variant_reports: dict[str, Any] = {}
    for variant in VARIANTS:
        all_trades: list[Trade] = []
        symbols: dict[str, Any] = {}
        for query in universe:
            try:
                instrument = client.find_instrument(query)
                uid = str(
                    instrument.get("uid")
                    or instrument.get("instrumentUid")
                    or ""
                )
                ticker = str(instrument.get("ticker") or query).upper()
                bars = fetch_daily_history(client, uid, start, end)
                trades = backtest_symbol(ticker, bars, benchmark, variant)
                all_trades.extend(trades)
                symbols[ticker] = {
                    "bars": len(bars),
                    "trades": len(trades),
                    "average_r": (
                        str(
                            (
                                sum(
                                    (t.r_multiple for t in trades),
                                    Decimal("0"),
                                )
                                / Decimal(len(trades))
                            ).quantize(Decimal("0.001"))
                        )
                        if trades
                        else None
                    ),
                    "wins": sum(1 for t in trades if t.net_return > 0),
                }
            except Exception as exc:
                symbols[query] = {
                    "error": f"{type(exc).__name__}: {exc}",
                }

        variant_reports[variant.name] = {
            "parameters": {
                "relative_strength_margin": str(
                    variant.relative_strength_margin
                ),
                "stop_atr": str(variant.stop_atr),
                "trail_atr": str(variant.trail_atr),
                "max_hold_sessions": variant.max_hold_sessions,
                "require_recovery_break": variant.require_recovery_break,
            },
            "symbols": symbols,
            "full_period": portfolio_metrics(all_trades),
            "split": split_metrics(all_trades, cutoff),
        }

    breakout_reports: dict[str, Any] = {}
    for variant in BREAKOUT_VARIANTS:
        all_trades: list[Trade] = []
        symbols: dict[str, Any] = {}
        for query in universe:
            try:
                instrument = client.find_instrument(query)
                uid = str(
                    instrument.get("uid")
                    or instrument.get("instrumentUid")
                    or ""
                )
                ticker = str(instrument.get("ticker") or query).upper()
                bars = fetch_daily_history(client, uid, start, end)
                trades = backtest_breakout_symbol(
                    ticker,
                    bars,
                    benchmark,
                    variant,
                )
                all_trades.extend(trades)
                symbols[ticker] = {
                    "bars": len(bars),
                    "trades": len(trades),
                    "average_r": (
                        str(
                            (
                                sum(
                                    (t.r_multiple for t in trades),
                                    Decimal("0"),
                                )
                                / Decimal(len(trades))
                            ).quantize(Decimal("0.001"))
                        )
                        if trades
                        else None
                    ),
                    "wins": sum(1 for t in trades if t.net_return > 0),
                }
            except Exception as exc:
                symbols[query] = {
                    "error": f"{type(exc).__name__}: {exc}",
                }

        breakout_reports[variant.name] = {
            "parameters": {
                "entry_lookback": variant.entry_lookback,
                "exit_lookback": variant.exit_lookback,
                "stop_atr": str(variant.stop_atr),
                "trail_atr": str(variant.trail_atr),
                "max_hold_sessions": variant.max_hold_sessions,
            },
            "symbols": symbols,
            "full_period": portfolio_metrics(all_trades),
            "split": split_metrics(all_trades, cutoff),
        }

    rotation_report = backtest_weekly_rotation(
        client,
        universe,
        benchmark_bars,
        start,
        end,
    )

    report = {
        "strategy_family": "CAPITAL_PRESERVATION_TREND",
        "model": "DAILY_CORE_PROXY_NO_LOOKAHEAD",
        "benchmark": BENCHMARK_QUERY,
        "period_start_utc": start.isoformat(),
        "period_end_utc": end.isoformat(),
        "holdout_cutoff_utc": cutoff.isoformat(),
        "risk_assumptions": {
            "round_trip_cost_percent": str(
                (ROUND_TRIP_COST * Decimal("100")).quantize(Decimal("0.001"))
            ),
            "risk_per_trade_percent": str(
                (RISK_PER_TRADE * Decimal("100")).quantize(Decimal("0.001"))
            ),
            "position_cap_percent": str(
                (MAX_POSITION_SHARE * Decimal("100")).quantize(Decimal("0.01"))
            ),
        },
        "variants": variant_reports,
        "breakout_variants": breakout_reports,
        "rotation_strategy": rotation_report,
        "notes": [
            "Read-only T-Invest market-data validation; no orders are placed.",
            "Market regime uses EQMX trend; entries require positive relative strength.",
            "Signal uses completed daily bars and next-session entry.",
            "The last 30% of calendar history is reported separately as a holdout.",
            "Live 1h/15m/5m timing is not simulated in this daily core proxy.",
            "Historical results do not guarantee future returns.",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
