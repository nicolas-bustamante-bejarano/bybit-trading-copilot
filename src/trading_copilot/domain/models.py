from enum import StrEnum

from pydantic import BaseModel, Field


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"


class Regime(StrEnum):
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    RANGE = "range"
    TRANSITION = "transition"


class SetupState(StrEnum):
    NO_SETUP = "no_setup"
    CONTEXT_VALID = "context_valid"
    APPROACHING_LOCATION = "approaching_location"
    AT_LOCATION = "at_location"
    CONFIRMATION_DEVELOPING = "confirmation_developing"
    WAITING_FOR_TRIGGER = "waiting_for_trigger"
    TRIGGERED = "triggered"
    READY = "ready"
    INVALIDATED = "invalidated"


class PositionRiskRequest(BaseModel):
    account_equity: float = Field(gt=0)
    max_risk_pct: float = Field(gt=0, le=1)
    side: Side
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)


class PositionRiskResult(BaseModel):
    risk_budget: float
    risk_per_unit: float
    max_quantity: float


class OpenRiskPosition(BaseModel):
    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    correlation_group: str = "crypto_beta"


class PortfolioRiskRequest(BaseModel):
    account_equity: float = Field(gt=0)
    max_portfolio_risk_pct: float = Field(gt=0, le=1)
    positions: list[OpenRiskPosition]


class FibRequest(BaseModel):
    swing_low: float = Field(gt=0)
    swing_high: float = Field(gt=0)
    direction: Side
