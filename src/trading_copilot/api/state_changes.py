from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.domain.state_change import StateChangeEvent, StateChangeMonitorStatus
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import StateChangeEventRow

router = APIRouter(tags=["state changes"])
_status_provider = None


def set_status_provider(provider) -> None:
    global _status_provider
    _status_provider = provider


@router.get("/state-changes", response_model=list[StateChangeEvent])
async def list_state_changes(
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    query = select(StateChangeEventRow).order_by(StateChangeEventRow.timestamp.desc()).limit(limit)
    if symbol:
        query = query.where(StateChangeEventRow.symbol == symbol.upper())
    rows = (await session.scalars(query)).all()
    return [
        StateChangeEvent.model_validate(
            {column.name: getattr(row, column.key) for column in row.__table__.columns}
        )
        for row in rows
    ]


@router.get("/state-change-monitor/status", response_model=StateChangeMonitorStatus)
def monitor_status():
    if _status_provider is None:
        return StateChangeMonitorStatus(enabled=False, running=False, interval_seconds=5)
    return _status_provider()
