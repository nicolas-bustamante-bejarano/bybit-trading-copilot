import pytest

from trading_copilot.domain.models import Regime, SetupState, Side
from trading_copilot.domain.playbook import (
    FibAnchors,
    PlaybookEvaluationRequest,
    PlaybookType,
    RangeBounds,
)
from trading_copilot.services.playbook import evaluate_playbook


def trend_request(**overrides) -> PlaybookEvaluationRequest:
    payload = {
        "symbol": "ETHUSDT",
        "playbook": PlaybookType.TREND_PULLBACK,
        "side": Side.LONG,
        "price": 130.0,
        "regime_1h": Regime.UPTREND,
        "regime_4h": Regime.UPTREND,
        "stoch_k": 25.0,
        "stoch_d": 20.0,
        "prev_stoch_k": 15.0,
        "prev_stoch_d": 18.0,
        "fib": FibAnchors(swing_low=100.0, swing_high=200.0),
        "reaction_state": "sell_absorption",
        "trigger_confirmed": False,
    }
    payload.update(overrides)
    return PlaybookEvaluationRequest(**payload)


def test_trend_pullback_ready_only_after_trigger():
    waiting = evaluate_playbook(trend_request())
    ready = evaluate_playbook(trend_request(trigger_confirmed=True))

    assert waiting["state"] == SetupState.WAITING_FOR_TRIGGER
    assert waiting["location"]["active_zone"] == "primary"
    assert waiting["ready_for_manual_execution"] is False
    assert ready["state"] == SetupState.READY
    assert ready["ready_for_manual_execution"] is True


def test_trend_pullback_rejects_wrong_higher_timeframe_regime():
    result = evaluate_playbook(trend_request(regime_4h=Regime.DOWNTREND))

    assert result["state"] == SetupState.NO_SETUP
    assert "4h_regime_aligned" in result["missing_required_conditions"]


def test_trend_pullback_can_be_approaching_location():
    result = evaluate_playbook(
        trend_request(
            price=138.5,
            reaction_state=None,
            stoch_k=60,
            stoch_d=55,
            prev_stoch_k=58,
            prev_stoch_d=54,
        )
    )

    assert result["state"] == SetupState.APPROACHING_LOCATION
    assert result["location"]["at_location"] is False
    assert result["location"]["approaching"] is True


def test_shallow_trend_pullback_requires_strong_timeframe_alignment():
    aligned = evaluate_playbook(trend_request(price=155.0))
    transition = evaluate_playbook(trend_request(price=155.0, regime_1h=Regime.TRANSITION))

    assert aligned["location"]["active_zone"] == "shallow"
    assert transition["location"]["active_zone"] is None


def test_short_trend_pullback_supports_inverse_momentum_and_reaction():
    result = evaluate_playbook(
        trend_request(
            side=Side.SHORT,
            price=170.0,
            regime_1h=Regime.DOWNTREND,
            regime_4h=Regime.DOWNTREND,
            stoch_k=75.0,
            stoch_d=80.0,
            prev_stoch_k=90.0,
            prev_stoch_d=85.0,
            reaction_state="buy_absorption",
            trigger_confirmed=True,
        )
    )

    assert result["state"] == SetupState.READY
    assert result["side"] == Side.SHORT
    assert result["momentum"]["exiting_overbought"] is True


def test_range_long_waits_for_trigger_at_lower_extreme():
    request = PlaybookEvaluationRequest(
        symbol="BNBUSDT",
        playbook=PlaybookType.RANGE_LONG,
        side=Side.LONG,
        price=103.0,
        regime_1h=Regime.RANGE,
        regime_4h=Regime.RANGE,
        stoch_k=18,
        stoch_d=21,
        range_bounds=RangeBounds(low=100, high=120),
        reaction_state="sell_absorption",
    )

    result = evaluate_playbook(request)

    assert result["state"] == SetupState.WAITING_FOR_TRIGGER
    assert result["location"]["range_position"] == pytest.approx(0.15)


def test_range_middle_is_context_only_not_a_trade_location():
    request = PlaybookEvaluationRequest(
        symbol="BNBUSDT",
        playbook=PlaybookType.RANGE_LONG,
        side=Side.LONG,
        price=110.0,
        regime_1h=Regime.RANGE,
        regime_4h=Regime.RANGE,
        stoch_k=10,
        stoch_d=12,
        range_bounds=RangeBounds(low=100, high=120),
        reaction_state="sell_absorption",
        trigger_confirmed=True,
    )

    result = evaluate_playbook(request)

    assert result["state"] == SetupState.CONTEXT_VALID
    assert result["ready_for_manual_execution"] is False


def test_range_short_ready_at_upper_extreme():
    request = PlaybookEvaluationRequest(
        symbol="BNBUSDT",
        playbook=PlaybookType.RANGE_SHORT,
        side=Side.SHORT,
        price=118.0,
        regime_1h=Regime.RANGE,
        regime_4h=Regime.RANGE,
        stoch_k=82,
        stoch_d=85,
        range_bounds=RangeBounds(low=100, high=120),
        reaction_state="buy_absorption",
        trigger_confirmed=True,
    )

    result = evaluate_playbook(request)

    assert result["state"] == SetupState.READY
    assert result["location"]["range_position"] == pytest.approx(0.9)


def test_manual_invalidation_has_priority():
    result = evaluate_playbook(trend_request(invalidation_breached=True, trigger_confirmed=True))

    assert result["state"] == SetupState.INVALIDATED
    assert result["ready_for_manual_execution"] is False


def test_range_long_model_rejects_short_side():
    with pytest.raises(ValueError):
        PlaybookEvaluationRequest(
            symbol="ETHUSDT",
            playbook=PlaybookType.RANGE_LONG,
            side=Side.SHORT,
            price=100,
            regime_1h=Regime.RANGE,
            regime_4h=Regime.RANGE,
            range_bounds=RangeBounds(low=90, high=110),
        )
