from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from trading_copilot.domain.models import Regime, Side


class PlaybookType(StrEnum):
    TREND_PULLBACK = "trend_pullback"
    RANGE_LONG = "range_long"
    RANGE_SHORT = "range_short"


class FibAnchors(BaseModel):
    swing_low: float = Field(gt=0)
    swing_high: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "FibAnchors":
        if self.swing_high <= self.swing_low:
            raise ValueError("swing_high must be greater than swing_low")
        return self


class RangeBounds(BaseModel):
    low: float = Field(gt=0)
    high: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "RangeBounds":
        if self.high <= self.low:
            raise ValueError("range high must be greater than range low")
        return self


class PlaybookEvaluationRequest(BaseModel):
    symbol: str = Field(min_length=1)
    playbook: PlaybookType
    side: Side
    price: float = Field(gt=0)
    regime_1h: Regime
    regime_4h: Regime
    stoch_k: float | None = Field(default=None, ge=0, le=100)
    stoch_d: float | None = Field(default=None, ge=0, le=100)
    prev_stoch_k: float | None = Field(default=None, ge=0, le=100)
    prev_stoch_d: float | None = Field(default=None, ge=0, le=100)
    fib: FibAnchors | None = None
    range_bounds: RangeBounds | None = None
    reaction_state: str | None = None
    trigger_confirmed: bool = False
    invalidation_breached: bool = False
    approach_tolerance_bps: float = Field(default=50.0, ge=0)

    @model_validator(mode="after")
    def validate_playbook_inputs(self) -> "PlaybookEvaluationRequest":
        if self.playbook == PlaybookType.TREND_PULLBACK and self.fib is None:
            raise ValueError("trend_pullback requires fib anchors")
        if self.playbook in {PlaybookType.RANGE_LONG, PlaybookType.RANGE_SHORT}:
            if self.range_bounds is None:
                raise ValueError("range playbooks require range_bounds")
        if self.playbook == PlaybookType.RANGE_LONG and self.side != Side.LONG:
            raise ValueError("range_long requires side=long")
        if self.playbook == PlaybookType.RANGE_SHORT and self.side != Side.SHORT:
            raise ValueError("range_short requires side=short")
        return self
