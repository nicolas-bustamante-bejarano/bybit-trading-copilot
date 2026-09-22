from trading_copilot.domain.account import NormalizedFill, NormalizedPosition
from trading_copilot.domain.lifecycle import LifecycleAction
from trading_copilot.domain.models import Side
from trading_copilot.services.lifecycle import reconstruct_open_position_lifecycle


def _position(quantity: float = 5.54) -> NormalizedPosition:
    return NormalizedPosition(
        symbol="BNBUSDT",
        side=Side.SHORT,
        quantity=quantity,
        average_entry=786.40,
        stop_loss=812.0,
        protected=True,
        structural_risk_usdt=quantity * (812.0 - 786.40),
    )


def _fill(exec_id: str, side: Side, qty: float, price: float, ts: int) -> NormalizedFill:
    return NormalizedFill(
        exec_id=exec_id,
        symbol="BNBUSDT",
        side=side,
        price=price,
        quantity=qty,
        exec_time_ms=ts,
    )


def test_reconstructs_entry_and_add_from_current_position() -> None:
    fills = [
        _fill("1", Side.SHORT, 2.0, 786.40, 1000),
        _fill("2", Side.SHORT, 3.54, 800.0, 2000),
    ]

    lifecycle = reconstruct_open_position_lifecycle(_position(), fills)

    assert lifecycle.history_complete is True
    assert lifecycle.matches_current_position is True
    assert [event.action for event in lifecycle.events] == [
        LifecycleAction.ENTRY,
        LifecycleAction.ADD,
    ]
    assert lifecycle.events[-1].signed_position_after == -5.54


def test_reconstructs_partial_reduction() -> None:
    fills = [
        _fill("1", Side.SHORT, 5.54, 786.40, 1000),
        _fill("2", Side.LONG, 1.0, 776.0, 2000),
    ]

    lifecycle = reconstruct_open_position_lifecycle(_position(quantity=4.54), fills)

    assert lifecycle.history_complete is True
    assert [event.action for event in lifecycle.events] == [
        LifecycleAction.ENTRY,
        LifecycleAction.REDUCE,
    ]
    assert lifecycle.events[-1].signed_position_after == -4.54


def test_marks_history_incomplete_when_opening_fill_is_missing() -> None:
    fills = [_fill("2", Side.SHORT, 3.54, 800.0, 2000)]

    lifecycle = reconstruct_open_position_lifecycle(_position(), fills)

    assert lifecycle.history_complete is False
    assert lifecycle.matches_current_position is True
    assert lifecycle.selected_fill_count == 1
    assert lifecycle.events[0].action == LifecycleAction.ADD
