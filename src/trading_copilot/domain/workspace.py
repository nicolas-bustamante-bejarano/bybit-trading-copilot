from decimal import ROUND_DOWN, Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class StructureCreate(BaseModel):
    symbol: str
    timeframe: str = "1h"
    structure_type: Literal["HORIZONTAL_ZONE", "TRENDLINE"]
    label: str | None = None
    lower_price: Decimal | None = None
    upper_price: Decimal | None = None
    anchor_one_time: int | None = None
    anchor_one_price: Decimal | None = None
    anchor_two_time: int | None = None
    anchor_two_price: Decimal | None = None
    active: bool = True

    @model_validator(mode="after")
    def valid_shape(self):
        if self.structure_type == "HORIZONTAL_ZONE" and (
            self.lower_price is None or self.upper_price is None
        ):
            raise ValueError("horizontal zones require lower_price and upper_price")
        if self.structure_type == "TRENDLINE" and (
            self.anchor_one_time is None
            or self.anchor_one_price is None
            or self.anchor_two_time is None
            or self.anchor_two_price is None
        ):
            raise ValueError("trendlines require two timestamp and price anchors")
        return self


class StructurePatch(BaseModel):
    timeframe: str | None = None
    label: str | None = None
    lower_price: Decimal | None = None
    upper_price: Decimal | None = None
    anchor_one_time: int | None = None
    anchor_one_price: Decimal | None = None
    anchor_two_time: int | None = None
    anchor_two_price: Decimal | None = None
    active: bool | None = None


class SizingStage(BaseModel):
    name: Literal["PROBE", "ADD_1", "ADD_2", "FULL_SIZE"]
    allocation: Decimal = Field(gt=0, le=1)
    required_evidence: list[str] = Field(default_factory=list)


class SizingRequest(BaseModel):
    symbol: str
    side: Literal["LONG", "SHORT"]
    entry: Decimal = Field(gt=0)
    hard_invalidation: Decimal = Field(gt=0)
    max_risk_percent: Decimal = Field(gt=0, le=1)
    correlation_group: str | None = Field(default=None, min_length=1)
    trade_plan_id: str | None = None
    stages: list[SizingStage] = Field(default_factory=lambda: [SizingStage(name="PROBE", allocation=Decimal(1))])
    current_evidence: list[str] = Field(default_factory=list)
    prior_evidence: list[str] = Field(default_factory=list)
    prior_stage_baseline_trusted: bool = False
    completed_stage_count: int = Field(default=0, ge=0)
    fee_bps: Decimal = Field(default=Decimal(6), ge=0)
    slippage_bps: Decimal = Field(default=Decimal(3), ge=0)
    requested_leverage: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_stop_and_stages(self):
        if self.side == "LONG" and self.hard_invalidation >= self.entry:
            raise ValueError("long invalidation must be below entry")
        if self.side == "SHORT" and self.hard_invalidation <= self.entry:
            raise ValueError("short invalidation must be above entry")
        if sum((s.allocation for s in self.stages), Decimal()) > 1:
            raise ValueError("stage allocations cannot exceed 100%")
        return self


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step
