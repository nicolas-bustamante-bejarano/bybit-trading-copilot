from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.domain.position_coach import PositionCoach
from trading_copilot.persistence.models import (
    CoachStateCursorRow,
    DecisionSnapshotRow,
    StateChangeEventRow,
)

MATERIAL_FIELDS = (
    "position_open",
    "trade_plan_id",
    "plan_status",
    "execution_state",
    "add_allowed",
    "risk_policy_status",
    "reaction_status",
    "reaction_state",
    "playbook_state",
    "warning_active",
    "invalidation_breached",
    "confidence",
)


def project_coach_state(coach: PositionCoach) -> dict[str, Any]:
    return {
        "position_open": True,
        "trade_plan_id": coach.plan.trade_plan_id,
        "plan_status": coach.plan.status.value,
        "execution_state": coach.execution.state.value,
        "add_allowed": coach.execution.add_allowed,
        "risk_policy_status": coach.risk.policy_status.value,
        "reaction_status": coach.market_context.reaction_status.value,
        "reaction_state": coach.market_context.reaction,
        "playbook_state": coach.market_context.playbook_state,
        "warning_active": any("Thesis warning" in item for item in coach.execution.warnings),
        "invalidation_breached": coach.execution.state.value == "INVALIDATE",
        "confidence": coach.confidence.value,
    }


def material_changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"field": field, "from": before.get(field), "to": after.get(field)}
        for field in MATERIAL_FIELDS
        if before.get(field) != after.get(field)
    ]


def _change(changes: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
    return next((item for item in changes if item["field"] == field), None)


def classify_transition(changes: list[dict[str, Any]]) -> tuple[str, str, str]:
    position = _change(changes, "position_open")
    invalidation = _change(changes, "invalidation_breached")
    execution = _change(changes, "execution_state")
    warning = _change(changes, "warning_active")
    risk = _change(changes, "risk_policy_status")
    plan = _change(changes, "trade_plan_id")
    reaction = _change(changes, "reaction_status")
    playbook = _change(changes, "playbook_state")
    add = _change(changes, "add_allowed")

    if position:
        return (
            "POSITION_OPENED" if position["to"] else "POSITION_CLOSED",
            "INFO",
            "Position opened" if position["to"] else "Position closed",
        )
    if invalidation and invalidation["to"]:
        return "INVALIDATION_BREACHED", "CRITICAL", "Hard invalidation breached"
    if execution:
        importance = "CRITICAL" if execution["to"] == "INVALIDATE" else "INFO"
        return "EXECUTION_STATE_CHANGED", importance, f'{execution["from"]} → {execution["to"]}'
    if warning:
        return (
            "THESIS_WARNING_CHANGED",
            "WARNING" if warning["to"] else "INFO",
            "Thesis warning became active" if warning["to"] else "Thesis warning cleared",
        )
    if risk:
        return (
            "RISK_POLICY_CHANGED",
            "WARNING" if risk["to"] == "BREACH" else "INFO",
            f'Risk {risk["from"]} → {risk["to"]}',
        )
    if plan:
        if plan["from"] is None:
            summary = "Active trade plan attached"
        elif plan["to"] is None:
            summary = "Active trade plan removed"
        else:
            summary = "Active trade plan changed"
        return "TRADE_PLAN_CHANGED", "INFO", summary
    if reaction:
        return "REACTION_CHANGED", "INFO", f'Reaction {reaction["from"]} → {reaction["to"]}'
    if playbook:
        return "PLAYBOOK_CHANGED", "INFO", f'Playbook {playbook["from"]} → {playbook["to"]}'
    if add:
        return (
            "ADD_PERMISSION_CHANGED",
            "WARNING" if not add["to"] else "INFO",
            "ADD permission granted" if add["to"] else "ADD permission revoked",
        )
    first = changes[0]
    return "COACH_STATE_CHANGED", "INFO", f'{first["field"]} {first["from"]} → {first["to"]}'


def deterministic_reason(changes: list[dict[str, Any]]) -> str:
    parts = [f'{item["field"].replace("_", " ")} {item["from"]} -> {item["to"]}' for item in changes]
    return "Automatic state change: " + "; ".join(parts) + "."


async def persist_observation(
    session: AsyncSession,
    *,
    symbol: str,
    state: dict[str, Any],
    coach: PositionCoach | None,
    observed_at: datetime | None = None,
) -> StateChangeEventRow | None:
    symbol = symbol.upper()
    observed_at = observed_at or datetime.now(UTC)
    cursor = await session.get(CoachStateCursorRow, symbol)
    if cursor is None:
        session.add(
            CoachStateCursorRow(
                symbol=symbol,
                position_open=bool(state["position_open"]),
                trade_plan_id=state.get("trade_plan_id"),
                version=1,
                state=state,
                updated_at=observed_at,
            )
        )
        await session.commit()
        return None

    changes = material_changes(cursor.state, state)
    if not changes:
        return None

    from_version = cursor.version
    to_version = from_version + 1
    event_type, importance, summary = classify_transition(changes)
    snapshot = None
    plan_id = state.get("trade_plan_id")
    if (
        coach is not None
        and state.get("position_open")
        and plan_id
        and coach.position.mark_price is not None
    ):
        snapshot = DecisionSnapshotRow(
            id=str(uuid4()),
            trade_plan_id=plan_id,
            timestamp=observed_at,
            symbol=symbol,
            price=coach.position.mark_price,
            action_considered=coach.execution.state.value,
            action_taken=None,
            state={
                "source": "state_change_monitor",
                "automatic": True,
                "changes": changes,
                "previous": cursor.state,
                "current": state,
                "coach": coach.model_dump(mode="json"),
            },
            evidence_present=coach.execution.evidence_present,
            evidence_missing=coach.execution.evidence_missing,
            confirmation_conditions=coach.execution.next_conditions,
            reason=deterministic_reason(changes),
            data_confidence={"status": coach.confidence.value},
        )
        session.add(snapshot)

    event = StateChangeEventRow(
        timestamp=observed_at,
        symbol=symbol,
        trade_plan_id=plan_id,
        decision_snapshot_id=snapshot.id if snapshot else None,
        event_type=event_type,
        importance=importance,
        changes=changes,
        summary=summary,
        state_before=cursor.state,
        state_after=state,
        from_version=from_version,
        to_version=to_version,
        confidence_status=state.get("confidence", "INDETERMINATE"),
    )
    session.add(event)
    cursor.position_open = bool(state["position_open"])
    cursor.trade_plan_id = plan_id
    cursor.version = to_version
    cursor.state = state
    cursor.updated_at = observed_at
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(StateChangeEventRow).where(
                StateChangeEventRow.symbol == symbol,
                StateChangeEventRow.to_version == to_version,
            )
        )
        return existing
    except Exception:
        await session.rollback()
        raise
    await session.refresh(event)
    return event


async def persist_closed_position(
    session: AsyncSession, symbol: str, observed_at: datetime | None = None
) -> StateChangeEventRow | None:
    cursor = await session.get(CoachStateCursorRow, symbol.upper())
    if cursor is None or not cursor.position_open:
        return None
    state = dict(cursor.state)
    state["position_open"] = False
    return await persist_observation(
        session, symbol=symbol, state=state, coach=None, observed_at=observed_at
    )
