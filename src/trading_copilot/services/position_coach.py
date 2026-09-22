from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.domain.account import NormalizedAccount, NormalizedPosition
from trading_copilot.domain.lifecycle import PositionLifecycle
from trading_copilot.domain.models import Regime, Side
from trading_copilot.domain.playbook import (
    FibAnchors,
    PlaybookEvaluationRequest,
    PlaybookType,
    RangeBounds,
)
from trading_copilot.domain.position_coach import (
    CoachExecution,
    CoachExecutionState,
    CoachMarketContext,
    CoachPlan,
    CoachPosition,
    CoachRisk,
    EvidenceStatus,
    PositionCoach,
    RiskPolicyStatus,
)
from trading_copilot.persistence.models import (
    ExecutionRuleRow,
    FibDefinitionRow,
    RangeDefinitionRow,
    TradePlanRow,
)
from trading_copilot.services.playbook import evaluate_playbook

SUPPORTED_ADD_CONDITIONS = {
    "CONTEXT_VALID",
    "LOCATION_VALID",
    "CONFIRMATION_REQUIRED",
    "RISK_PASS_REQUIRED",
    "DIRECTIONAL_CONFIRMATION",
    "FRESH_DIRECTIONAL_CONFIRMATION",
    "SELLER_CONFIRMATION",
    "BUYER_CONFIRMATION",
}
SUPPORTED_RULE_TYPES = SUPPORTED_ADD_CONDITIONS | {
    "HARD_INVALIDATION",
    "THESIS_WARNING",
    "REDUCE_REQUIRED",
    "EXIT_REQUIRED",
}
REACTION_MAX_AGE_MS = 120_000


def decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _invalidation_breached(side: Side, mark: Decimal | None, level: Decimal | None) -> bool:
    if mark is None or level is None:
        return False
    return mark <= level if side == Side.LONG else mark >= level


def _warning_crossed(side: Side, mark: Decimal | None, warning: Decimal | None) -> bool:
    if mark is None or warning is None:
        return False
    return mark <= warning if side == Side.LONG else mark >= warning


def _reaction_confirmation(side: Side, reaction: str | None) -> bool:
    if side == Side.LONG:
        return reaction in {"buy_continuation", "sell_absorption"}
    return reaction in {"sell_continuation", "buy_absorption"}


def _playbook_evidence(
    plan: TradePlanRow | None,
    side: Side,
    mark: Decimal | None,
    market: dict[str, Any] | None,
    reaction: str | None,
    confirmation: bool,
    fibs: list[FibDefinitionRow],
    ranges: list[RangeDefinitionRow],
) -> tuple[dict[str, Any] | None, str, EvidenceStatus, bool, bool]:
    if plan is None or mark is None or market is None:
        return None, "MISSING", EvidenceStatus.MISSING, False, False
    timeframes = market.get("timeframes", {})
    one_hour = timeframes.get("1h", {})
    four_hour = timeframes.get("4h", {})
    try:
        playbook = PlaybookType(plan.setup_type.lower())
        regime_1h = Regime(str(one_hour["regime"]))
        regime_4h = Regime(str(four_hour["regime"]))
    except (KeyError, ValueError):
        return None, "INDETERMINATE", EvidenceStatus.INDETERMINATE, False, False

    kwargs: dict[str, Any] = {}
    if playbook == PlaybookType.TREND_PULLBACK:
        if len(fibs) != 1:
            status = EvidenceStatus.MISSING if not fibs else EvidenceStatus.INDETERMINATE
            return None, "MISSING" if not fibs else "AMBIGUOUS", status, False, False
        kwargs["fib"] = FibAnchors(
            swing_low=float(fibs[0].swing_low), swing_high=float(fibs[0].swing_high)
        )
    else:
        if len(ranges) != 1:
            status = EvidenceStatus.MISSING if not ranges else EvidenceStatus.INDETERMINATE
            return None, "MISSING" if not ranges else "AMBIGUOUS", status, False, False
        kwargs["range_bounds"] = RangeBounds(
            low=float(ranges[0].range_low), high=float(ranges[0].range_high)
        )
    request = PlaybookEvaluationRequest(
        symbol=plan.symbol,
        playbook=playbook,
        side=side,
        price=float(mark),
        regime_1h=regime_1h,
        regime_4h=regime_4h,
        stoch_k=one_hour.get("stoch_rsi_k"),
        stoch_d=one_hour.get("stoch_rsi_d"),
        reaction_state=reaction,
        trigger_confirmed=confirmation,
        **kwargs,
    )
    result = evaluate_playbook(request)
    conditions = {condition["name"]: condition["status"] for condition in result["conditions"]}
    context_name = (
        "4h_regime_aligned" if playbook == PlaybookType.TREND_PULLBACK else "4h_range_regime"
    )
    context_valid = conditions.get(context_name) is True
    location_data = result["location"]
    at_location = bool(location_data.get("at_location"))
    location = location_data.get("active_zone") or location_data.get("type") or "UNKNOWN"
    return result, str(location).upper(), EvidenceStatus.CONFIRMED, at_location, context_valid


def _planned_add_permission(
    plan: TradePlanRow | None,
    rules: list[ExecutionRuleRow],
    *,
    context_valid: bool,
    at_location: bool,
    confirmation: bool,
    risk_pass: bool,
) -> tuple[bool, list[str], list[str]]:
    if plan is None:
        return False, [], []
    raw_conditions: list[Any] = list(plan.add_conditions or [])
    raw_conditions.extend(rule.rule_type for rule in rules if str(rule.action).upper() == "ADD")
    if not raw_conditions:
        return False, ["no predefined add condition exists in the trade plan"], []

    values = {
        "CONTEXT_VALID": context_valid,
        "LOCATION_VALID": at_location,
        "CONFIRMATION_REQUIRED": confirmation,
        "DIRECTIONAL_CONFIRMATION": confirmation,
        "FRESH_DIRECTIONAL_CONFIRMATION": confirmation,
        "SELLER_CONFIRMATION": confirmation,
        "BUYER_CONFIRMATION": confirmation,
        "RISK_PASS_REQUIRED": risk_pass,
    }
    unsatisfied: list[str] = []
    unsupported: list[str] = []
    for raw in raw_conditions:
        if isinstance(raw, str):
            name = raw
        elif isinstance(raw, dict):
            name = raw.get("type") or raw.get("rule_type") or raw.get("condition")
        else:
            name = None
        normalized = str(name).upper() if name else "UNSPECIFIED"
        if normalized not in SUPPORTED_ADD_CONDITIONS:
            unsupported.append(normalized)
        elif not values[normalized]:
            unsatisfied.append(normalized)
    return not unsatisfied and not unsupported, unsatisfied, unsupported


def _risk(
    account: NormalizedAccount,
    position: NormalizedPosition,
    plan: TradePlanRow | None,
    plans_by_symbol: dict[str, TradePlanRow],
) -> CoachRisk:
    equity = decimal(account.equity_usdt) or Decimal()
    group = (
        plan.correlation_group if plan and plan.correlation_group else position.correlation_group
    )
    max_pct = plan.max_risk_percent if plan else None
    known = Decimal()
    incomplete: list[str] = []
    provenance: dict[str, str] = {}
    position_risk: Decimal | None = None

    for current in account.positions:
        if current.correlation_group != group:
            continue
        current_plan = plans_by_symbol.get(current.symbol)
        quantity = decimal(current.quantity) or Decimal()
        entry = decimal(current.average_entry)
        if current_plan is not None and entry is not None:
            risk = quantity * abs(entry - current_plan.hard_invalidation)
            provenance[current.symbol] = "execution_plan"
        elif current.structural_risk_usdt is not None:
            risk = decimal(current.structural_risk_usdt) or Decimal()
            provenance[current.symbol] = (
                "exchange_order" if current.stop_loss is not None else "inferred"
            )
        else:
            risk = None
            provenance[current.symbol] = "unknown"
        if risk is None:
            incomplete.append(current.symbol)
        else:
            known += risk
        if current.symbol == position.symbol:
            position_risk = risk

    budget = equity * max_pct if max_pct is not None else None
    if budget is not None and known > budget:
        status = RiskPolicyStatus.BREACH
    elif budget is None or incomplete:
        status = RiskPolicyStatus.INDETERMINATE
    else:
        status = RiskPolicyStatus.PASS
    return CoachRisk(
        account_equity=equity,
        position_structural_risk_usdt=position_risk,
        position_risk_pct=position_risk / equity
        if position_risk is not None and equity > 0
        else None,
        correlation_group=group,
        known_group_risk_usdt=known,
        group_risk_pct=known / equity if equity > 0 else None,
        max_risk_pct=max_pct,
        policy_status=status,
        provenance=provenance,
        incomplete_symbols=sorted(incomplete),
    )


def evaluate_position_coach(
    *,
    position: NormalizedPosition,
    account: NormalizedAccount,
    lifecycle: PositionLifecycle,
    plan: TradePlanRow | None,
    rules: list[ExecutionRuleRow],
    plans_by_symbol: dict[str, TradePlanRow],
    market: dict[str, Any] | None,
    live: dict[str, Any] | None,
    fibs: list[FibDefinitionRow],
    ranges: list[RangeDefinitionRow],
    now: datetime | None = None,
) -> PositionCoach:
    now = now or datetime.now(UTC)
    side = position.side
    if plan is not None and plan.side.upper() != side.value.upper():
        raise ValueError("Active trade plan side does not match the live position side")
    mark = decimal(position.mark_price)
    invalidation = plan.hard_invalidation if plan else None
    warning = plan.thesis_warning if plan else None
    breached = _invalidation_breached(side, mark, invalidation)
    warned = _warning_crossed(side, mark, warning) and not breached

    reaction_payload = (live or {}).get("reaction_1m")
    reaction = reaction_payload.get("state") if reaction_payload else None
    live_timestamp = (live or {}).get("timestamp_ms")
    age_ms = int(now.timestamp() * 1000) - live_timestamp if live_timestamp else None
    if reaction is None:
        reaction_status = EvidenceStatus.MISSING
    elif age_ms is not None and age_ms > REACTION_MAX_AGE_MS:
        reaction_status = EvidenceStatus.STALE
    else:
        reaction_status = EvidenceStatus.CONFIRMED
    confirmation = reaction_status == EvidenceStatus.CONFIRMED and _reaction_confirmation(
        side, reaction
    )
    playbook_result, location, location_status, at_location, context_valid = _playbook_evidence(
        plan, side, mark, market, reaction, confirmation, fibs, ranges
    )

    risk = _risk(account, position, plan, plans_by_symbol)
    unsupported = sorted(
        {rule.rule_type for rule in rules if rule.rule_type not in SUPPORTED_RULE_TYPES}
    )
    evidence_present: list[str] = []
    evidence_missing: list[str] = []
    blocking: list[str] = []
    warnings: list[str] = []

    if plan is None:
        blocking.append("No active trade plan is attached to this position.")
    else:
        evidence_present.append("structural invalidation is defined by the execution plan")
    if at_location:
        evidence_present.append("planned location is present")
    else:
        evidence_missing.append("valid planned location")
    if confirmation:
        evidence_present.append("directional reaction confirmation is present")
    else:
        evidence_missing.append("fresh directional reaction confirmation")
    if risk.policy_status != RiskPolicyStatus.PASS:
        blocking.append(
            "risk budget exceeded"
            if risk.policy_status == RiskPolicyStatus.BREACH
            else "risk capacity cannot be established safely"
        )
    if warned:
        warnings.append("Thesis warning level has been crossed; hard invalidation remains intact.")
        blocking.append("thesis warning blocks an aggressive add")
    if unsupported:
        blocking.append("unsupported execution rules: " + ", ".join(unsupported))

    triggered_rule_types = {
        rule.rule_type for rule in rules if (rule.parameters or {}).get("triggered") is True
    }
    if not context_valid:
        evidence_missing.append("playbook context is valid")
    planned_add_satisfied, unsatisfied_add, unsupported_add = _planned_add_permission(
        plan,
        rules,
        context_valid=context_valid,
        at_location=at_location,
        confirmation=confirmation,
        risk_pass=risk.policy_status == RiskPolicyStatus.PASS,
    )
    if (
        plan
        and not (plan.add_conditions or [])
        and not any(str(rule.action).upper() == "ADD" for rule in rules)
    ):
        blocking.append("no predefined add condition exists in the trade plan")
    if unsatisfied_add:
        blocking.append("predefined add conditions are unsatisfied: " + ", ".join(unsatisfied_add))
    if unsupported_add:
        blocking.append("unsupported predefined add conditions: " + ", ".join(unsupported_add))

    add_allowed = bool(
        plan
        and not breached
        and not warned
        and context_valid
        and at_location
        and confirmation
        and risk.policy_status == RiskPolicyStatus.PASS
        and not unsupported
        and planned_add_satisfied
    )
    if breached:
        state = CoachExecutionState.INVALIDATE
    elif "EXIT_REQUIRED" in triggered_rule_types:
        state = CoachExecutionState.EXIT
        add_allowed = False
    elif "REDUCE_REQUIRED" in triggered_rule_types:
        state = CoachExecutionState.REDUCE
        add_allowed = False
    else:
        state = CoachExecutionState.ADD if add_allowed else CoachExecutionState.HOLD
    if breached:
        blocking.insert(0, "hard structural invalidation breached")
        add_allowed = False

    planned_risk = None
    open_r = None
    if invalidation is not None:
        planned_risk = (decimal(position.quantity) or Decimal()) * abs(
            (decimal(position.average_entry) or Decimal()) - invalidation
        )
        pnl = decimal(position.unrealized_pnl)
        if pnl is not None and planned_risk > 0:
            open_r = pnl / planned_risk

    timeframe = (market or {}).get("timeframes", {})
    one_hour = timeframe.get("1h", {})
    four_hour = timeframe.get("4h", {})
    market_status = EvidenceStatus.CONFIRMED if market else EvidenceStatus.PARTIAL
    confidence = EvidenceStatus.CONFIRMED
    if plan is None or risk.policy_status == RiskPolicyStatus.INDETERMINATE:
        confidence = EvidenceStatus.INDETERMINATE
    elif reaction_status != EvidenceStatus.CONFIRMED or market is None:
        confidence = EvidenceStatus.PARTIAL

    return PositionCoach(
        symbol=position.symbol.upper(),
        timestamp=now,
        side=side.value.upper(),
        position=CoachPosition(
            quantity=decimal(position.quantity) or Decimal(),
            average_entry=decimal(position.average_entry) or Decimal(),
            mark_price=mark,
            unrealized_pnl_usdt=decimal(position.unrealized_pnl),
            planned_open_risk_usdt=planned_risk,
            open_position_r=open_r,
            lifecycle_history_complete=lifecycle.history_complete,
            lifecycle_confidence=EvidenceStatus.CONFIRMED
            if lifecycle.history_complete
            else EvidenceStatus.PARTIAL,
        ),
        plan=CoachPlan(
            status=EvidenceStatus.CONFIRMED if plan else EvidenceStatus.MISSING,
            trade_plan_id=plan.id if plan else None,
            setup_type=plan.setup_type if plan else None,
            lifecycle_status=plan.lifecycle_status if plan else None,
            thesis=plan.thesis if plan else None,
            hard_invalidation=invalidation,
            thesis_warning=warning,
            correlation_group=plan.correlation_group if plan else None,
            targets=plan.target_ladder if plan else [],
            stop_provenance="execution_plan" if plan else "unknown",
        ),
        market_context=CoachMarketContext(
            status=market_status,
            regime_4h=str(four_hour.get("regime")) if four_hour.get("regime") else None,
            context_1h=str(one_hour.get("regime")) if one_hour.get("regime") else None,
            ema_12=decimal(one_hour.get("ema12")),
            ema_21=decimal(one_hour.get("ema21")),
            stoch_rsi={
                "k": decimal(one_hour.get("stoch_rsi_k")),
                "d": decimal(one_hour.get("stoch_rsi_d")),
            },
            location=location,
            location_status=location_status,
            playbook_state=str(playbook_result["state"]).upper() if playbook_result else None,
            playbook_conditions=playbook_result["conditions"] if playbook_result else [],
            reaction=reaction,
            reaction_status=reaction_status,
            order_flow=(live or {}).get("trade_flow_60s", {}),
            funding=decimal((live or {}).get("funding_rate") or (market or {}).get("funding_rate")),
            open_interest=decimal((live or {}).get("open_interest")),
            timestamp_ms=live_timestamp,
        ),
        risk=risk,
        execution=CoachExecution(
            state=state,
            add_allowed=add_allowed,
            evidence_present=evidence_present,
            evidence_missing=evidence_missing,
            blocking_reasons=blocking,
            warnings=warnings,
            next_conditions=[
                item
                for condition, item in (
                    (not at_location, "price reaches a defined plan location"),
                    (not confirmation, "fresh directional order-flow reaction confirms"),
                    (
                        risk.policy_status != RiskPolicyStatus.PASS,
                        "correlation-group risk policy passes",
                    ),
                )
                if condition
            ],
            invalidation=invalidation,
        ),
        confidence=confidence,
    )


async def load_coach_records(session: AsyncSession, symbol: str):
    plans = list(
        (
            await session.scalars(
                select(TradePlanRow).where(
                    TradePlanRow.symbol == symbol, TradePlanRow.lifecycle_status == "ACTIVE"
                )
            )
        ).all()
    )
    if len(plans) > 1:
        raise ValueError("Multiple active trade plans exist for this symbol")
    plan = plans[0] if plans else None
    rules = []
    if plan:
        rules = list(
            (
                await session.scalars(
                    select(ExecutionRuleRow).where(ExecutionRuleRow.trade_plan_id == plan.id)
                )
            ).all()
        )
    fibs = []
    ranges = []
    if plan:
        fibs = list(
            (
                await session.scalars(
                    select(FibDefinitionRow).where(FibDefinitionRow.trade_plan_id == plan.id)
                )
            ).all()
        )
        ranges = list(
            (
                await session.scalars(
                    select(RangeDefinitionRow).where(RangeDefinitionRow.trade_plan_id == plan.id)
                )
            ).all()
        )
    if not fibs:
        fibs = list(
            (
                await session.scalars(
                    select(FibDefinitionRow).where(
                        FibDefinitionRow.symbol == symbol,
                        FibDefinitionRow.trade_plan_id.is_(None),
                    )
                )
            ).all()
        )
    if not ranges:
        ranges = list(
            (
                await session.scalars(
                    select(RangeDefinitionRow).where(
                        RangeDefinitionRow.symbol == symbol,
                        RangeDefinitionRow.trade_plan_id.is_(None),
                    )
                )
            ).all()
        )
    all_active = list(
        (
            await session.scalars(
                select(TradePlanRow).where(TradePlanRow.lifecycle_status == "ACTIVE")
            )
        ).all()
    )
    unique_plans: dict[str, TradePlanRow] = {}
    ambiguous: set[str] = set()
    for item in all_active:
        if item.symbol in unique_plans:
            ambiguous.add(item.symbol)
        else:
            unique_plans[item.symbol] = item
    for ambiguous_symbol in ambiguous:
        unique_plans.pop(ambiguous_symbol, None)
    return plan, rules, fibs, ranges, unique_plans
