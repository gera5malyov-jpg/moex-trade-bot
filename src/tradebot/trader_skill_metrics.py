"""Read-only daily execution diagnostics inspired by Trader Skill postmortems.

This module NEVER places orders and NEVER equates broker operations with
strategy performance. T-Invest operations do not include the lifecycle_kind
needed to distinguish deliberate STRATEGY trades from SMOKE_TEST operations.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _rub_money(value: Any) -> Decimal | None:
    if not isinstance(value, dict) or str(value.get("currency", "")).lower() != "rub":
        return None
    try:
        units = Decimal(str(value.get("units", "0")))
        nano = Decimal(str(value.get("nano", 0)))
        amount = units + nano / Decimal("1000000000")
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount.is_finite() else None


def summarize_daily_trade_activity(operations: dict[str, Any]) -> dict[str, Any]:
    """Summarize *observed* operations, not inferred returns.

    BUY/SELL payments are gross cash movements. Broker fee operations are
    counted once via their own payment; the commission mirrored in BUY/SELL
    records is deliberately ignored to prevent double counting.

    The resulting net_execution_cashflow_rub is NOT a performance or P&L
    figure, particularly when positions remain open or trades are smoke tests.
    """
    records = (operations or {}).get("items")
    if records is None:
        records = (operations or {}).get("operations", [])
    if not isinstance(records, list):
        records = []

    seen_ids: set[str] = set()
    buys = sells = fees = unpriced = 0
    cashflow = Decimal("0")
    explicit_fees = Decimal("0")

    for record in records:
        if not isinstance(record, dict):
            continue
        operation_id = record.get("id")
        if isinstance(operation_id, str) and operation_id:
            if operation_id in seen_ids:
                continue
            seen_ids.add(operation_id)

        if record.get("state") != "OPERATION_STATE_EXECUTED":
            continue

        kind = record.get("type")
        if kind not in {
            "OPERATION_TYPE_BUY",
            "OPERATION_TYPE_SELL",
            "OPERATION_TYPE_BROKER_FEE",
        }:
            continue

        amount = _rub_money(record.get("payment"))
        if kind == "OPERATION_TYPE_BUY":
            buys += 1
        elif kind == "OPERATION_TYPE_SELL":
            sells += 1
        else:
            fees += 1

        if amount is None:
            unpriced += 1
            continue
        cashflow += amount
        if kind == "OPERATION_TYPE_BROKER_FEE":
            explicit_fees += amount

    return {
        "schema_version": "1",
        "environment": "TINVEST_SANDBOX",
        "executed_buys": buys,
        "executed_sells": sells,
        "explicit_broker_fee_operations": fees,
        "explicit_broker_fee_cashflow_rub": str(explicit_fees),
        "net_execution_cashflow_rub": None if unpriced else str(cashflow),
        "missing_rub_payment_count": unpriced,
        "strategy_trade_count": None,
        "strategy_realized_pnl_rub": None,
        "classification": (
            "NO_EXECUTED_TRADES" if buys + sells == 0
            else "UNCLASSIFIED_EXECUTIONS_REQUIRES_LIFECYCLE_JOURNAL"
        ),
        "explanation": (
            "Cashflow is not P&L. Trade operations do not identify SMOKE_TEST "
            "vs strategy execution. Match lifecycle_kind, fills and actual "
            "fees before reporting strategy win rate, profit or drawdown."
        ),
    }
