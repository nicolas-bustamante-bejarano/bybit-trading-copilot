from decimal import ROUND_DOWN, Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class StructureCreate(BaseModel):
    symbol: str = Field(min_length=1)
    timeframe: str = "1h"
    structure_type: Literal["HORIZONTAL_ZONE", "TRENDLINE"]
    label: str | None = None
    lower_price: Decimal | None = Field(default=None, gt=0)
    upper_price: Decimal | None = Field(default=None, gt=0)
    anchor_one_time: int | None = Field(default=None, gt=0)
    anchor_one_price: Decimal | None = Field(default=None, gt=0)
    anchor_two_time: int | None = Field(default=None, gt=0)
    anchor_two_price: Decimal | None = Field(default=None, gt=0)
    active: bool = True

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol must not be empty")
        return normalized

    @model_validator(mode="after")
    def valid_shape(self):
        prices = (
            self.lower_price,
            self.upper_price,
            self.anchor_one_price,
            self.anchor_two_price,
        )
        if any(value is not None and not value.is_finite() for value in prices):
            raise ValueError("structure prices must be finite")
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
        if (
            self.structure_type == "TRENDLINE"
            and self.anchor_one_time == self.anchor_two_time
        ):
            raise ValueError("trendline anchor timestamps must be distinct")
        return self


class StructurePatch(BaseModel):
    timeframe: str | None = None
    label: str | None = None
    lower_price: Decimal | None = Field(default=None, gt=0)
    upper_price: Decimal | None = Field(default=None, gt=0)
    anchor_one_time: int | None = Field(default=None, gt=0)
    anchor_one_price: Decimal | None = Field(default=None, gt=0)
    anchor_two_time: int | None = Field(default=None, gt=0)
    anchor_two_price: Decimal | None = Field(default=None, gt=0)
    active: bool | None = None


class FibDefinitionInput(BaseModel):
    direction: Literal["LONG", "SHORT"]
    swing_low: Decimal = Field(gt=0)
    swing_high: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if not self.swing_low.is_finite() or not self.swing_high.is_finite():
            raise ValueError("Fib anchors must be finite")
        if self.swing_low >= self.swing_high:
            raise ValueError("swing_low must be less than swing_high")
        return self


class RangeDefinitionInput(BaseModel):
    range_low: Decimal = Field(gt=0)
    range_high: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if not self.range_low.is_finite() or not self.range_high.is_finite():
            raise ValueError("range bounds must be finite")
        if self.range_low >= self.range_high:
            raise ValueError("range_low must be less than range_high")
        return self


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
