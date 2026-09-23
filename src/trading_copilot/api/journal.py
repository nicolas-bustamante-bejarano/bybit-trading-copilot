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
from trading_copilot.domain.workspace import FibDefinitionInput, RangeDefinitionInput
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import (
    DecisionSnapshotRow,
    ExecutionEventRow,
    ExecutionRuleRow,
    FibDefinitionRow,
    RangeDefinitionRow,
    TradePlanRow,
    TradeReviewRow,
)
from trading_copilot.persistence.repository import TradePlanRepository
from trading_copilot.services.indicators import fib_retracements

router = APIRouter(prefix="/trade-plans", tags=["trade journal"])


def dump(row):
    return {
        column.name: row.metadata_json if column.name == "metadata" else getattr(row, column.key)
        for column in row.__table__.columns
    }


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
    data["target_ladder"] = [target.model_dump(mode="json") for target in body.target_ladder]
    plan = TradePlanRow(**data)
    session.add(plan)
    try:
        await session.flush()
        for rule in body.execution_rules:
            session.add(ExecutionRuleRow(trade_plan_id=plan.id, **rule.model_dump(mode="json")))
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            409, "Trade plan or execution rule conflicts with existing data"
        ) from exc
    except Exception:
        await session.rollback()
        raise
    await session.refresh(plan)
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
    updates = body.model_dump(exclude_unset=True)
    if (
        plan.lifecycle_status == "ACTIVE"
        and any(key in updates for key in ("symbol", "side"))
        and updates.get("lifecycle_status") != "DRAFT"
    ):
        raise HTTPException(409, "Return an active plan to DRAFT before changing symbol or side")
    for key, value in updates.items():
        if key == "target_ladder" and value is not None:
            value = [x.model_dump(mode="json") if hasattr(x, "model_dump") else x for x in value]
        if key == "side" and value is not None:
            value = value.value
        if key == "symbol" and value is not None:
            value = value.upper()
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
    data = body.model_dump()
    data["symbol"] = body.symbol.upper()
    row = DecisionSnapshotRow(trade_plan_id=plan_id, **data)
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
    if body.decision_snapshot_id is not None:
        snapshot = await session.get(DecisionSnapshotRow, body.decision_snapshot_id)
        if snapshot is None:
            raise HTTPException(422, "Decision snapshot does not exist")
        if snapshot.trade_plan_id != plan_id:
            raise HTTPException(422, "Decision snapshot belongs to a different trade plan")
    data = body.model_dump()
    data["symbol"] = body.symbol.upper()
    row = ExecutionEventRow(trade_plan_id=plan_id, **data)
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


@router.get("/{plan_id}/fib-definitions")
async def list_fibs(plan_id: str, session: AsyncSession = Depends(get_session)):
    await require_plan(session, plan_id)
    rows = await session.scalars(select(FibDefinitionRow).where(FibDefinitionRow.trade_plan_id == plan_id))
    return [
        dump(row) | {"levels": fib_retracements(float(row.swing_low), float(row.swing_high), row.direction.lower())}
        for row in rows
    ]


@router.put("/{plan_id}/fib-definition")
async def put_fib(plan_id: str, body: FibDefinitionInput, session: AsyncSession = Depends(get_session)):
    plan = await require_plan(session, plan_id)
    row = await session.scalar(select(FibDefinitionRow).where(FibDefinitionRow.trade_plan_id == plan_id))
    if row is None:
        row = FibDefinitionRow(trade_plan_id=plan_id, symbol=plan.symbol, **body.model_dump())
        session.add(row)
    else:
        for key, value in body.model_dump().items():
            setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return dump(row) | {"levels": fib_retracements(float(row.swing_low), float(row.swing_high), row.direction.lower())}


@router.get("/{plan_id}/range-definitions")
async def list_ranges(plan_id: str, session: AsyncSession = Depends(get_session)):
    await require_plan(session, plan_id)
    rows = await session.scalars(select(RangeDefinitionRow).where(RangeDefinitionRow.trade_plan_id == plan_id))
    return [dump(row) for row in rows]


@router.put("/{plan_id}/range-definition")
async def put_range(plan_id: str, body: RangeDefinitionInput, session: AsyncSession = Depends(get_session)):
    plan = await require_plan(session, plan_id)
    row = await session.scalar(select(RangeDefinitionRow).where(RangeDefinitionRow.trade_plan_id == plan_id))
    if row is None:
        row = RangeDefinitionRow(trade_plan_id=plan_id, symbol=plan.symbol, **body.model_dump())
        session.add(row)
    else:
        for key, value in body.model_dump().items():
            setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return dump(row)
