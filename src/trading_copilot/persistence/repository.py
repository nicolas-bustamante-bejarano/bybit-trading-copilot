from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.persistence.models import TradePlanRow


class TradePlanRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, plan: TradePlanRow) -> TradePlanRow:
        self.session.add(plan)
        await self.session.commit()
        await self.session.refresh(plan)
        return plan

    async def get(self, plan_id: str) -> TradePlanRow | None:
        return await self.session.get(TradePlanRow, plan_id)

    async def list(self, symbol: str | None = None, status: str | None = None):
        query = select(TradePlanRow).order_by(TradePlanRow.created_at.desc())
        if symbol:
            query = query.where(TradePlanRow.symbol == symbol.upper())
        if status:
            query = query.where(TradePlanRow.lifecycle_status == status)
        return list((await self.session.scalars(query)).all())

    async def commit(self):
        await self.session.commit()
