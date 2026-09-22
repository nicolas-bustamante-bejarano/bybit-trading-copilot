from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.domain.journal import (
    ExecutionEventCreate,
    ReviewCreate,
    SnapshotCreate,
    TradePlanCreate,
    TradePlanPatch,
)
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import (
    DecisionSnapshotRow,
    ExecutionEventRow,
    ExecutionRuleRow,
    TradePlanRow,
    TradeReviewRow,
)
from trading_copilot.persistence.repository import TradePlanRepository

router = APIRouter(prefix="/trade-plans", tags=["trade journal"])


def dump(row):
    return {column.name: getattr(row, column.key) for column in row.__table__.columns}


async def require_plan(session: AsyncSession, plan_id: str):
    plan = await session.get(TradePlanRow, plan_id)
    if plan is None:
        raise HTTPException(404, "Trade plan not found")
    return plan


@router.post("", status_code=201)
async def create_plan(body: TradePlanCreate, session: AsyncSession = Depends(get_session)):
    data = body.model_dump(exclude={"execution_rules", "target_ladder"})
    data["side"] = body.side.value
    data["symbol"] = body.symbol.upper()
    data["target_ladder"] = [target.model_dump() for target in body.target_ladder]
    plan = await TradePlanRepository(session).add(TradePlanRow(**data))
    for rule in body.execution_rules:
        session.add(ExecutionRuleRow(trade_plan_id=plan.id, **rule.model_dump(mode="json")))
    await session.commit()
    return dump(plan)


@router.get("")
async def list_plans(
    symbol: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    return [dump(row) for row in await TradePlanRepository(session).list(symbol, status)]


@router.get("/{plan_id}")
async def get_plan(plan_id: str, session: AsyncSession = Depends(get_session)):
    plan = await require_plan(session, plan_id)
    result = dump(plan)
    result["execution_rules"] = [
        dump(x)
        for x in (
            await session.scalars(
                select(ExecutionRuleRow)
                .where(ExecutionRuleRow.trade_plan_id == plan_id)
                .order_by(ExecutionRuleRow.ordering)
            )
        ).all()
    ]
    return result


@router.patch("/{plan_id}")
async def patch_plan(
    plan_id: str, body: TradePlanPatch, session: AsyncSession = Depends(get_session)
):
    plan = await require_plan(session, plan_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        if key == "target_ladder" and value is not None:
            value = [x.model_dump() if hasattr(x, "model_dump") else x for x in value]
        setattr(plan, key, value)
    await session.commit()
    await session.refresh(plan)
    return dump(plan)


@router.post("/{plan_id}/snapshots", status_code=201)
async def create_snapshot(
    plan_id: str, body: SnapshotCreate, session: AsyncSession = Depends(get_session)
):
    plan = await require_plan(session, plan_id)
    if body.symbol.upper() != plan.symbol:
        raise HTTPException(422, "Snapshot symbol must match the trade plan")
    row = DecisionSnapshotRow(trade_plan_id=plan_id, **body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return dump(row)


@router.get("/{plan_id}/snapshots")
async def list_snapshots(plan_id: str, session: AsyncSession = Depends(get_session)):
    await require_plan(session, plan_id)
    rows = await session.scalars(
        select(DecisionSnapshotRow)
        .where(DecisionSnapshotRow.trade_plan_id == plan_id)
        .order_by(DecisionSnapshotRow.timestamp)
    )
    return [dump(x) for x in rows.all()]


@router.post("/{plan_id}/execution-events", status_code=201)
async def create_event(
    plan_id: str, body: ExecutionEventCreate, session: AsyncSession = Depends(get_session)
):
    plan = await require_plan(session, plan_id)
    if body.symbol.upper() != plan.symbol:
        raise HTTPException(422, "Execution event symbol must match the trade plan")
    row = ExecutionEventRow(trade_plan_id=plan_id, **body.model_dump())
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Bybit execution ID already recorded") from exc
    await session.refresh(row)
    return dump(row)


@router.get("/{plan_id}/execution-events")
async def list_events(plan_id: str, session: AsyncSession = Depends(get_session)):
    await require_plan(session, plan_id)
    rows = await session.scalars(
        select(ExecutionEventRow)
        .where(ExecutionEventRow.trade_plan_id == plan_id)
        .order_by(ExecutionEventRow.timestamp)
    )
    return [dump(x) for x in rows.all()]


@router.post("/{plan_id}/review", status_code=201)
async def create_review(
    plan_id: str, body: ReviewCreate, session: AsyncSession = Depends(get_session)
):
    await require_plan(session, plan_id)
    row = TradeReviewRow(trade_plan_id=plan_id, **body.model_dump())
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Trade review already exists") from exc
    await session.refresh(row)
    return dump(row)


@router.get("/{plan_id}/review")
async def get_review(plan_id: str, session: AsyncSession = Depends(get_session)):
    await require_plan(session, plan_id)
    row = await session.scalar(
        select(TradeReviewRow).where(TradeReviewRow.trade_plan_id == plan_id)
    )
    if row is None:
        raise HTTPException(404, "Trade review not found")
    return dump(row)
