from __future__ import annotations

from typing import Any

from trading_copilot.domain.execution import AddProjectionRequest, ExecutionPlanRequest, PositionLeg
from trading_copilot.domain.models import Side
from trading_copilot.services.risk import position_risk, risk_per_unit


def weighted_average_entry(legs: list[PositionLeg]) -> float | None:
    total_quantity = sum(leg.quantity for leg in legs)
    if total_quantity <= 0:
        return None
    return sum(leg.entry * leg.quantity for leg in legs) / total_quantity


def _current_trade_risk(plan: ExecutionPlanRequest) -> float:
    return sum(
        leg.quantity * risk_per_unit(plan.side, leg.entry, plan.hard_stop)
        for leg in plan.current_legs
    )


def _other_group_risk(plan: ExecutionPlanRequest) -> float:
    return sum(
        position_risk(position)
        for position in plan.other_correlated_positions
        if position.correlation_group == plan.correlation_group
    )


def _reward_per_unit(side: Side, entry: float, target: float) -> float:
    if side == Side.LONG:
        return target - entry
    return entry - target


def _target_analysis(
    plan: ExecutionPlanRequest,
    average_entry: float | None,
) -> dict[str, Any]:
    if average_entry is None:
        return {
            "weighted_average_entry": None,
            "targets": [],
            "allocated_fraction": 0.0,
            "unallocated_fraction": 1.0,
            "weighted_target_r": None,
        }

    unit_risk = risk_per_unit(plan.side, average_entry, plan.hard_stop)
    rows: list[dict[str, Any]] = []
    allocated = 0.0
    weighted_r = 0.0

    for target in plan.targets:
        reward = _reward_per_unit(plan.side, average_entry, target.price)
        r_multiple = reward / unit_risk
        allocated += target.close_fraction
        weighted_r += target.close_fraction * r_multiple
        rows.append(
            {
                "label": target.label,
                "price": target.price,
                "close_fraction": target.close_fraction,
                "reward_per_unit": reward,
                "r_multiple": r_multiple,
                "favorable": reward > 0,
            }
        )

    return {
        "weighted_average_entry": average_entry,
        "risk_per_unit_from_average": unit_risk,
        "targets": rows,
        "allocated_fraction": allocated,
        "unallocated_fraction": max(0.0, 1.0 - allocated),
        "weighted_target_r": weighted_r if rows else None,
    }


def summarize_execution_plan(plan: ExecutionPlanRequest) -> dict[str, Any]:
    current_quantity = sum(leg.quantity for leg in plan.current_legs)
    average_entry = weighted_average_entry(plan.current_legs)
    trade_risk = _current_trade_risk(plan)
    other_risk = _other_group_risk(plan)
    group_risk = trade_risk + other_risk
    risk_budget = plan.account_equity * plan.max_group_risk_pct
    remaining = risk_budget - group_risk

    return {
        "symbol": plan.symbol.upper(),
        "side": plan.side,
        "stage": plan.stage,
        "correlation_group": plan.correlation_group,
        "account_equity": plan.account_equity,
        "max_group_risk_pct": plan.max_group_risk_pct,
        "group_risk_budget_usdt": risk_budget,
        "current_quantity": current_quantity,
        "weighted_average_entry": average_entry,
        "hard_stop": plan.hard_stop,
        "thesis_warning_level": plan.thesis_warning_level,
        "current_trade_risk_usdt": trade_risk,
        "other_group_risk_usdt": other_risk,
        "current_group_risk_usdt": group_risk,
        "current_group_risk_pct": group_risk / plan.account_equity,
        "remaining_group_risk_usdt": max(0.0, remaining),
        "over_budget_usdt": max(0.0, -remaining),
        "within_group_risk_budget": group_risk <= risk_budget + 1e-9,
        "target_analysis": _target_analysis(plan, average_entry),
    }


def project_add(request: AddProjectionRequest) -> dict[str, Any]:
    plan = request.plan
    summary = summarize_execution_plan(plan)
    remaining_risk = summary["remaining_group_risk_usdt"]
    add_risk_per_unit = risk_per_unit(plan.side, request.add_price, plan.hard_stop)
    add_risk = request.add_quantity * add_risk_per_unit
    max_add_quantity = remaining_risk / add_risk_per_unit if add_risk_per_unit > 0 else 0.0
    projected_group_risk = summary["current_group_risk_usdt"] + add_risk
    budget = summary["group_risk_budget_usdt"]
    allowed_by_risk = projected_group_risk <= budget + 1e-9
    allowed_by_confirmation = request.confirmation_met
    allowed = allowed_by_risk and allowed_by_confirmation

    projected_legs = [*plan.current_legs, PositionLeg(entry=request.add_price, quantity=request.add_quantity)]
    projected_average = weighted_average_entry(projected_legs)
    projected_plan = plan.model_copy(update={"current_legs": projected_legs})

    blockers: list[str] = []
    if not allowed_by_confirmation:
        blockers.append(f"confirmation not met: {request.confirmation_label}")
    if not allowed_by_risk:
        blockers.append("projected correlation-group risk exceeds configured budget")

    return {
        "symbol": plan.symbol.upper(),
        "side": plan.side,
        "confirmation": {
            "label": request.confirmation_label,
            "met": request.confirmation_met,
        },
        "proposed_add": {
            "price": request.add_price,
            "quantity": request.add_quantity,
            "risk_per_unit": add_risk_per_unit,
            "risk_usdt": add_risk,
        },
        "risk_policy": {
            "group": plan.correlation_group,
            "budget_usdt": budget,
            "current_group_risk_usdt": summary["current_group_risk_usdt"],
            "remaining_before_add_usdt": remaining_risk,
            "projected_group_risk_usdt": projected_group_risk,
            "projected_group_risk_pct": projected_group_risk / plan.account_equity,
            "max_add_quantity_at_price": max(0.0, max_add_quantity),
            "allowed_by_risk": allowed_by_risk,
        },
        "projected_position": {
            "quantity": summary["current_quantity"] + request.add_quantity,
            "weighted_average_entry": projected_average,
            "trade_risk_usdt": _current_trade_risk(projected_plan),
            "target_analysis": _target_analysis(projected_plan, projected_average),
        },
        "allowed_by_confirmation": allowed_by_confirmation,
        "add_allowed": allowed,
        "blockers": blockers,
    }
