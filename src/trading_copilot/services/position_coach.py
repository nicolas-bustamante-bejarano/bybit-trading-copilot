from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.domain.account import NormalizedAccount, NormalizedPosition
from trading_copilot.domain.lifecycle import PositionLifecycle
from trading_copilot.domain.models import Side
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

SUPPORTED_RULE_TYPES = {
    "CONTEXT_VALID",
    "LOCATION_VALID",
    "CONFIRMATION_REQUIRED",
    "RISK_PASS_REQUIRED",
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


def _location(
    side: Side,
    mark: Decimal | None,
    fibs: list[FibDefinitionRow],
    ranges: list[RangeDefinitionRow],
) -> tuple[str, EvidenceStatus, bool]:
    definitions = len(fibs) + len(ranges)
    if definitions == 0 or mark is None:
        return "MISSING", EvidenceStatus.MISSING, False
    if definitions > 1:
        return "AMBIGUOUS", EvidenceStatus.INDETERMINATE, False
    tolerance = abs(mark) * Decimal("0.005")
    if ranges:
        boundary = ranges[0].range_low if side == Side.LONG else ranges[0].range_high
        at_location = abs(mark - boundary) <= tolerance
        return (
            ("AT_PLANNED_RANGE" if at_location else "AWAY_FROM_PLANNED_RANGE"),
            EvidenceStatus.CONFIRMED,
            at_location,
        )
    fib = fibs[0]
    span = fib.swing_high - fib.swing_low
    levels = [
        fib.swing_low + span * Decimal(str(level))
        for level in (0.236, 0.382, 0.5, 0.618, 0.786, 0.886)
    ]
    at_location = any(abs(mark - level) <= tolerance for level in levels)
    return (
        ("AT_PLANNED_FIB" if at_location else "AWAY_FROM_PLANNED_FIB"),
        EvidenceStatus.CONFIRMED,
        at_location,
    )


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
    mark = decimal(position.mark_price)
    invalidation = plan.hard_invalidation if plan else None
    warning = plan.thesis_warning if plan else None
    breached = _invalidation_breached(side, mark, invalidation)
    warned = _warning_crossed(side, mark, warning) and not breached
    location, location_status, at_location = _location(side, mark, fibs, ranges)

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
    context_valid = market is not None
    if not context_valid:
        evidence_missing.append("current 1H/4H market context")

    add_allowed = bool(
        plan
        and not breached
        and not warned
        and context_valid
        and at_location
        and confirmation
        and risk.policy_status == RiskPolicyStatus.PASS
        and not unsupported
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
    association = [FibDefinitionRow.symbol == symbol]
    if plan:
        association.append(FibDefinitionRow.trade_plan_id == plan.id)
    fibs = list((await session.scalars(select(FibDefinitionRow).where(or_(*association)))).all())
    range_association = [RangeDefinitionRow.symbol == symbol]
    if plan:
        range_association.append(RangeDefinitionRow.trade_plan_id == plan.id)
    ranges = list(
        (await session.scalars(select(RangeDefinitionRow).where(or_(*range_association)))).all()
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
