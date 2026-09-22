from decimal import ROUND_CEILING, Decimal
from typing import Any

from trading_copilot.domain.workspace import SizingRequest, floor_step


def build_sizing_plan(
    request: SizingRequest,
    *,
    equity: Decimal,
    available_margin: Decimal | None,
    group_risk: Decimal,
    group_unknown: bool,
    qty_step: Decimal,
    min_qty: Decimal,
    min_notional: Decimal,
) -> dict[str, Any]:
    """Pure, conservative sizing calculation. Leverage only changes margin presentation."""
    budget = equity * request.max_risk_percent
    remaining = max(Decimal(), budget - group_risk)
    permitted = Decimal() if group_unknown else min(budget, remaining)
    unit_risk = abs(request.entry - request.hard_invalidation)
    friction = request.entry * (request.fee_bps + request.slippage_bps) / Decimal(10000)
    total_unit_risk = unit_risk + friction
    quantity = floor_step(permitted / total_unit_risk, qty_step) if total_unit_risk else Decimal()
    notional = quantity * request.entry
    constraints_ok = quantity >= min_qty and notional >= min_notional
    base_reasons: list[str] = []
    if group_unknown:
        base_reasons.append("Correlation-group structural risk is indeterminate")
    if remaining <= 0:
        base_reasons.append("No remaining correlation-group risk capacity")
    if quantity and not constraints_ok:
        base_reasons.append("Rounded quantity does not meet exchange minimums")
    if not quantity:
        base_reasons.append("Risk budget does not permit a tradable quantity")
    stages = []
    seen = set(request.prior_evidence)
    current = set(request.current_evidence)
    for stage in request.stages:
        required = set(stage.required_evidence)
        missing = sorted(required - current)
        novel = required - seen
        reasons = list(base_reasons)
        if stage.name != "PROBE" and required and not novel:
            reasons.append("Add requires evidence not already used by an earlier stage")
        if missing:
            reasons.append("Missing required evidence: " + ", ".join(missing))
        stage_qty = floor_step(quantity * stage.allocation, qty_step)
        allowed = constraints_ok and permitted > 0 and not reasons
        stages.append({
            "state": stage.name, "allocation": stage.allocation, "quantity": stage_qty,
            "notional": stage_qty * request.entry, "allowed": allowed,
            "status": "PASS" if allowed else ("INDETERMINATE" if group_unknown else "LOCKED"),
            "reasons": reasons,
            "required_evidence": sorted(required), "evidence_present": sorted(required & current),
        })
        seen |= required & current
    suggested = None
    if available_margin is not None and available_margin > 0 and notional > 0:
        suggested = (notional / available_margin).to_integral_value(rounding=ROUND_CEILING)
        suggested = max(Decimal(1), suggested)
    leverage = request.requested_leverage or suggested
    return {
        "symbol": request.symbol.upper(), "risk_budget_usdt": budget,
        "group_risk_used_usdt": group_risk, "group_risk_remaining_usdt": remaining,
        "permitted_risk_usdt": permitted, "risk_per_unit_usdt": total_unit_risk,
        "stop_distance": unit_risk, "fee_and_slippage_per_unit": friction,
        "maximum_quantity": quantity, "maximum_notional": notional,
        "qty_step": qty_step, "min_qty": min_qty, "min_notional": min_notional,
        "suggested_min_leverage": suggested, "selected_leverage": leverage,
        "estimated_margin_usdt": (notional / leverage) if leverage else None,
        "leverage_note": "Leverage changes estimated margin only; it never increases allowed risk quantity.",
        "stages": stages,
    }
