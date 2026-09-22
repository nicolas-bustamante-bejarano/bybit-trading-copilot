from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from trading_copilot.domain.models import OpenRiskPosition, Side


class ExecutionStage(StrEnum):
    WAIT = "wait"
    PROBE = "probe"
    ADD = "add"
    HOLD = "hold"
    REDUCE = "reduce"
    EXIT = "exit"
    INVALIDATE = "invalidate"


class PositionLeg(BaseModel):
    entry: float = Field(gt=0)
    quantity: float = Field(gt=0)


class TargetLevel(BaseModel):
    price: float = Field(gt=0)
    close_fraction: float = Field(gt=0, le=1)
    label: str | None = None


class ExecutionPlanRequest(BaseModel):
    symbol: str = Field(min_length=1)
    side: Side
    account_equity: float = Field(gt=0)
    max_group_risk_pct: float = Field(default=0.02, gt=0, le=1)
    hard_stop: float = Field(gt=0)
    thesis_warning_level: float | None = Field(default=None, gt=0)
    correlation_group: str = "crypto_beta"
    stage: ExecutionStage = ExecutionStage.HOLD
    current_legs: list[PositionLeg] = Field(default_factory=list)
    targets: list[TargetLevel] = Field(default_factory=list)
    other_correlated_positions: list[OpenRiskPosition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_target_allocations(self) -> "ExecutionPlanRequest":
        allocated = sum(target.close_fraction for target in self.targets)
        if allocated > 1.0000001:
            raise ValueError("target close fractions cannot exceed 1.0 in total")
        return self


class AddProjectionRequest(BaseModel):
    plan: ExecutionPlanRequest
    add_price: float = Field(gt=0)
    add_quantity: float = Field(gt=0)
    confirmation_label: str = Field(min_length=1)
    confirmation_met: bool = False
