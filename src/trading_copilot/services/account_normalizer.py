from __future__ import annotations

from typing import Any

from trading_copilot.domain.account import (
    NormalizedAccount,
    NormalizedFill,
    NormalizedOrder,
    NormalizedPosition,
)
from trading_copilot.domain.models import Side


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _side(value: str) -> Side:
    return Side.LONG if value.lower() == "buy" else Side.SHORT


def _wallet_values(wallet_rows: list[dict[str, Any]]) -> tuple[float, float | None]:
    if not wallet_rows:
        return 0.0, None
    account = wallet_rows[0]
    equity = _float(account.get("totalEquity")) or 0.0
    available = _float(account.get("totalAvailableBalance"))
    return equity, available


def _position_map(raw_positions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in raw_positions:
        size = _float(row.get("size")) or 0.0
        avg_price = _float(row.get("avgPrice")) or 0.0
        symbol = str(row.get("symbol") or "").upper()
        if size <= 0 or avg_price <= 0 or not symbol:
            continue
        result[symbol] = row
    return result


def _classify_order(
    row: dict[str, Any],
    position: dict[str, Any] | None,
) -> str:
    reduce_only = bool(row.get("reduceOnly"))
    if not reduce_only:
        return "entry"
    if position is None:
        return "reduce"

    avg_entry = _float(position.get("avgPrice"))
    trigger = _float(row.get("triggerPrice"))
    order_price = _float(row.get("price"))
    candidate = trigger or order_price
    if avg_entry is None or candidate is None:
        return "reduce"

    position_side = str(position.get("side") or "")
    if position_side == "Buy":
        if candidate < avg_entry:
            return "stop"
        if candidate > avg_entry:
            return "take_profit"
    elif position_side == "Sell":
        if candidate > avg_entry:
            return "stop"
        if candidate < avg_entry:
            return "take_profit"
    return "reduce"


def _normalize_orders(
    raw_orders: list[dict[str, Any]],
    positions_by_symbol: dict[str, dict[str, Any]],
) -> list[NormalizedOrder]:
    result: list[NormalizedOrder] = []
    for row in raw_orders:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        qty = _float(row.get("leavesQty"))
        if qty is None:
            qty = _float(row.get("qty")) or 0.0
        side_raw = str(row.get("side") or "Buy")
        result.append(
            NormalizedOrder(
                order_id=row.get("orderId"),
                symbol=symbol,
                side=_side(side_raw),
                order_type=row.get("orderType"),
                quantity=max(0.0, qty),
                price=_float(row.get("price")),
                trigger_price=_float(row.get("triggerPrice")),
                reduce_only=bool(row.get("reduceOnly")),
                status=row.get("orderStatus"),
                purpose=_classify_order(row, positions_by_symbol.get(symbol)),
            )
        )
    return result


def _normalize_fills(raw_fills: list[dict[str, Any]]) -> list[NormalizedFill]:
    result: list[NormalizedFill] = []
    for row in raw_fills:
        symbol = str(row.get("symbol") or "").upper()
        price = _float(row.get("execPrice"))
        qty = _float(row.get("execQty"))
        if not symbol or price is None or qty is None or price <= 0 or qty <= 0:
            continue
        result.append(
            NormalizedFill(
                exec_id=row.get("execId"),
                order_id=row.get("orderId"),
                symbol=symbol,
                side=_side(str(row.get("side") or "Buy")),
                price=price,
                quantity=qty,
                exec_time_ms=int(row["execTime"]) if row.get("execTime") not in (None, "") else None,
                fee_usdt=_float(row.get("execFee")),
            )
        )
    return result


def _valid_stop(side: Side, entry: float, stop: float | None) -> bool:
    if stop is None:
        return False
    return stop < entry if side == Side.LONG else stop > entry


def _stop_from_orders(
    symbol: str,
    side: Side,
    entry: float,
    orders: list[NormalizedOrder],
) -> float | None:
    candidates = [
        order.trigger_price or order.price
        for order in orders
        if order.symbol == symbol and order.purpose == "stop"
    ]
    valid = [price for price in candidates if price is not None and _valid_stop(side, entry, price)]
    if not valid:
        return None
    return max(valid) if side == Side.LONG else min(valid)


def _take_profit_from_orders(
    symbol: str,
    side: Side,
    entry: float,
    orders: list[NormalizedOrder],
) -> float | None:
    candidates = [
        order.trigger_price or order.price
        for order in orders
        if order.symbol == symbol and order.purpose == "take_profit"
    ]
    if side == Side.LONG:
        valid = [price for price in candidates if price is not None and price > entry]
        return min(valid) if valid else None
    valid = [price for price in candidates if price is not None and price < entry]
    return max(valid) if valid else None


def normalize_account_snapshot(
    snapshot: dict[str, Any],
    *,
    max_group_risk_pct: float = 0.02,
) -> NormalizedAccount:
    raw_positions = snapshot.get("positions") or []
    raw_orders = snapshot.get("open_orders") or []
    raw_fills = snapshot.get("executions") or []
    raw_wallet = snapshot.get("wallet") or []

    positions_by_symbol = _position_map(raw_positions)
    orders = _normalize_orders(raw_orders, positions_by_symbol)
    fills = _normalize_fills(raw_fills)
    positions: list[NormalizedPosition] = []

    for symbol, row in positions_by_symbol.items():
        side = _side(str(row.get("side") or "Buy"))
        quantity = _float(row.get("size")) or 0.0
        entry = _float(row.get("avgPrice")) or 0.0

        explicit_stop = _float(row.get("stopLoss"))
        stop = explicit_stop if _valid_stop(side, entry, explicit_stop) else None
        if stop is None:
            stop = _stop_from_orders(symbol, side, entry, orders)

        explicit_tp = _float(row.get("takeProfit"))
        take_profit = explicit_tp
        if take_profit in (None, 0.0):
            take_profit = _take_profit_from_orders(symbol, side, entry, orders)

        risk = quantity * abs(stop - entry) if stop is not None else None
        positions.append(
            NormalizedPosition(
                symbol=symbol,
                side=side,
                quantity=quantity,
                average_entry=entry,
                mark_price=_float(row.get("markPrice")),
                leverage=_float(row.get("leverage")),
                liquidation_price=_float(row.get("liqPrice")),
                unrealized_pnl=_float(row.get("unrealisedPnl")),
                stop_loss=stop,
                take_profit=take_profit,
                structural_risk_usdt=risk,
                protected=stop is not None,
            )
        )

    equity, available = _wallet_values(raw_wallet)
    total_risk = sum(position.structural_risk_usdt or 0.0 for position in positions)
    total_risk_pct = total_risk / equity if equity > 0 else 0.0
    budget = equity * max_group_risk_pct
    unprotected = sorted(position.symbol for position in positions if not position.protected)

    return NormalizedAccount(
        equity_usdt=equity,
        available_balance_usdt=available,
        max_group_risk_pct=max_group_risk_pct,
        risk_budget_usdt=budget,
        total_structural_risk_usdt=total_risk,
        total_structural_risk_pct=total_risk_pct,
        within_risk_budget=(not unprotected and total_risk <= budget + 1e-9),
        unprotected_symbols=unprotected,
        positions=sorted(positions, key=lambda position: position.symbol),
        open_orders=orders,
        recent_fills=fills,
    )


def portfolio_live_view(account: NormalizedAccount) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for position in account.positions:
        group = groups.setdefault(
            position.correlation_group,
            {"risk_usdt": 0.0, "positions": [], "unprotected": []},
        )
        if position.structural_risk_usdt is not None:
            group["risk_usdt"] += position.structural_risk_usdt
        else:
            group["unprotected"].append(position.symbol)
        group["positions"].append(position.symbol)

    return {
        "equity_usdt": account.equity_usdt,
        "available_balance_usdt": account.available_balance_usdt,
        "risk_policy_pct": account.max_group_risk_pct,
        "risk_budget_usdt": account.risk_budget_usdt,
        "total_structural_risk_usdt": account.total_structural_risk_usdt,
        "total_structural_risk_pct": account.total_structural_risk_pct,
        "within_policy": account.within_risk_budget,
        "unprotected_symbols": account.unprotected_symbols,
        "correlation_groups": groups,
        "positions": [position.model_dump(mode="json") for position in account.positions],
    }
