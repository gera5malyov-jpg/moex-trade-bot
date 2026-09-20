from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .protocol import (
    PROTECTIVE_LIFECYCLE_VERSION,
    TradeCommand,
    parse_iso_utc,
    utc_now,
)
from .tinvest import TInvestSandboxClient


FINAL_STATUSES = {
    "ENTRY_NOT_FILLED",
    "CLOSED_STOP_LOSS",
    "CLOSED_TAKE_PROFIT",
    "CLOSED_TIME_STOP",
    "CLOSED_FORCE_EXIT",
    "CLOSED_POSITION_GONE",
}


def _pick(data: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


def _as_int(data: dict[str, Any], *names: str) -> int:
    value = _pick(data, *names)
    if value in (None, ""):
        return 0
    return int(value)


def _money_decimal_or_none(value: Any) -> Decimal | None:
    if not isinstance(value, dict):
        return None
    try:
        result = (
            Decimal(str(value.get("units", "0")))
            + Decimal(str(value.get("nano", 0)))
            / Decimal("1000000000")
        )
    except Exception:
        return None
    return result if result.is_finite() else None


def _execution_price(payload: dict[str, Any]) -> Decimal | None:
    return _money_decimal_or_none(
        _pick(
            payload,
            "executedOrderPrice",
            "executed_order_price",
        )
    )


def _execution_commission(payload: dict[str, Any]) -> Decimal | None:
    value = _pick(
        payload,
        "executedCommission",
        "executed_commission",
    )
    result = _money_decimal_or_none(value)
    return abs(result) if result is not None else None


def _append_exit_component(
    state: dict[str, Any],
    *,
    payload: dict[str, Any],
    source: str,
    order_id: str,
) -> dict[str, Any]:
    lots = _as_int(payload, "lotsExecuted", "lots_executed")
    price = _execution_price(payload)
    commission = _execution_commission(payload)
    if lots <= 0 or price is None or commission is None:
        return state

    components = [
        dict(item)
        for item in (state.get("exit_components") or [])
        if isinstance(item, dict)
    ]
    if any(str(item.get("order_id") or "") == order_id for item in components):
        return state

    out = dict(state)
    components.append(
        {
            "source": source,
            "order_id": order_id,
            "lots": lots,
            "executed_price": str(price),
            "commission_rub": str(commission),
        }
    )
    out["exit_components"] = components
    return out


def _finalize_realized_pnl(state: dict[str, Any]) -> dict[str, Any]:
    out = dict(state)
    if str(out.get("lifecycle_kind") or "") != "STRATEGY":
        return out

    try:
        entry_price = Decimal(str(out["entry_executed_price"]))
        entry_commission = Decimal(str(out["entry_commission_rub"]))
        filled_lots = int(out["filled_lots"])
        lot_size = int(out["lot_size"])
    except Exception:
        out["realized_pnl_error"] = "entry execution data unavailable"
        return out

    components = [
        item
        for item in (out.get("exit_components") or [])
        if isinstance(item, dict)
    ]
    exited_lots = sum(int(item.get("lots") or 0) for item in components)
    if exited_lots != filled_lots:
        out["realized_pnl_error"] = (
            f"exit lots mismatch: expected {filled_lots}, got {exited_lots}"
        )
        return out

    exit_proceeds = Decimal("0")
    exit_commission = Decimal("0")
    for item in components:
        try:
            lots = Decimal(int(item["lots"]))
            price = Decimal(str(item["executed_price"]))
            commission = Decimal(str(item["commission_rub"]))
        except Exception:
            out["realized_pnl_error"] = "invalid exit execution component"
            return out
        exit_proceeds += price * lots * Decimal(lot_size)
        exit_commission += commission

    entry_cost = (
        entry_price * Decimal(filled_lots * lot_size)
        + entry_commission
    )
    realized = exit_proceeds - exit_commission - entry_cost
    out["realized_pnl_rub"] = str(realized)
    out["realized_pnl_error"] = None
    return out


def _status(data: dict[str, Any]) -> str:
    return str(
        _pick(
            data,
            "executionReportStatus",
            "execution_report_status",
            "status",
        )
        or ""
    ).upper()


def _stop_order_id(data: dict[str, Any]) -> str:
    return str(
        _pick(data, "stopOrderId", "stop_order_id") or ""
    )


def _exchange_order_id(data: dict[str, Any]) -> str:
    return str(
        _pick(data, "exchangeOrderId", "exchange_order_id") or ""
    )


def _best_bid(
    client: TInvestSandboxClient,
    instrument_uid: str,
) -> Decimal:
    book = client.get_order_book(instrument_uid, depth=1)
    bids = book.get("bids") or []
    if not bids:
        raise RuntimeError("Protective exit: best bid is unavailable")
    price = bids[0].get("price")
    if not isinstance(price, dict):
        raise RuntimeError("Protective exit: best bid is invalid")
    value = (
        Decimal(str(price.get("units", "0")))
        + Decimal(str(price.get("nano", 0)))
        / Decimal("1000000000")
    )
    if value <= 0:
        raise RuntimeError("Protective exit: best bid must be positive")
    return value


def _cancel_if_present(
    client: TInvestSandboxClient,
    stop_order_id: str | None,
) -> None:
    if not stop_order_id:
        return
    try:
        client.cancel_stop_order(stop_order_id)
    except Exception:
        # Cancellation is best-effort here. The monitor retries and all
        # protective SELL orders are submitted with confirmMarginTrade=false.
        pass


def _cancel_all_known_protection(
    client: TInvestSandboxClient,
    state: dict[str, Any],
) -> None:
    _cancel_if_present(client, str(state.get("stop_order_id") or ""))
    _cancel_if_present(client, str(state.get("take_order_id") or ""))


def _known_protection_items(
    client: TInvestSandboxClient,
    state: dict[str, Any],
    *,
    status: str = "STOP_ORDER_STATUS_ALL",
) -> dict[str, dict[str, Any] | None]:
    orders = client.get_stop_orders(status=status)
    result: dict[str, dict[str, Any] | None] = {}
    for role, key in (
        ("STOP_LOSS", "stop_order_id"),
        ("TAKE_PROFIT", "take_order_id"),
    ):
        stop_id = str(state.get(key) or "")
        if not stop_id:
            result[role] = None
            continue
        match = None
        for item in orders:
            if _stop_order_id(item) == stop_id:
                match = item
                break
        result[role] = match
    return result


def _cancel_verified_or_wait(
    *,
    client: TInvestSandboxClient,
    state: dict[str, Any],
    reason: str,
    closed_status: str,
) -> dict[str, Any]:
    _cancel_all_known_protection(client, state)

    active_items = _known_protection_items(
        client,
        state,
        status="STOP_ORDER_STATUS_ACTIVE",
    )
    still_active = [
        role
        for role, item in active_items.items()
        if item is not None
    ]
    if still_active:
        out = dict(state)
        out.update(
            {
                "status": "PROTECTION_CANCEL_PENDING",
                "close_reason": reason,
                "pending_closed_status": closed_status,
                "cancel_pending_roles": still_active,
                "updated_at": utc_now().isoformat(),
            }
        )
        return out

    all_items = _known_protection_items(client, state)
    pending_children: list[dict[str, str]] = []
    missing_ids: list[str] = []
    for role, key in (
        ("STOP_LOSS", "stop_order_id"),
        ("TAKE_PROFIT", "take_order_id"),
    ):
        stop_id = str(state.get(key) or "")
        if not stop_id:
            continue
        item = all_items.get(role)
        if item is None:
            missing_ids.append(stop_id)
            continue
        if _triggered(item):
            pending_children.append(
                {
                    "role": role,
                    "stop_order_id": stop_id,
                    "exchange_order_id": _exchange_order_id(item),
                }
            )

    if pending_children:
        derived_closed_status = closed_status
        if len(pending_children) == 1:
            role = pending_children[0]["role"]
            if role == "STOP_LOSS":
                derived_closed_status = "CLOSED_STOP_LOSS"
            elif role == "TAKE_PROFIT":
                derived_closed_status = "CLOSED_TAKE_PROFIT"
        out = dict(state)
        out.update(
            {
                "status": "PROTECTIVE_CHILD_PENDING",
                "close_reason": reason,
                "pending_closed_status": derived_closed_status,
                "pending_children": pending_children,
                "updated_at": utc_now().isoformat(),
            }
        )
        return out

    if missing_ids:
        out = dict(state)
        out.update(
            {
                "status": "PROTECTION_CANCEL_PENDING",
                "close_reason": reason,
                "pending_closed_status": closed_status,
                "cancel_pending_missing_ids": missing_ids,
                "updated_at": utc_now().isoformat(),
            }
        )
        return out

    return _force_exit_remaining(
        client=client,
        state=state,
        reason=reason,
        closed_status=closed_status,
    )


def _force_exit_remaining(
    *,
    client: TInvestSandboxClient,
    state: dict[str, Any],
    reason: str,
    closed_status: str,
) -> dict[str, Any]:
    remaining = client.get_position_lots(
        instrument_uid=str(state["instrument_uid"]),
        lot_size=int(state["lot_size"]),
    )
    if remaining <= 0:
        out = dict(state)
        out.update(
            {
                "status": closed_status,
                "close_reason": reason,
                "updated_at": utc_now().isoformat(),
                "remaining_lots": 0,
            }
        )
        return _finalize_realized_pnl(out)

    bid = _best_bid(client, str(state["instrument_uid"]))
    attempt = int(state.get("force_exit_attempts") or 0) + 1
    result = client.post_limit_order(
        ticker=str(state["ticker"]),
        class_code=str(state["class_code"]),
        instrument_uid=str(state["instrument_uid"]),
        side="SELL",
        quantity_lots=remaining,
        limit_price=bid,
        idempotency_seed=(
            f"moex-trade-bot:lifecycle:{state['signal_id']}:"
            f"force-exit:{attempt}"
        ),
    )

    post = result.get("post_order") or {}
    lots_executed = _as_int(post, "lotsExecuted", "lots_executed")
    state_with_exit = _append_exit_component(
        state,
        payload=post,
        source="FORCE_EXIT",
        order_id=str(_pick(post, "orderId", "order_id") or result.get("request_order_id") or ""),
    )
    after = client.get_position_lots(
        instrument_uid=str(state["instrument_uid"]),
        lot_size=int(state["lot_size"]),
    )

    out = dict(state_with_exit)
    out.update(
        {
            "updated_at": utc_now().isoformat(),
            "force_exit_attempts": attempt,
            "force_exit_last_bid": str(bid),
            "force_exit_last_lots_requested": remaining,
            "force_exit_last_lots_executed": lots_executed,
            "remaining_lots": after,
            "close_reason": reason,
        }
    )
    if after <= 0:
        out["status"] = closed_status
        out = _finalize_realized_pnl(out)
    else:
        out["status"] = "FORCE_EXIT_PENDING"
        out["pending_closed_status"] = closed_status
    return out


def open_protected_long(
    *,
    client: TInvestSandboxClient,
    command: TradeCommand,
    prepared: dict[str, Any],
) -> dict[str, Any]:
    if command.action != "BUY":
        raise ValueError("Protected lifecycle requires BUY")
    if (
        command.limit_price is None
        or command.stop_loss is None
        or command.take_profit is None
        or command.time_stop is None
    ):
        raise ValueError("BUY protective plan is incomplete")

    lot_size = int(prepared["instrument"].get("lot") or 0)
    if lot_size <= 0:
        raise RuntimeError("Protected lifecycle: instrument lot is unavailable")

    entry = client.post_limit_order(
        ticker=command.ticker,
        class_code=command.class_code,
        instrument_uid=command.instrument_uid,
        side="BUY",
        quantity_lots=command.quantity_lots,
        limit_price=command.limit_price,
        idempotency_seed=f"moex-trade-bot:signal:{command.signal_id}:entry",
        prepared=prepared,
    )
    post = entry.get("post_order") or {}
    entry_status = _status(post)
    lots_executed = _as_int(post, "lotsExecuted", "lots_executed")
    entry_exchange_order_id = str(
        _pick(post, "orderId", "order_id") or ""
    )

    state: dict[str, Any] = {
        "lifecycle_version": PROTECTIVE_LIFECYCLE_VERSION,
        "environment": "TINVEST_SANDBOX",
        "sandbox_account_id": client.account_id,
        "signal_id": command.signal_id,
        "ticker": command.ticker,
        "class_code": command.class_code,
        "instrument_uid": command.instrument_uid,
        "instrument_type": command.instrument_type,
        "lot_size": lot_size,
        "entry_limit_price": str(command.limit_price),
        "stop_loss": str(command.stop_loss),
        "take_profit": str(command.take_profit),
        "time_stop": command.time_stop.isoformat(),
        "entry_request_id": str(entry.get("request_order_id") or ""),
        "entry_exchange_order_id": entry_exchange_order_id,
        "entry_status": entry_status,
        "requested_lots": command.quantity_lots,
        "filled_lots": lots_executed,
        "lifecycle_kind": (
            "SMOKE_TEST"
            if command.reviewer_note.startswith("SANDBOX_LIFECYCLE_SMOKE_TEST")
            else "STRATEGY"
        ),
        "entry_executed_price": (
            str(_execution_price(post))
            if _execution_price(post) is not None
            else None
        ),
        "entry_commission_rub": (
            str(_execution_commission(post))
            if _execution_commission(post) is not None
            else None
        ),
        "exit_components": [],
        "created_at": utc_now().isoformat(),
        "updated_at": utc_now().isoformat(),
        "status": "ENTRY_NOT_FILLED",
    }

    if lots_executed <= 0:
        return state

    stop_id = ""
    take_id = ""
    try:
        stop_result = client.post_stop_order(
            instrument_uid=command.instrument_uid,
            quantity_lots=lots_executed,
            stop_price=command.stop_loss,
            stop_order_type="STOP_ORDER_TYPE_STOP_LOSS",
            idempotency_seed=(
                f"moex-trade-bot:lifecycle:{command.signal_id}:stop-loss"
            ),
        )
        stop_payload = stop_result.get("stop_order") or {}
        stop_id = _stop_order_id(stop_payload)
        if not stop_id:
            raise RuntimeError("STOP_LOSS returned no stop_order_id")

        take_result = client.post_stop_order(
            instrument_uid=command.instrument_uid,
            quantity_lots=lots_executed,
            stop_price=command.take_profit,
            stop_order_type="STOP_ORDER_TYPE_TAKE_PROFIT",
            idempotency_seed=(
                f"moex-trade-bot:lifecycle:{command.signal_id}:take-profit"
            ),
        )
        take_payload = take_result.get("stop_order") or {}
        take_id = _stop_order_id(take_payload)
        if not take_id:
            raise RuntimeError("TAKE_PROFIT returned no stop_order_id")

        state.update(
            {
                "status": "PROTECTED",
                "stop_order_id": stop_id,
                "take_order_id": take_id,
                "stop_request_id": str(
                    stop_result.get("request_order_id") or ""
                ),
                "take_request_id": str(
                    take_result.get("request_order_id") or ""
                ),
            }
        )
        return state
    except Exception as exc:
        state.update(
            {
                "status": "PROTECTION_SETUP_FAILED",
                "stop_order_id": stop_id,
                "take_order_id": take_id,
                "protection_error": f"{type(exc).__name__}: {exc}",
            }
        )
        return _cancel_verified_or_wait(
            client=client,
            state=state,
            reason="PROTECTION_SETUP_FAILED",
            closed_status="CLOSED_FORCE_EXIT",
        )


def _find_stop(
    stop_orders: list[dict[str, Any]],
    stop_order_id: str,
) -> dict[str, Any] | None:
    for item in stop_orders:
        if _stop_order_id(item) == stop_order_id:
            return item
    return None


def _triggered(item: dict[str, Any] | None) -> bool:
    if not item:
        return False
    status = str(item.get("status") or "").upper()
    activation = str(
        _pick(item, "activationDateTime", "activation_date_time") or ""
    )
    return (
        status == "STOP_ORDER_STATUS_EXECUTED"
        or bool(activation)
        or bool(_exchange_order_id(item))
    )


def monitor_lifecycle_state(
    *,
    client: TInvestSandboxClient,
    state: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    current_status = str(state.get("status") or "")
    if current_status in FINAL_STATUSES:
        return state

    now = now or datetime.now(timezone.utc)
    position_lots = client.get_position_lots(
        instrument_uid=str(state["instrument_uid"]),
        lot_size=int(state["lot_size"]),
    )

    if current_status in {
        "FORCE_EXIT_PENDING",
        "PROTECTION_CANCEL_PENDING",
    }:
        return _cancel_verified_or_wait(
            client=client,
            state=state,
            reason=str(state.get("close_reason") or "FORCE_EXIT_RETRY"),
            closed_status=str(
                state.get("pending_closed_status") or "CLOSED_FORCE_EXIT"
            ),
        )

    if current_status == "PROTECTIVE_CHILD_PENDING":
        children = state.get("pending_children") or []
        if not children:
            out = dict(state)
            out.update(
                {
                    "updated_at": now.isoformat(),
                    "child_wait_error": "missing pending_children",
                }
            )
            return out

        child_statuses: list[dict[str, str]] = []
        refreshed_children: list[dict[str, str]] = []
        working_state = dict(state)
        all_items = _known_protection_items(client, state)
        for child_info in children:
            role = str(child_info.get("role") or "")
            child_id = str(child_info.get("exchange_order_id") or "")
            if not child_id:
                item = all_items.get(role)
                if item is not None:
                    child_id = _exchange_order_id(item)
            refreshed_children.append(
                {
                    "role": role,
                    "stop_order_id": str(
                        child_info.get("stop_order_id") or ""
                    ),
                    "exchange_order_id": child_id,
                }
            )
            if not child_id:
                out = dict(state)
                out.update(
                    {
                        "pending_children": refreshed_children,
                        "updated_at": now.isoformat(),
                        "child_wait_error": (
                            "triggered stop has no exchange_order_id yet"
                        ),
                    }
                )
                return out
            child = client.get_order_state(child_id)
            child_status = _status(child)
            if child_status in {
                "EXECUTION_REPORT_STATUS_FILL",
                "EXECUTION_REPORT_STATUS_PARTIALLYFILL",
            }:
                working_state = _append_exit_component(
                    working_state,
                    payload=child,
                    source=role or "PROTECTIVE_CHILD",
                    order_id=child_id,
                )
            child_statuses.append(
                {
                    "exchange_order_id": child_id,
                    "status": child_status,
                }
            )

        terminal = {
            "EXECUTION_REPORT_STATUS_FILL",
            "EXECUTION_REPORT_STATUS_REJECTED",
            "EXECUTION_REPORT_STATUS_CANCELLED",
        }
        if any(
            item["status"] not in terminal
            for item in child_statuses
        ):
            out = dict(working_state)
            out.update(
                {
                    "updated_at": now.isoformat(),
                    "pending_children": refreshed_children,
                    "pending_child_statuses": child_statuses,
                    "remaining_lots": position_lots,
                }
            )
            return out

        active_items = _known_protection_items(
            client,
            state,
            status="STOP_ORDER_STATUS_ACTIVE",
        )
        if any(item is not None for item in active_items.values()):
            out = dict(state)
            out.update(
                {
                    "status": "PROTECTION_CANCEL_PENDING",
                    "updated_at": now.isoformat(),
                    "pending_child_statuses": child_statuses,
                }
            )
            return out

        return _force_exit_remaining(
            client=client,
            state=working_state,
            reason=str(
                state.get("close_reason")
                or "PROTECTIVE_CHILD_TERMINAL_WITH_REMAINDER"
            ),
            closed_status=str(
                state.get("pending_closed_status")
                or "CLOSED_FORCE_EXIT"
            ),
        )

    if current_status == "PROTECTION_TRIGGERED":
        child_id = str(state.get("triggered_exchange_order_id") or "")
        if not child_id:
            return _cancel_verified_or_wait(
                client=client,
                state=state,
                reason="TRIGGERED_PROTECTION_WITHOUT_CHILD_ORDER",
                closed_status=str(
                    state.get("triggered_closed_status")
                    or "CLOSED_FORCE_EXIT"
                ),
            )

        child = client.get_order_state(child_id)
        child_status = _status(child)
        if child_status == "EXECUTION_REPORT_STATUS_FILL":
            state_with_child = _append_exit_component(
                state,
                payload=child,
                source=str(state.get("triggered_by") or "PROTECTIVE_CHILD"),
                order_id=child_id,
            )
            return _cancel_verified_or_wait(
                client=client,
                state=state_with_child,
                reason="PROTECTIVE_CHILD_FILLED",
                closed_status=str(
                    state.get("triggered_closed_status")
                    or "CLOSED_FORCE_EXIT"
                ),
            )

        if child_status in {
            "EXECUTION_REPORT_STATUS_PARTIALLYFILL",
            "EXECUTION_REPORT_STATUS_REJECTED",
            "EXECUTION_REPORT_STATUS_CANCELLED",
        }:
            state_with_child = _append_exit_component(
                state,
                payload=child,
                source=str(state.get("triggered_by") or "PROTECTIVE_CHILD"),
                order_id=child_id,
            )
            return _cancel_verified_or_wait(
                client=client,
                state=state_with_child,
                reason=f"PROTECTIVE_CHILD_{child_status}",
                closed_status=str(
                    state.get("triggered_closed_status")
                    or "CLOSED_FORCE_EXIT"
                ),
            )

        out = dict(state)
        out.update(
            {
                "updated_at": now.isoformat(),
                "triggered_child_status": child_status,
                "remaining_lots": position_lots,
            }
        )
        return out

    if position_lots <= 0:
        return _cancel_verified_or_wait(
            client=client,
            state=state,
            reason=str(state.get("close_reason") or "POSITION_GONE"),
            closed_status=str(
                state.get("pending_closed_status")
                or state.get("triggered_closed_status")
                or "CLOSED_POSITION_GONE"
            ),
        )

    if current_status not in {"PROTECTED", "PROTECTION_SETUP_FAILED"}:
        return _cancel_verified_or_wait(
            client=client,
            state=state,
            reason=f"UNEXPECTED_LIFECYCLE_STATE_{current_status}",
            closed_status="CLOSED_FORCE_EXIT",
        )

    time_stop = parse_iso_utc(str(state["time_stop"]))
    if now >= time_stop:
        return _cancel_verified_or_wait(
            client=client,
            state=state,
            reason="TIME_STOP",
            closed_status="CLOSED_TIME_STOP",
        )

    stop_orders = client.get_stop_orders(status="STOP_ORDER_STATUS_ALL")
    stop_item = _find_stop(
        stop_orders,
        str(state.get("stop_order_id") or ""),
    )
    take_item = _find_stop(
        stop_orders,
        str(state.get("take_order_id") or ""),
    )

    if stop_item is None or take_item is None:
        return _cancel_verified_or_wait(
            client=client,
            state=state,
            reason="PROTECTIVE_ORDER_MISSING",
            closed_status="CLOSED_FORCE_EXIT",
        )

    stop_triggered = _triggered(stop_item)
    take_triggered = _triggered(take_item)

    if not stop_triggered and not take_triggered:
        bad_statuses = {
            "STOP_ORDER_STATUS_CANCELED",
            "STOP_ORDER_STATUS_EXPIRED",
        }
        if (
            str(stop_item.get("status") or "").upper() in bad_statuses
            or str(take_item.get("status") or "").upper() in bad_statuses
        ):
            return _cancel_verified_or_wait(
                client=client,
                state=state,
                reason="PROTECTIVE_ORDER_INACTIVE",
                closed_status="CLOSED_FORCE_EXIT",
            )
        # Healthy protection requires no persisted state transition.
        # Returning the original object avoids a new journal email every
        # monitor cycle.
        return state

    if stop_triggered and take_triggered:
        return _cancel_verified_or_wait(
            client=client,
            state=state,
            reason="BOTH_PROTECTIVE_ORDERS_TRIGGERED",
            closed_status="CLOSED_FORCE_EXIT",
        )

    if stop_triggered:
        triggered = stop_item
        sibling_id = str(state.get("take_order_id") or "")
        closed_status = "CLOSED_STOP_LOSS"
        trigger_name = "STOP_LOSS"
    else:
        triggered = take_item
        sibling_id = str(state.get("stop_order_id") or "")
        closed_status = "CLOSED_TAKE_PROFIT"
        trigger_name = "TAKE_PROFIT"

    _cancel_if_present(client, sibling_id)
    child_id = _exchange_order_id(triggered or {})
    if not child_id:
        out = dict(state)
        out.update(
            {
                "status": "PROTECTIVE_CHILD_PENDING",
                "close_reason": (
                    f"{trigger_name}_TRIGGERED_WITHOUT_CHILD"
                ),
                "pending_closed_status": closed_status,
                "pending_children": [
                    {
                        "role": trigger_name,
                        "stop_order_id": _stop_order_id(triggered or {}),
                        "exchange_order_id": "",
                    }
                ],
                "updated_at": now.isoformat(),
            }
        )
        return out

    child = client.get_order_state(child_id)
    child_status = _status(child)
    if child_status == "EXECUTION_REPORT_STATUS_FILL":
        filled_state = _append_exit_component(
            state,
            payload=child,
            source=trigger_name,
            order_id=child_id,
        )
        filled_state.update(
            {
                "triggered_by": trigger_name,
                "triggered_exchange_order_id": child_id,
                "triggered_closed_status": closed_status,
                "triggered_child_status": child_status,
            }
        )
        return _cancel_verified_or_wait(
            client=client,
            state=filled_state,
            reason=f"{trigger_name}_FILLED",
            closed_status=closed_status,
        )

    if child_status in {
        "EXECUTION_REPORT_STATUS_PARTIALLYFILL",
        "EXECUTION_REPORT_STATUS_REJECTED",
        "EXECUTION_REPORT_STATUS_CANCELLED",
    }:
        state_with_child = _append_exit_component(
            state,
            payload=child,
            source=trigger_name,
            order_id=child_id,
        )
        return _cancel_verified_or_wait(
            client=client,
            state=state_with_child,
            reason=f"{trigger_name}_{child_status}",
            closed_status=closed_status,
        )

    out = dict(state)
    out.update(
        {
            "status": "PROTECTION_TRIGGERED",
            "updated_at": now.isoformat(),
            "remaining_lots": position_lots,
            "triggered_by": trigger_name,
            "triggered_exchange_order_id": child_id,
            "triggered_closed_status": closed_status,
            "triggered_child_status": child_status,
        }
    )
    return out
