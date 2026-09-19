from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .protocol import TradeCommand, parse_iso_utc


RISK_PER_TRADE = Decimal("0.0025")
DAILY_STOP = Decimal("0.0075")
WEEKLY_STOP = Decimal("0.02")
MONTHLY_STOP = Decimal("0.04")
DRAWDOWN_RISK_REDUCTION_TRIGGER = Decimal("0.03")
DRAWDOWN_RISK_MULTIPLIER = Decimal("0.5")
MAX_POSITION_SHARE = Decimal("0.10")
MAX_OPEN_POSITIONS = 2
MIN_NET_RISK_REWARD = Decimal("2.0")


def _decimal_parts(value: Any) -> Decimal:
    if not isinstance(value, dict):
        raise RuntimeError("Required money/quotation value is unavailable")
    units = Decimal(str(value.get("units", "0")))
    nano = Decimal(str(value.get("nano", 0))) / Decimal("1000000000")
    return units + nano


def _quotation_or_zero(value: Any) -> Decimal:
    if not isinstance(value, dict):
        return Decimal("0")
    return _decimal_parts(value)


def _pick(data: dict, *names: str) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


def _require_money(data: dict, names: tuple[str, ...], field: str) -> Decimal:
    value = _pick(data, *names)
    if not isinstance(value, dict):
        raise RuntimeError(f"Hard risk: {field} is unavailable")
    return _decimal_parts(value)


def _is_base_rub_cash(position: dict[str, Any]) -> bool:
    instrument_type = str(
        _pick(position, "instrumentType", "instrument_type") or ""
    ).lower()
    ticker = str(position.get("ticker") or "").upper()
    class_code = str(
        _pick(position, "classCode", "class_code") or ""
    ).upper()
    return (
        instrument_type == "currency"
        and (ticker == "RUB000UTSTOM" or (ticker.startswith("RUB") and class_code == "CETS"))
    )


def _positive_position_count(portfolio: dict) -> int:
    count = 0
    for position in portfolio.get("positions") or []:
        if _is_base_rub_cash(position):
            continue
        quantity = _quotation_or_zero(
            _pick(position, "quantity", "quantity_lots", "quantityLots")
        )
        if quantity > 0:
            count += 1
    return count




def compute_consecutive_losses_from_lifecycle(
    lifecycle_states: list[dict[str, Any]],
    *,
    since_utc: str,
) -> int:
    since = parse_iso_utc(since_utc)
    closed_statuses = {
        "CLOSED_STOP_LOSS",
        "CLOSED_TAKE_PROFIT",
        "CLOSED_TIME_STOP",
        "CLOSED_FORCE_EXIT",
        "CLOSED_POSITION_GONE",
    }
    outcomes: list[tuple[Any, Decimal]] = []

    for state in lifecycle_states:
        if str(state.get("lifecycle_kind") or "") != "STRATEGY":
            continue
        if str(state.get("status") or "") not in closed_statuses:
            continue

        updated_raw = str(state.get("updated_at") or "")
        if not updated_raw:
            raise RuntimeError(
                "Hard risk: closed lifecycle state has no updated_at"
            )
        updated_at = parse_iso_utc(updated_raw)
        if updated_at < since:
            continue

        pnl_raw = state.get("realized_pnl_rub")
        if pnl_raw in (None, ""):
            raise RuntimeError(
                "Hard risk: realized strategy P&L unavailable for loss streak"
            )
        try:
            pnl = Decimal(str(pnl_raw))
        except Exception as exc:
            raise RuntimeError(
                "Hard risk: invalid realized strategy P&L"
            ) from exc
        if not pnl.is_finite():
            raise RuntimeError(
                "Hard risk: non-finite realized strategy P&L"
            )
        outcomes.append((updated_at, pnl))

    outcomes.sort(key=lambda item: item[0])
    count = 0
    for _, pnl in reversed(outcomes):
        if pnl < 0:
            count += 1
            continue
        break
    return count


def compute_consecutive_losses(
    operations_since_baseline: dict,
) -> int:
    items = (
        operations_since_baseline.get("items")
        or operations_since_baseline.get("operations")
        or []
    )
    sells = [
        item
        for item in items
        if str(item.get("type") or "").upper() == "OPERATION_TYPE_SELL"
    ]
    sells.sort(key=lambda item: str(item.get("date") or ""))

    count = 0
    for operation in reversed(sells):
        value = operation.get("yield")
        if not isinstance(value, dict):
            raise RuntimeError(
                "Hard risk: realized SELL yield unavailable for loss streak"
            )
        result = _decimal_parts(value)
        if result < 0:
            count += 1
            continue
        break
    return count

def compute_daily_pnl_rub(
    *,
    current_portfolio: dict,
    baseline_payload: dict,
    operations_since_baseline: dict,
) -> Decimal:
    if baseline_payload.get("environment") != "TINVEST_SANDBOX":
        raise RuntimeError("Hard risk: baseline environment mismatch")

    generated_at = str(baseline_payload.get("generated_at_utc") or "")
    if not generated_at:
        raise RuntimeError("Hard risk: baseline timestamp is unavailable")
    parse_iso_utc(generated_at)

    baseline_portfolio = baseline_payload.get("portfolio")
    if not isinstance(baseline_portfolio, dict):
        raise RuntimeError("Hard risk: baseline portfolio is unavailable")

    baseline_equity = _require_money(
        baseline_portfolio,
        ("totalAmountPortfolio", "total_amount_portfolio"),
        "baseline portfolio capital",
    )
    current_equity = _require_money(
        current_portfolio,
        ("totalAmountPortfolio", "total_amount_portfolio"),
        "current portfolio capital",
    )

    items = (
        operations_since_baseline.get("items")
        or operations_since_baseline.get("operations")
        or []
    )
    for operation in items:
        op_type = str(operation.get("type") or "").upper()
        if op_type in {"OPERATION_TYPE_INPUT", "OPERATION_TYPE_OUTPUT"}:
            raise RuntimeError(
                "Hard risk: account funding/withdrawal after daily baseline"
            )

    return current_equity - baseline_equity

def compute_period_pnl_rub(
    *,
    current_portfolio: dict,
    start_equity_rub: Decimal,
    operations_since_start: dict,
    period_name: str,
) -> Decimal:
    if (
        not start_equity_rub.is_finite()
        or start_equity_rub <= 0
    ):
        raise RuntimeError(
            f"Hard risk: {period_name} start equity is invalid"
        )
    current_equity = _require_money(
        current_portfolio,
        ("totalAmountPortfolio", "total_amount_portfolio"),
        "current portfolio capital",
    )
    items = (
        operations_since_start.get("items")
        or operations_since_start.get("operations")
        or []
    )
    for operation in items:
        op_type = str(operation.get("type") or "").upper()
        if op_type in {"OPERATION_TYPE_INPUT", "OPERATION_TYPE_OUTPUT"}:
            raise RuntimeError(
                f"Hard risk: account funding/withdrawal during {period_name}"
            )
    return current_equity - start_equity_rub


def latest_strategy_close_time(
    lifecycle_states: list[dict[str, Any]],
    *,
    since_utc: str,
) -> datetime | None:
    since = parse_iso_utc(since_utc)
    closed_statuses = {
        "CLOSED_STOP_LOSS",
        "CLOSED_TAKE_PROFIT",
        "CLOSED_TIME_STOP",
        "CLOSED_FORCE_EXIT",
        "CLOSED_POSITION_GONE",
    }
    times: list[datetime] = []
    for state in lifecycle_states:
        if str(state.get("lifecycle_kind") or "") != "STRATEGY":
            continue
        if str(state.get("status") or "") not in closed_statuses:
            continue
        raw = str(state.get("updated_at") or "")
        if not raw:
            continue
        dt = parse_iso_utc(raw)
        if dt >= since:
            times.append(dt)
    return max(times) if times else None


@dataclass(frozen=True)
class RiskCheck:
    capital_rub: Decimal
    daily_yield_rub: Decimal
    position_value_rub: Decimal
    max_loss_rub: Decimal
    risk_budget_rub: Decimal
    position_cap_rub: Decimal
    open_positions: int
    net_risk_rub: Decimal
    net_reward_rub: Decimal
    risk_reward_net: Decimal
    weekly_pnl_rub: Decimal
    monthly_pnl_rub: Decimal
    drawdown_from_high_water: Decimal
    risk_budget_multiplier: Decimal


def validate_buy_hard_risk(
    *,
    command: TradeCommand,
    portfolio: dict,
    preflight_order_price: dict,
    instrument_lot: int,
    min_price_increment: Decimal,
    market_spread_per_unit: Decimal,
    daily_pnl_rub: Decimal | None = None,
    weekly_pnl_rub: Decimal | None = None,
    monthly_pnl_rub: Decimal | None = None,
    week_start_equity_rub: Decimal | None = None,
    month_start_equity_rub: Decimal | None = None,
    high_water_mark_rub: Decimal | None = None,
    consecutive_losses: int | None = None,
    last_strategy_close_at: datetime | None = None,
    now: datetime | None = None,
) -> RiskCheck:
    if command.action != "BUY":
        raise ValueError("Hard-risk BUY validator requires BUY command")
    if command.limit_price is None or command.stop_loss is None:
        raise ValueError("BUY requires limit_price and stop_loss")
    if instrument_lot <= 0:
        raise ValueError("Instrument lot must be positive")
    if not min_price_increment.is_finite() or min_price_increment <= 0:
        raise ValueError("min_price_increment must be finite and positive")
    if (
        not market_spread_per_unit.is_finite()
        or market_spread_per_unit < 0
    ):
        raise ValueError("market_spread_per_unit must be finite and non-negative")
    if command.take_profit is None:
        raise ValueError("BUY requires take_profit")

    capital = _require_money(
        portfolio,
        ("totalAmountPortfolio", "total_amount_portfolio"),
        "portfolio capital",
    )
    if capital <= 0:
        raise RuntimeError("Hard risk: portfolio capital must be positive")

    # Prefer the independently computed baseline-to-current equity P&L.
    # Fall back to broker dailyYield only when it is actually present.
    if daily_pnl_rub is None:
        daily_yield = _require_money(
            portfolio,
            ("dailyYield", "daily_yield"),
            "daily P&L",
        )
    else:
        daily_yield = Decimal(daily_pnl_rub)
    if daily_yield <= -(capital * DAILY_STOP):
        raise RuntimeError("Hard risk: daily loss limit reached")

    if (
        weekly_pnl_rub is None
        or week_start_equity_rub is None
        or monthly_pnl_rub is None
        or month_start_equity_rub is None
        or high_water_mark_rub is None
    ):
        raise RuntimeError(
            "Hard risk: weekly/monthly/high-water context is unavailable"
        )

    weekly_pnl = Decimal(weekly_pnl_rub)
    monthly_pnl = Decimal(monthly_pnl_rub)
    week_start = Decimal(week_start_equity_rub)
    month_start = Decimal(month_start_equity_rub)
    high_water = Decimal(high_water_mark_rub)
    for value, name in (
        (weekly_pnl, "weekly P&L"),
        (monthly_pnl, "monthly P&L"),
        (week_start, "week start equity"),
        (month_start, "month start equity"),
        (high_water, "high water mark"),
    ):
        if not value.is_finite():
            raise RuntimeError(f"Hard risk: {name} is not finite")

    if week_start <= 0 or month_start <= 0 or high_water <= 0:
        raise RuntimeError("Hard risk: invalid period/high-water equity")

    if weekly_pnl <= -(week_start * WEEKLY_STOP):
        raise RuntimeError("Hard risk: weekly loss limit reached")
    if monthly_pnl <= -(month_start * MONTHLY_STOP):
        raise RuntimeError("Hard risk: monthly loss limit reached")

    effective_high_water = max(high_water, capital)
    drawdown = (
        (effective_high_water - capital) / effective_high_water
    )
    risk_budget_multiplier = Decimal("1")
    if drawdown > DRAWDOWN_RISK_REDUCTION_TRIGGER:
        risk_budget_multiplier *= DRAWDOWN_RISK_MULTIPLIER
    if command.market_regime == "UNKNOWN":
        risk_budget_multiplier *= Decimal("0.5")

    if consecutive_losses is None:
        raise RuntimeError("Hard risk: consecutive loss count is unavailable")
    if consecutive_losses >= 3:
        raise RuntimeError("Hard risk: 3 consecutive losses reached")
    if consecutive_losses >= 2:
        if last_strategy_close_at is None:
            raise RuntimeError(
                "Hard risk: last loss timestamp unavailable after 2 losses"
            )
        if last_strategy_close_at.tzinfo is None:
            raise RuntimeError(
                "Hard risk: last loss timestamp has no timezone"
            )
        current_time = now or datetime.now(timezone.utc)
        if current_time < (
            last_strategy_close_at.astimezone(timezone.utc)
            + timedelta(hours=2)
        ):
            raise RuntimeError(
                "Hard risk: 2-loss cooldown is still active"
            )

    open_positions = _positive_position_count(portfolio)
    if open_positions >= MAX_OPEN_POSITIONS:
        raise RuntimeError("Hard risk: maximum open positions reached")

    initial_amount = _require_money(
        preflight_order_price,
        ("initialOrderAmount", "initial_order_amount"),
        "initial order amount",
    )
    total_amount = _require_money(
        preflight_order_price,
        ("totalOrderAmount", "total_order_amount"),
        "total order amount",
    )
    position_value = max(abs(initial_amount), abs(total_amount))
    if position_value <= 0:
        raise RuntimeError("Hard risk: order value must be positive")

    position_cap = capital * MAX_POSITION_SHARE
    if position_value > position_cap:
        raise RuntimeError("Hard risk: position exceeds 10% of capital")

    commission_buy = _require_money(
        preflight_order_price,
        (
            "executedCommissionRub",
            "executed_commission_rub",
            "executedCommission",
            "executed_commission",
        ),
        "broker commission estimate",
    )
    estimated_round_trip_commission = abs(commission_buy) * Decimal("2")

    units = Decimal(command.quantity_lots * instrument_lot)
    price_risk = (command.limit_price - command.stop_loss) * units
    if price_risk <= 0:
        raise RuntimeError("Hard risk: invalid stop distance")

    # Code-level execution-cost floor: at least one tick on entry and
    # one tick on exit, but never less than the currently observed full
    # bid/ask spread. Work may use a larger slippage estimate.
    execution_friction_per_unit = max(
        min_price_increment * Decimal("2"),
        market_spread_per_unit,
    )
    min_round_trip_slippage = execution_friction_per_unit * units
    net_risk = (
        price_risk
        + estimated_round_trip_commission
        + min_round_trip_slippage
    )
    gross_reward = (
        command.take_profit - command.limit_price
    ) * units
    net_reward = (
        gross_reward
        - estimated_round_trip_commission
        - min_round_trip_slippage
    )
    if net_reward <= 0:
        raise RuntimeError("Hard risk: net reward is not positive")

    risk_reward_net = net_reward / net_risk
    if risk_reward_net < MIN_NET_RISK_REWARD:
        raise RuntimeError(
            "Hard risk: net risk/reward below 2.0"
        )

    max_loss = net_risk
    risk_budget = (
        capital
        * RISK_PER_TRADE
        * risk_budget_multiplier
    )
    if max_loss > risk_budget:
        raise RuntimeError("Hard risk: max loss exceeds 0.25% of capital")

    return RiskCheck(
        capital_rub=capital,
        daily_yield_rub=daily_yield,
        position_value_rub=position_value,
        max_loss_rub=max_loss,
        risk_budget_rub=risk_budget,
        position_cap_rub=position_cap,
        open_positions=open_positions,
        net_risk_rub=net_risk,
        net_reward_rub=net_reward,
        risk_reward_net=risk_reward_net,
        weekly_pnl_rub=weekly_pnl,
        monthly_pnl_rub=monthly_pnl,
        drawdown_from_high_water=drawdown,
        risk_budget_multiplier=risk_budget_multiplier,
    )
