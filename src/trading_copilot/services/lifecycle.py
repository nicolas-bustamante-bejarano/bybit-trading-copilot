from __future__ import annotations

from math import isclose

from trading_copilot.domain.account import NormalizedFill, NormalizedPosition
from trading_copilot.domain.lifecycle import LifecycleAction, LifecycleEvent, PositionLifecycle
from trading_copilot.domain.models import Side

TOLERANCE = 1e-9


def _signed_quantity(side: Side, quantity: float) -> float:
    return quantity if side == Side.LONG else -quantity


def _same_direction(left: float, right: float) -> bool:
    return left * right > 0


def _classify_transition(before: float, after: float) -> LifecycleAction:
    if isclose(before, 0.0, abs_tol=TOLERANCE) and not isclose(
        after, 0.0, abs_tol=TOLERANCE
    ):
        return LifecycleAction.ENTRY
    if not isclose(before, 0.0, abs_tol=TOLERANCE) and isclose(
        after, 0.0, abs_tol=TOLERANCE
    ):
        return LifecycleAction.EXIT
    if before * after < 0:
        return LifecycleAction.FLIP
    if _same_direction(before, after):
        if abs(after) > abs(before) + TOLERANCE:
            return LifecycleAction.ADD
        if abs(after) < abs(before) - TOLERANCE:
            return LifecycleAction.REDUCE
    return LifecycleAction.UNKNOWN


def reconstruct_open_position_lifecycle(
    position: NormalizedPosition,
    fills: list[NormalizedFill],
) -> PositionLifecycle:
    symbol = position.symbol.upper()
    relevant = [fill for fill in fills if fill.symbol.upper() == symbol]
    relevant.sort(key=lambda fill: fill.exec_time_ms or 0, reverse=True)

    target_signed = _signed_quantity(position.side, position.quantity)
    cursor = target_signed
    selected_reverse: list[NormalizedFill] = []
    found_flat_origin = False

    for fill in relevant:
        signed_fill = _signed_quantity(fill.side, fill.quantity)
        previous = cursor - signed_fill
        selected_reverse.append(fill)

        if isclose(previous, 0.0, abs_tol=TOLERANCE):
            cursor = 0.0
            found_flat_origin = True
            break

        if cursor * previous < 0:
            cursor = previous
            break

        cursor = previous

    selected = list(reversed(selected_reverse))
    running = cursor
    events: list[LifecycleEvent] = []

    for fill in selected:
        before = running
        running += _signed_quantity(fill.side, fill.quantity)
        events.append(
            LifecycleEvent(
                exec_id=fill.exec_id,
                order_id=fill.order_id,
                timestamp_ms=fill.exec_time_ms,
                side=fill.side,
                price=fill.price,
                quantity=fill.quantity,
                action=_classify_transition(before, running),
                signed_position_before=before,
                signed_position_after=running,
            )
        )

    matches_current = isclose(running, target_signed, rel_tol=0.0, abs_tol=1e-8)
    history_complete = found_flat_origin and matches_current
    opening_time = events[0].timestamp_ms if history_complete and events else None

    return PositionLifecycle(
        symbol=symbol,
        current_side=position.side,
        current_quantity=position.quantity,
        current_average_entry=position.average_entry,
        history_complete=history_complete,
        matches_current_position=matches_current,
        source_fill_count=len(relevant),
        selected_fill_count=len(selected),
        opening_time_ms=opening_time,
        events=events,
    )
