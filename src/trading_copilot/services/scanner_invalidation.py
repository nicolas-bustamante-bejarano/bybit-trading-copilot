from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.persistence.models import (
    ScannerTransitionRow,
    TriggerAttemptRow,
    WatchedSetupRow,
)


def _structure_id(state: dict) -> str | None:
    structure = state.get("structure")
    if not isinstance(structure, dict):
        return None
    value = structure.get("structure_id") or structure.get("id")
    return value if isinstance(value, str) else None


def _reset_state(row: WatchedSetupRow, *, now: datetime, reason: str) -> dict:
    previous = row.state if isinstance(row.state, dict) else {}
    state = {
        "symbol": row.symbol,
        "setup_type": row.setup_type,
        "side": "LONG" if row.setup_type.endswith("_LONG") else "SHORT",
        "status": "WATCH",
        "price": previous.get("price"),
        "evaluated_at": now.isoformat(),
        "structure": {},
        "context": {},
        "location": {},
        "reaction": {},
        "conditions": {},
        "blocking_reasons": ["STRUCTURE_REQUIRED", reason],
        "next_conditions": ["Define compatible canonical structure"],
        "data_status": previous.get("data_status", "PARTIAL"),
    }
    return state


async def _reset_rows(
    session: AsyncSession,
    rows: list[WatchedSetupRow],
    *,
    reason: str,
) -> int:
    now = datetime.now(UTC)
    for row in rows:
        before = deepcopy(row.state) if isinstance(row.state, dict) else {}
        previous_status = row.status
        row.status = "WATCH"
        row.state = _reset_state(row, now=now, reason=reason)
        row.version += 1
        row.last_evaluated_at = now
        session.add(
            ScannerTransitionRow(
                watched_setup_id=row.id,
                symbol=row.symbol,
                setup_type=row.setup_type,
                from_status=previous_status,
                to_status="WATCH",
                timestamp=now,
                state_before=before,
                state_after=deepcopy(row.state),
                version=row.version,
            )
        )
    await session.flush()
    return len(rows)


async def invalidate_chart_structure_dependents(
    session: AsyncSession,
    *,
    structure_id: str,
) -> int:
    rows = list(
        await session.scalars(
            select(WatchedSetupRow).where(
                WatchedSetupRow.setup_type.like("MACRO_BREAKOUT_%")
            )
        )
    )
    setup_ids = [row.id for row in rows]
    attempts = (
        list(
            await session.scalars(
                select(TriggerAttemptRow).where(
                    TriggerAttemptRow.watched_setup_id.in_(setup_ids)
                )
            )
        )
        if setup_ids
        else []
    )
    armed_setup_ids = {
        attempt.watched_setup_id
        for attempt in attempts
        if isinstance(attempt.reference_metadata, dict)
        and attempt.reference_metadata.get("structure_id") == structure_id
    }
    dependent = [
        row
        for row in rows
        if (
            (isinstance(row.state, dict) and row.state.get("accepted_structure_id") == structure_id)
            or _structure_id(row.state if isinstance(row.state, dict) else {}) == structure_id
            or row.id in armed_setup_ids
        )
    ]
    return await _reset_rows(
        session,
        dependent,
        reason="CANONICAL_STRUCTURE_CHANGED",
    )


async def invalidate_plan_structure_dependents(
    session: AsyncSession,
    *,
    trade_plan_id: str,
    setup_family: str,
) -> int:
    rows = list(
        await session.scalars(
            select(WatchedSetupRow).where(
                WatchedSetupRow.trade_plan_id == trade_plan_id,
                WatchedSetupRow.setup_type.like(f"{setup_family}_%"),
            )
        )
    )
    return await _reset_rows(
        session,
        rows,
        reason="CANONICAL_STRUCTURE_CHANGED",
    )
