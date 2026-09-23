from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_copilot.domain.models import Regime, SetupState
from trading_copilot.domain.scanner import ScannerDataStatus, ScannerStatus
from trading_copilot.persistence.models import (
    FibDefinitionRow,
    RangeDefinitionRow,
    TradePlanRow,
    WatchedSetupRow,
)
from trading_copilot.services import scanner_playbook

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def plan(
    plan_id: str,
    *,
    symbol: str = "BTCUSDT",
    side: str = "LONG",
    setup_type: str = "TREND_PULLBACK_LONG",
) -> TradePlanRow:
    return TradePlanRow(
        id=plan_id,
        symbol=symbol,
        side=side,
        setup_type=setup_type,
        thesis="test",
        hard_invalidation=Decimal(90),
        max_risk_percent=Decimal("0.01"),
    )


def watched(setup_type="TREND_PULLBACK_LONG", linked=None) -> WatchedSetupRow:
    return WatchedSetupRow(
        id="watch-1",
        symbol="BTCUSDT",
        setup_type=setup_type,
        status="WATCH",
        trade_plan_id=linked,
    )


def fib(plan_id: str) -> FibDefinitionRow:
    return FibDefinitionRow(
        id=f"fib-{plan_id}",
        trade_plan_id=plan_id,
        symbol="BTCUSDT",
        direction="LONG",
        swing_low=Decimal(100),
        swing_high=Decimal(200),
    )


def range_definition(plan_id: str) -> RangeDefinitionRow:
    return RangeDefinitionRow(
        id=f"range-{plan_id}",
        trade_plan_id=plan_id,
        symbol="BTCUSDT",
        range_low=Decimal(100),
        range_high=Decimal(120),
    )


def evaluate_trend(*, price=190, regime_4h=Regime.UPTREND, plans=None, definitions=None, **kwargs):
    compatible = plan("plan-1")
    return scanner_playbook.evaluate_scanner_playbook(
        watched=kwargs.pop("watched", watched()),
        plans=[compatible] if plans is None else plans,
        definitions=[fib(compatible.id)] if definitions is None else definitions,
        price=price,
        regime_1h=kwargs.pop("regime_1h", Regime.UPTREND),
        regime_4h=regime_4h,
        evaluated_at=NOW,
        **kwargs,
    )


def evaluate_range(*, price=110, regime_4h=Regime.RANGE, plans=None, definitions=None, **kwargs):
    compatible = plan("plan-1", setup_type="RANGE_LONG")
    return scanner_playbook.evaluate_scanner_playbook(
        watched=kwargs.pop("watched", watched("RANGE_LONG")),
        plans=[compatible] if plans is None else plans,
        definitions=[range_definition(compatible.id)] if definitions is None else definitions,
        price=price,
        regime_1h=kwargs.pop("regime_1h", Regime.RANGE),
        regime_4h=regime_4h,
        evaluated_at=NOW,
        **kwargs,
    )


def test_trend_wrong_4h_regime_is_ignored():
    assert evaluate_trend(regime_4h=Regime.DOWNTREND).status == ScannerStatus.IGNORE


def test_trend_valid_context_far_from_fib_is_watch():
    assert evaluate_trend().status == ScannerStatus.WATCH


def test_trend_approaching_fib_is_approaching_location():
    result = evaluate_trend(price=162, approach_tolerance_bps=100)

    assert result.status == ScannerStatus.APPROACHING_LOCATION


def test_trend_inside_actionable_fib_is_at_location():
    result = evaluate_trend(price=130, stoch_k=60, stoch_d=55)

    assert result.status == ScannerStatus.AT_LOCATION


def test_trend_supportive_partial_evidence_is_reaction_developing():
    result = evaluate_trend(price=130, stoch_k=15, stoch_d=20)

    assert result.status == ScannerStatus.REACTION_DEVELOPING


def test_trend_waiting_for_trigger_is_trigger_armed():
    result = evaluate_trend(
        price=130,
        stoch_k=15,
        stoch_d=20,
        reaction_state="sell_absorption",
    )

    assert result.status == ScannerStatus.TRIGGER_ARMED
    assert result.next_conditions == ["await lower-timeframe trigger engine"]


@pytest.mark.parametrize(
    ("plans", "definitions", "item", "reason"),
    [
        ([], [], watched(), "STRUCTURE_REQUIRED"),
        (
            [plan("plan-1"), plan("plan-2")],
            [fib("plan-1"), fib("plan-2")],
            watched(),
            "AMBIGUOUS_STRUCTURE",
        ),
        (
            [plan("plan-1", symbol="ETHUSDT")],
            [fib("plan-1")],
            watched(linked="plan-1"),
            "INCOMPATIBLE_LINKED_PLAN",
        ),
    ],
)
def test_trend_structural_blockers_cannot_exceed_watch(plans, definitions, item, reason):
    result = evaluate_trend(
        price=130,
        plans=plans,
        definitions=definitions,
        watched=item,
        stoch_k=15,
        reaction_state="sell_absorption",
        data_status=ScannerDataStatus.PARTIAL,
    )

    assert result.status == ScannerStatus.WATCH
    assert result.location == {}
    assert result.blocking_reasons == [reason]
    assert result.data_status == ScannerDataStatus.PARTIAL


def test_adapter_always_supplies_trigger_confirmed_false(monkeypatch):
    captured = {}
    real_evaluator = scanner_playbook.evaluate_playbook

    def capture(request):
        captured["trigger_confirmed"] = request.trigger_confirmed
        return real_evaluator(request)

    monkeypatch.setattr(scanner_playbook, "evaluate_playbook", capture)

    evaluate_trend(price=130)

    assert captured == {"trigger_confirmed": False}


def test_range_wrong_4h_regime_is_ignored():
    assert evaluate_range(regime_4h=Regime.UPTREND).status == ScannerStatus.IGNORE


def test_range_mid_range_is_watch():
    assert evaluate_range().status == ScannerStatus.WATCH


def test_range_approaching_extreme_is_approaching_location():
    assert evaluate_range(price=105).status == ScannerStatus.APPROACHING_LOCATION


def test_range_at_extreme_is_at_location():
    assert evaluate_range(price=103, stoch_k=50, stoch_d=55).status == ScannerStatus.AT_LOCATION


def test_range_supportive_partial_evidence_is_reaction_developing():
    result = evaluate_range(price=103, stoch_k=15, stoch_d=20)

    assert result.status == ScannerStatus.REACTION_DEVELOPING


def test_range_waiting_for_trigger_is_trigger_armed():
    result = evaluate_range(
        price=103,
        stoch_k=15,
        stoch_d=20,
        reaction_state="sell_absorption",
    )

    assert result.status == ScannerStatus.TRIGGER_ARMED


@pytest.mark.parametrize(
    ("plans", "definitions", "item", "reason"),
    [
        ([], [], watched("RANGE_LONG"), "STRUCTURE_REQUIRED"),
        (
            [
                plan("plan-1", setup_type="RANGE_LONG"),
                plan("plan-2", setup_type="RANGE_LONG"),
            ],
            [range_definition("plan-1"), range_definition("plan-2")],
            watched("RANGE_LONG"),
            "AMBIGUOUS_STRUCTURE",
        ),
        (
            [plan("plan-1", symbol="ETHUSDT", setup_type="RANGE_LONG")],
            [range_definition("plan-1")],
            watched("RANGE_LONG", linked="plan-1"),
            "INCOMPATIBLE_LINKED_PLAN",
        ),
    ],
)
def test_range_structural_blockers_cannot_exceed_watch(plans, definitions, item, reason):
    result = evaluate_range(
        price=103,
        plans=plans,
        definitions=definitions,
        watched=item,
        stoch_k=15,
        reaction_state="sell_absorption",
    )

    assert result.status == ScannerStatus.WATCH
    assert result.location == {}
    assert result.blocking_reasons == [reason]


@pytest.mark.parametrize("unexpected", [SetupState.TRIGGERED, SetupState.READY])
def test_unreachable_execution_states_are_clamped(monkeypatch, unexpected):
    def impossible_result(request):
        return {
            "state": unexpected,
            "location": {},
            "momentum": {},
            "reaction_state": None,
            "conditions": [],
            "missing_required_conditions": [],
        }

    monkeypatch.setattr(scanner_playbook, "evaluate_playbook", impossible_result)

    result = evaluate_trend()

    assert result.status == ScannerStatus.TRIGGER_ARMED
    assert result.blocking_reasons == [f"UNEXPECTED_PLAYBOOK_STATE:{unexpected.value.upper()}"]


@pytest.mark.parametrize(
    ("setup_type", "plan_side", "plan_setup", "price", "regime"),
    [
        ("TREND_PULLBACK_SHORT", "SHORT", "TREND_PULLBACK_SHORT", 170, Regime.DOWNTREND),
        ("RANGE_SHORT", "SHORT", "RANGE_SHORT", 118, Regime.RANGE),
    ],
)
def test_short_scanner_playbooks_are_adapted(setup_type, plan_side, plan_setup, price, regime):
    compatible = plan("plan-1", side=plan_side, setup_type=plan_setup)
    definition = fib(compatible.id) if "TREND" in setup_type else range_definition(compatible.id)
    if isinstance(definition, FibDefinitionRow):
        definition.direction = plan_side

    result = scanner_playbook.evaluate_scanner_playbook(
        watched=watched(setup_type),
        plans=[compatible],
        definitions=[definition],
        price=price,
        regime_1h=regime,
        regime_4h=regime,
        evaluated_at=NOW,
    )

    assert result.side == "SHORT"
    assert result.status == ScannerStatus.AT_LOCATION
