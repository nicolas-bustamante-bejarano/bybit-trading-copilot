from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.domain.workspace import StructureCreate, StructurePatch
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import ChartStructureRow
from trading_copilot.services.scanner_invalidation import (
    invalidate_chart_structure_dependents,
)

router = APIRouter(prefix="/chart-structures", tags=["workspace"])


def dump(row: ChartStructureRow) -> dict:
    return {column.name: getattr(row, column.key) for column in row.__table__.columns}


@router.get("")
async def list_structures(symbol: str | None = None, session: AsyncSession = Depends(get_session)):
    query = select(ChartStructureRow).order_by(ChartStructureRow.created_at)
    if symbol:
        query = query.where(ChartStructureRow.symbol == symbol.upper())
    return [dump(row) for row in (await session.scalars(query)).all()]


@router.post("", status_code=201)
async def create_structure(body: StructureCreate, session: AsyncSession = Depends(get_session)):
    data = body.model_dump()
    data["symbol"] = body.symbol.upper()
    row = ChartStructureRow(**data)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return dump(row)


@router.patch("/{structure_id}")
async def patch_structure(
    structure_id: str, body: StructurePatch, session: AsyncSession = Depends(get_session)
):
    row = await session.get(ChartStructureRow, structure_id)
    if row is None:
        raise HTTPException(404, "Chart structure not found")
    updates = body.model_dump(exclude_unset=True)
    material_fields = {
        "timeframe",
        "lower_price",
        "upper_price",
        "anchor_one_time",
        "anchor_one_price",
        "anchor_two_time",
        "anchor_two_price",
        "active",
    }
    changed_materially = any(
        key in material_fields and getattr(row, key) != value for key, value in updates.items()
    )
    for key, value in updates.items():
        setattr(row, key, value)
    try:
        StructureCreate(
            symbol=row.symbol, timeframe=row.timeframe, structure_type=row.structure_type,
            label=row.label, lower_price=row.lower_price, upper_price=row.upper_price,
            anchor_one_time=row.anchor_one_time, anchor_one_price=row.anchor_one_price,
            anchor_two_time=row.anchor_two_time, anchor_two_price=row.anchor_two_price,
            active=row.active,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if changed_materially:
        await invalidate_chart_structure_dependents(session, structure_id=row.id)
    await session.commit()
    await session.refresh(row)
    return dump(row)


@router.delete("/{structure_id}", status_code=204)
async def delete_structure(structure_id: str, session: AsyncSession = Depends(get_session)):
    row = await session.get(ChartStructureRow, structure_id)
    if row is None:
        raise HTTPException(404, "Chart structure not found")
    await invalidate_chart_structure_dependents(session, structure_id=row.id)
    await session.delete(row)
    await session.commit()
