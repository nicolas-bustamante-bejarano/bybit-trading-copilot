from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StateChangeEvent(BaseModel):
    id: str
    timestamp: datetime
    symbol: str
    trade_plan_id: str | None
    decision_snapshot_id: str | None
    event_type: str
    importance: str
    summary: str
    changes: list[dict[str, Any]] = Field(default_factory=list)
    state_before: dict[str, Any]
    state_after: dict[str, Any]
    confidence_status: str


class StateChangeMonitorStatus(BaseModel):
    enabled: bool
    running: bool
    interval_seconds: float
    last_cycle_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None

