from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.persistence.models import ScannerTransitionRow, WatchedSetupRow


async def persist_candidate(
    session: AsyncSession,
    *,
    symbol: str,
    setup_type: str,
    status: str,
    state: dict,
    trade_plan_id: str | None = None,
) -> WatchedSetupRow:
    """Persist a candidate and append an event only when status changes.

    Version 1 is the first observation. Each material status transition advances the
    version exactly once. The current row and transition event share one commit.
    """
    row = await session.scalar(
        select(WatchedSetupRow).where(
            WatchedSetupRow.symbol == symbol.upper(),
            WatchedSetupRow.setup_type == setup_type,
        )
    )
    now = datetime.now(UTC)
    if row is None:
        row = WatchedSetupRow(
            symbol=symbol,
            setup_type=setup_type,
            status=status,
            state=state,
            trade_plan_id=trade_plan_id,
            version=1,
            last_evaluated_at=now,
        )
        session.add(row)
    else:
        changed = row.status != status
        before = dict(row.state)
        previous = row.status
        row.state = state
        row.last_evaluated_at = now
        if changed:
            row.status = status
            row.version += 1
            session.add(
                ScannerTransitionRow(
                    watched_setup_id=row.id,
                    symbol=row.symbol,
                    setup_type=row.setup_type,
                    from_status=previous,
                    to_status=status,
                    timestamp=now,
                    state_before=before,
                    state_after=dict(state),
                    version=row.version,
                )
            )
    await session.commit()
    await session.refresh(row)
    return row
