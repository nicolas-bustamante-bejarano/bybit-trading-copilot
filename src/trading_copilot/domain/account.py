from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from trading_copilot.domain.models import Side


class NormalizedFill(BaseModel):
    exec_id: str | None = None
    order_id: str | None = None
    symbol: str
    side: Side
    price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    exec_time_ms: int | None = None
    fee_usdt: float | None = None


class NormalizedOrder(BaseModel):
    order_id: str | None = None
    symbol: str
    side: Side
    order_type: str | None = None
    quantity: float = Field(ge=0)
    price: float | None = None
    trigger_price: float | None = None
    reduce_only: bool = False
    status: str | None = None
    purpose: Literal["stop", "take_profit", "reduce", "entry", "unknown"] = "unknown"


class NormalizedPosition(BaseModel):
    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    average_entry: float = Field(gt=0)
    mark_price: float | None = None
    leverage: float | None = None
    liquidation_price: float | None = None
    unrealized_pnl: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    correlation_group: str = "crypto_beta"
    structural_risk_usdt: float | None = None
    protected: bool = False


class NormalizedAccount(BaseModel):
    equity_usdt: float = Field(ge=0)
    available_balance_usdt: float | None = None
    max_group_risk_pct: float = Field(gt=0, le=1)
    risk_budget_usdt: float = Field(ge=0)
    total_structural_risk_usdt: float = Field(ge=0)
    total_structural_risk_pct: float = Field(ge=0)
    within_risk_budget: bool
    unprotected_symbols: list[str] = Field(default_factory=list)
    positions: list[NormalizedPosition] = Field(default_factory=list)
    open_orders: list[NormalizedOrder] = Field(default_factory=list)
    recent_fills: list[NormalizedFill] = Field(default_factory=list)
