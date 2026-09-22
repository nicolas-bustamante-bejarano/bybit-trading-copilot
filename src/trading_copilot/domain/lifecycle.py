from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from trading_copilot.domain.models import Side


class LifecycleAction(StrEnum):
    ENTRY = "entry"
    ADD = "add"
    REDUCE = "reduce"
    EXIT = "exit"
    FLIP = "flip"
    UNKNOWN = "unknown"


class LifecycleEvent(BaseModel):
    exec_id: str | None = None
    order_id: str | None = None
    timestamp_ms: int | None = None
    side: Side
    price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    action: LifecycleAction
    signed_position_before: float
    signed_position_after: float


class PositionLifecycle(BaseModel):
    symbol: str
    current_side: Side
    current_quantity: float = Field(gt=0)
    current_average_entry: float = Field(gt=0)
    history_complete: bool
    matches_current_position: bool
    source_fill_count: int = Field(ge=0)
    selected_fill_count: int = Field(ge=0)
    opening_time_ms: int | None = None
    events: list[LifecycleEvent] = Field(default_factory=list)
