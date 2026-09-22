import pytest

from trading_copilot.domain.execution import (
    AddProjectionRequest,
    ExecutionPlanRequest,
    PositionLeg,
    TargetLevel,
)
from trading_copilot.domain.models import OpenRiskPosition, Side
from trading_copilot.services.execution import project_add, summarize_execution_plan


def test_current_bnb_eth_example_uses_one_correlated_risk_bucket():
    plan = ExecutionPlanRequest(
        symbol="BNBUSDT",
        side=Side.SHORT,
        account_equity=4898.0,
        max_group_risk_pct=0.02,
        hard_stop=812.0,
        current_legs=[PositionLeg(entry=786.40, quantity=5.54)],
        other_correlated_positions=[
            OpenRiskPosition(
                symbol="ETHUSDT",
                side=Side.SHORT,
                quantity=0.80,
                entry=2740.20,
                stop=2820.0,
                correlation_group="crypto_beta",
            )
        ],
    )

    result = summarize_execution_plan(plan)

    assert result["current_trade_risk_usdt"] == pytest.approx(141.824)
    assert result["other_group_risk_usdt"] == pytest.approx(63.84)
    assert result["current_group_risk_usdt"] == pytest.approx(205.664)
    assert result["current_group_risk_pct"] == pytest.approx(205.664 / 4898.0)
    assert result["within_group_risk_budget"] is False
    assert result["over_budget_usdt"] == pytest.approx(205.664 - 97.96)


def test_short_target_ladder_reports_r_multiples():
    plan = ExecutionPlanRequest(
        symbol="BNBUSDT",
        side=Side.SHORT,
        account_equity=10_000,
        hard_stop=812.0,
        current_legs=[PositionLeg(entry=786.40, quantity=5.54)],
        targets=[
            TargetLevel(price=776.0, close_fraction=0.20, label="TP1"),
            TargetLevel(price=769.0, close_fraction=0.30, label="TP2"),
            TargetLevel(price=759.0, close_fraction=0.30, label="TP3"),
        ],
    )

    result = summarize_execution_plan(plan)["target_analysis"]

    assert result["targets"][0]["r_multiple"] == pytest.approx(10.4 / 25.6)
    assert result["targets"][1]["r_multiple"] == pytest.approx(17.4 / 25.6)
    assert result["targets"][2]["r_multiple"] == pytest.approx(27.4 / 25.6)
    assert result["allocated_fraction"] == pytest.approx(0.80)
    assert result["unallocated_fraction"] == pytest.approx(0.20)


def test_multiple_fills_use_weighted_entry_and_exact_structural_risk():
    plan = ExecutionPlanRequest(
        symbol="TESTUSDT",
        side=Side.SHORT,
        account_equity=10_000,
        hard_stop=120.0,
        current_legs=[
            PositionLeg(entry=100.0, quantity=1.0),
            PositionLeg(entry=110.0, quantity=1.0),
        ],
    )

    result = summarize_execution_plan(plan)

    assert result["weighted_average_entry"] == pytest.approx(105.0)
    assert result["current_trade_risk_usdt"] == pytest.approx(30.0)


def test_long_plan_target_ladder_uses_long_reward_direction():
    plan = ExecutionPlanRequest(
        symbol="TESTUSDT",
        side=Side.LONG,
        account_equity=10_000,
        hard_stop=95.0,
        current_legs=[PositionLeg(entry=100.0, quantity=1.0)],
        targets=[
            TargetLevel(price=110.0, close_fraction=0.5),
            TargetLevel(price=115.0, close_fraction=0.5),
        ],
    )

    result = summarize_execution_plan(plan)["target_analysis"]

    assert result["targets"][0]["r_multiple"] == pytest.approx(2.0)
    assert result["targets"][1]["r_multiple"] == pytest.approx(3.0)
    assert result["weighted_target_r"] == pytest.approx(2.5)


def test_current_oversized_bnb_plan_blocks_any_add_under_two_percent_budget():
    plan = ExecutionPlanRequest(
        symbol="BNBUSDT",
        side=Side.SHORT,
        account_equity=4898.0,
        max_group_risk_pct=0.02,
        hard_stop=812.0,
        current_legs=[PositionLeg(entry=786.40, quantity=5.54)],
    )
    request = AddProjectionRequest(
        plan=plan,
        add_price=800.0,
        add_quantity=10.68,
        confirmation_label="retest rejects 800",
        confirmation_met=True,
    )

    result = project_add(request)

    assert result["risk_policy"]["max_add_quantity_at_price"] == 0.0
    assert result["risk_policy"]["allowed_by_risk"] is False
    assert result["add_allowed"] is False
    assert "projected correlation-group risk exceeds configured budget" in result["blockers"]


def test_affordable_add_is_still_blocked_without_predefined_confirmation():
    plan = ExecutionPlanRequest(
        symbol="BNBUSDT",
        side=Side.SHORT,
        account_equity=4898.0,
        max_group_risk_pct=0.02,
        hard_stop=812.0,
        current_legs=[PositionLeg(entry=786.40, quantity=1.0)],
    )
    request = AddProjectionRequest(
        plan=plan,
        add_price=800.0,
        add_quantity=2.0,
        confirmation_label="800 rejection confirmed",
        confirmation_met=False,
    )

    result = project_add(request)

    assert result["risk_policy"]["allowed_by_risk"] is True
    assert result["allowed_by_confirmation"] is False
    assert result["add_allowed"] is False
    assert result["risk_policy"]["max_add_quantity_at_price"] == pytest.approx(6.03)


def test_affordable_confirmed_add_is_allowed_and_updates_weighted_entry():
    plan = ExecutionPlanRequest(
        symbol="BNBUSDT",
        side=Side.SHORT,
        account_equity=4898.0,
        max_group_risk_pct=0.02,
        hard_stop=812.0,
        current_legs=[PositionLeg(entry=786.40, quantity=1.0)],
    )
    request = AddProjectionRequest(
        plan=plan,
        add_price=800.0,
        add_quantity=2.0,
        confirmation_label="800 rejection confirmed",
        confirmation_met=True,
    )

    result = project_add(request)

    assert result["add_allowed"] is True
    assert result["projected_position"]["quantity"] == pytest.approx(3.0)
    assert result["projected_position"]["weighted_average_entry"] == pytest.approx(
        (786.4 + 800.0 * 2) / 3
    )


def test_target_allocations_cannot_exceed_full_position():
    with pytest.raises(ValueError):
        ExecutionPlanRequest(
            symbol="ETHUSDT",
            side=Side.SHORT,
            account_equity=5000,
            hard_stop=2820,
            current_legs=[PositionLeg(entry=2740.20, quantity=0.8)],
            targets=[
                TargetLevel(price=2700, close_fraction=0.6),
                TargetLevel(price=2650, close_fraction=0.6),
            ],
        )
