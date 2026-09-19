from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .protocol import TradeCommand


RISK_PER_TRADE = Decimal("0.0025")
DAILY_STOP = Decimal("0.0075")
MAX_POSITION_SHARE = Decimal("0.10")
MAX_OPEN_POSITIONS = 2


def _decimal_parts(value: Any) -> Decimal:
    if not isinstance(value, dict):
        return Decimal("0")
    units = Decimal(str(value.get("units", "0")))
    nano = Decimal(str(value.get("nano", 0))) / Decimal("1000000000")
    return units + nano


def _quotation(value: Any) -> Decimal:
    return _decimal_parts(value)


def _pick(data: dict, *names: str) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


def _positive_position_count(portfolio: dict) -> int:
    count = 0
    for position in portfolio.get("positions") or []:
        quantity = _quotation(
            _pick(position, "quantity", "quantity_lots", "quantityLots")
        )
        if quantity > 0:
            count += 1
    return count


@dataclass(frozen=True)
class RiskCheck:
    capital_rub: Decimal
    daily_yield_rub: Decimal
    position_value_rub: Decimal
    max_loss_rub: Decimal
    risk_budget_rub: Decimal
    position_cap_rub: Decimal
    open_positions: int


def validate_buy_hard_risk(
    *,
    command: TradeCommand,
    portfolio: dict,
    preflight_order_price: dict,
    instrument_lot: int,
) -> RiskCheck:
    if command.action != "BUY":
        raise ValueError("Hard-risk BUY validator requires BUY command")
    if command.limit_price is None or command.stop_loss is None:
        raise ValueError("BUY requires limit_price and stop_loss")
    if instrument_lot <= 0:
        raise ValueError("Instrument lot must be positive")

    capital = _decimal_parts(
        _pick(
            portfolio,
            "totalAmountPortfolio",
            "total_amount_portfolio",
        )
    )
    if capital <= 0:
        raise RuntimeError("Hard risk: portfolio capital is unavailable")

    daily_yield = _decimal_parts(
        _pick(portfolio, "dailyYield", "daily_yield")
    )
    if daily_yield <= -(capital * DAILY_STOP):
        raise RuntimeError("Hard risk: daily loss limit reached")

    open_positions = _positive_position_count(portfolio)
    if open_positions >= MAX_OPEN_POSITIONS:
        raise RuntimeError("Hard risk: maximum open positions reached")

    initial_amount = _decimal_parts(
        _pick(
            preflight_order_price,
            "initialOrderAmount",
            "initial_order_amount",
        )
    )
    total_amount = _decimal_parts(
        _pick(
            preflight_order_price,
            "totalOrderAmount",
            "total_order_amount",
        )
    )
    position_value = max(abs(initial_amount), abs(total_amount))
    if position_value <= 0:
        raise RuntimeError("Hard risk: order value is unavailable")

    position_cap = capital * MAX_POSITION_SHARE
    if position_value > position_cap:
        raise RuntimeError(
            "Hard risk: position exceeds 10% of capital"
        )

    commission_buy = _decimal_parts(
        _pick(
            preflight_order_price,
            "executedCommissionRub",
            "executed_commission_rub",
            "executedCommission",
            "executed_commission",
        )
    )
    # Sandbox commission is symmetric enough for a conservative round-trip
    # guard. The strategy separately evaluates realistic Trader-tariff costs.
    estimated_round_trip_commission = abs(commission_buy) * Decimal("2")

    units = Decimal(command.quantity_lots * instrument_lot)
    price_risk = (
        command.limit_price - command.stop_loss
    ) * units
    if price_risk <= 0:
        raise RuntimeError("Hard risk: invalid stop distance")

    max_loss = price_risk + estimated_round_trip_commission
    risk_budget = capital * RISK_PER_TRADE
    if max_loss > risk_budget:
        raise RuntimeError(
            "Hard risk: max loss exceeds 0.25% of capital"
        )

    return RiskCheck(
        capital_rub=capital,
        daily_yield_rub=daily_yield,
        position_value_rub=position_value,
        max_loss_rub=max_loss,
        risk_budget_rub=risk_budget,
        position_cap_rub=position_cap,
        open_positions=open_positions,
    )
