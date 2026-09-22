from enum import StrEnum

from pydantic import BaseModel, Field

from trading_copilot.domain.models import Side
from trading_copilot.domain.playbook import FibAnchors, PlaybookType, RangeBounds


class MomentumTimeframe(StrEnum):
    ONE_HOUR = "1h"
    FOUR_HOUR = "4h"


class CopilotEvaluationRequest(BaseModel):
    symbol: str = Field(min_length=1)
    playbook: PlaybookType
    side: Side
    fib: FibAnchors | None = None
    range_bounds: RangeBounds | None = None
    momentum_timeframe: MomentumTimeframe = MomentumTimeframe.ONE_HOUR
    trigger_confirmed: bool = False
    invalidation_breached: bool = False
    approach_tolerance_bps: float = Field(default=50.0, ge=0)
