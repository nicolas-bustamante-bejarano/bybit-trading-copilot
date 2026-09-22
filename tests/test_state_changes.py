from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trading_copilot.domain.account import NormalizedAccount, NormalizedPosition
from trading_copilot.domain.models import Side
from trading_copilot.domain.position_coach import (
    CoachExecution,
    CoachExecutionState,
    CoachMarketContext,
    CoachPlan,
    CoachPosition,
    CoachRisk,
    EvidenceStatus,
    PositionCoach,
    RiskPolicyStatus,
)
from trading_copilot.main import app
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import (
    Base,
    CoachStateCursorRow,
    DecisionSnapshotRow,
    StateChangeEventRow,
    TradePlanRow,
)
from trading_copilot.services.state_change import (
    material_changes,
    persist_closed_position,
    persist_observation,
    project_coach_state,
)
from trading_copilot.services.state_change_monitor import StateChangeMonitor

NOW = datetime(2026, 9, 22, 14, 52, tzinfo=UTC)


def coach(
    *,
    execution="HOLD",
    add_allowed=False,
    risk="INDETERMINATE",
    reaction_status="MISSING",
    reaction=None,
    playbook="AT_LOCATION",
    warning=False,
    plan_id="plan-1",
    mark="600",
    confidence="PARTIAL",
):
    return PositionCoach(
        symbol="BNBUSDT",
        timestamp=NOW,
        side="LONG",
        position=CoachPosition(
            quantity=Decimal(1),
            average_entry=Decimal(590),
            mark_price=Decimal(mark) if mark is not None else None,
            lifecycle_history_complete=True,
            lifecycle_confidence=EvidenceStatus.CONFIRMED,
        ),
        plan=CoachPlan(
            status=EvidenceStatus.CONFIRMED if plan_id else EvidenceStatus.MISSING,
            trade_plan_id=plan_id,
        ),
        market_context=CoachMarketContext(
            status=EvidenceStatus.CONFIRMED,
            location="RANGE_LOW",
            location_status=EvidenceStatus.CONFIRMED,
            playbook_state=playbook,
            reaction=reaction,
            reaction_status=EvidenceStatus(reaction_status),
        ),
        risk=CoachRisk(
            account_equity=Decimal(10000),
            correlation_group="crypto_beta",
            known_group_risk_usdt=Decimal(100),
            policy_status=RiskPolicyStatus(risk),
        ),
        execution=CoachExecution(
            state=CoachExecutionState(execution),
            add_allowed=add_allowed,
            evidence_present=["planned location is present"],
            evidence_missing=["fresh directional reaction confirmation"] if not reaction else [],
            warnings=["Thesis warning level has been crossed"] if warning else [],
            next_conditions=["fresh directional order-flow reaction confirms"],
        ),
        confidence=EvidenceStatus(confidence),
    )


def plan():
    return TradePlanRow(
        id="plan-1",
        symbol="BNBUSDT",
        side="LONG",
        setup_type="RANGE_LONG",
        thesis="Range support",
        lifecycle_status="ACTIVE",
        hard_invalidation=Decimal(550),
        max_risk_percent=Decimal("0.02"),
    )


@pytest_asyncio.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/state.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_first_observation_is_silent_baseline(sessions):
    async with sessions() as session:
        session.add(plan())
        await session.commit()
        assert (
            await persist_observation(
                session, symbol="bnbusdt", state=project_coach_state(coach()), coach=coach()
            )
            is None
        )
        assert await session.get(CoachStateCursorRow, "BNBUSDT")
        assert await session.scalar(select(func.count()).select_from(StateChangeEventRow)) == 0
        assert await session.scalar(select(func.count()).select_from(DecisionSnapshotRow)) == 0


@pytest.mark.asyncio
async def test_unchanged_and_restart_create_no_history(sessions):
    state = project_coach_state(coach())
    async with sessions() as session:
        session.add(plan())
        await session.commit()
        await persist_observation(session, symbol="BNBUSDT", state=state, coach=coach())
    async with sessions() as restarted:
        assert (
            await persist_observation(restarted, symbol="BNBUSDT", state=state, coach=coach())
            is None
        )
        assert await restarted.scalar(select(func.count()).select_from(StateChangeEventRow)) == 0


@pytest.mark.asyncio
async def test_hold_to_add_creates_one_event_and_one_snapshot(sessions):
    before = coach()
    after = coach(
        execution="ADD",
        add_allowed=True,
        risk="PASS",
        reaction_status="CONFIRMED",
        reaction="buy_continuation",
        confidence="CONFIRMED",
    )
    async with sessions() as session:
        session.add(plan())
        await session.commit()
        await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(before), coach=before
        )
        event = await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(after), coach=after
        )
        assert event.event_type == "EXECUTION_STATE_CHANGED"
        assert event.summary == "HOLD → ADD"
        assert event.decision_snapshot_id is not None
        snapshot = await session.get(DecisionSnapshotRow, event.decision_snapshot_id)
        assert snapshot.action_considered == "ADD"
        assert snapshot.action_taken is None
        assert snapshot.state["source"] == "state_change_monitor"
        assert await session.scalar(select(func.count()).select_from(StateChangeEventRow)) == 1
        assert await session.scalar(select(func.count()).select_from(DecisionSnapshotRow)) == 1


@pytest.mark.parametrize(
    ("before", "after", "field"),
    [
        (coach(), coach(add_allowed=True), "add_allowed"),
        (coach(risk="PASS"), coach(risk="BREACH"), "risk_policy_status"),
        (coach(), coach(risk="PASS"), "risk_policy_status"),
        (
            coach(),
            coach(reaction_status="CONFIRMED", reaction="buy_continuation"),
            "reaction_status",
        ),
        (
            coach(reaction_status="CONFIRMED", reaction="buy_continuation"),
            coach(reaction_status="STALE", reaction="buy_continuation"),
            "reaction_status",
        ),
        (coach(), coach(playbook="CONFIRMATION_DEVELOPING"), "playbook_state"),
        (coach(), coach(warning=True), "warning_active"),
        (coach(), coach(execution="INVALIDATE"), "invalidation_breached"),
        (coach(plan_id=None), coach(), "trade_plan_id"),
        (coach(), coach(plan_id=None), "trade_plan_id"),
    ],
)
def test_material_projection_detects_required_transitions(before, after, field):
    assert field in {
        item["field"]
        for item in material_changes(project_coach_state(before), project_coach_state(after))
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("plan_id,mark", [(None, "600"), ("plan-1", None)])
async def test_transition_without_eligible_snapshot(plan_id, mark, sessions):
    before = coach(plan_id=plan_id, mark=mark)
    after = coach(plan_id=plan_id, mark=mark, risk="PASS")
    async with sessions() as session:
        if plan_id:
            session.add(plan())
            await session.commit()
        await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(before), coach=before
        )
        event = await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(after), coach=after
        )
        assert event is not None
        assert event.decision_snapshot_id is None
        assert await session.scalar(select(func.count()).select_from(DecisionSnapshotRow)) == 0


@pytest.mark.asyncio
async def test_multiple_changes_produce_single_event_and_snapshot(sessions):
    before = coach()
    after = coach(
        execution="ADD",
        add_allowed=True,
        risk="PASS",
        reaction_status="CONFIRMED",
        reaction="buy_continuation",
    )
    async with sessions() as session:
        session.add(plan())
        await session.commit()
        await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(before), coach=before
        )
        await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(after), coach=after
        )
        assert await session.scalar(select(func.count()).select_from(StateChangeEventRow)) == 1
        assert await session.scalar(select(func.count()).select_from(DecisionSnapshotRow)) == 1


@pytest.mark.asyncio
async def test_open_absent_and_reopen_are_observed_without_fake_prices(sessions):
    current = coach(plan_id=None)
    async with sessions() as session:
        await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(current), coach=current
        )
        closed = await persist_closed_position(session, "BNBUSDT")
        assert closed.event_type == "POSITION_CLOSED"
        assert closed.decision_snapshot_id is None
        reopened = await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(current), coach=current
        )
        assert reopened.event_type == "POSITION_OPENED"
        assert reopened.decision_snapshot_id is None


@pytest.mark.asyncio
async def test_repeated_same_evaluation_is_idempotent(sessions):
    before, after = coach(plan_id=None), coach(plan_id=None, risk="PASS")
    async with sessions() as session:
        await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(before), coach=before
        )
        first = await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(after), coach=after
        )
        second = await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(after), coach=after
        )
        assert first is not None and second is None
        assert await session.scalar(select(func.count()).select_from(StateChangeEventRow)) == 1


@pytest.mark.asyncio
async def test_transition_rolls_back_snapshot_event_and_cursor_together(sessions, monkeypatch):
    before, after = coach(), coach(risk="PASS")
    async with sessions() as session:
        session.add(plan())
        await session.commit()
        await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(before), coach=before
        )
        original_commit = session.commit
        monkeypatch.setattr(session, "commit", AsyncMock(side_effect=RuntimeError("write failed")))
        with pytest.raises(RuntimeError, match="write failed"):
            await persist_observation(
                session, symbol="BNBUSDT", state=project_coach_state(after), coach=after
            )
        monkeypatch.setattr(session, "commit", original_commit)
        cursor = await session.get(CoachStateCursorRow, "BNBUSDT")
        assert cursor.version == 1
        assert await session.scalar(select(func.count()).select_from(StateChangeEventRow)) == 0
        assert await session.scalar(select(func.count()).select_from(DecisionSnapshotRow)) == 0


def normalized_account(*positions):
    return NormalizedAccount(
        equity_usdt=10000,
        max_group_risk_pct=0.02,
        risk_budget_usdt=200,
        total_structural_risk_usdt=0,
        total_structural_risk_pct=0,
        within_risk_budget=True,
        positions=list(positions),
    )


def normalized_position():
    return NormalizedPosition(
        symbol="BNBUSDT", side=Side.LONG, quantity=1, average_entry=590, mark_price=600
    )


@pytest.mark.asyncio
async def test_account_failure_does_not_mark_position_closed(sessions):
    current = coach(plan_id=None)
    async with sessions() as session:
        await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(current), coach=current
        )

    async def fail_account():
        raise RuntimeError("Bybit unavailable")

    async def unused_composer(*_):
        raise AssertionError("composer should not run")

    monitor = StateChangeMonitor(
        enabled=True,
        interval_seconds=5,
        sessions=sessions,
        fetch_account=fail_account,
        compose_coach=unused_composer,
    )
    await monitor.run_cycle()
    async with sessions() as session:
        cursor = await session.get(CoachStateCursorRow, "BNBUSDT")
        assert cursor.position_open is True
        assert await session.scalar(select(func.count()).select_from(StateChangeEventRow)) == 0
    assert "Account refresh failed" in monitor.last_error


@pytest.mark.asyncio
async def test_coach_failure_does_not_overwrite_cursor(sessions):
    current = coach(plan_id=None)
    async with sessions() as session:
        await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(current), coach=current
        )

    async def fetch_account():
        return normalized_account(normalized_position())

    async def fail_coach(*_):
        raise ValueError("Active trade plan side does not match the live position side")

    monitor = StateChangeMonitor(
        enabled=True,
        interval_seconds=5,
        sessions=sessions,
        fetch_account=fetch_account,
        compose_coach=fail_coach,
    )
    await monitor.run_cycle()
    async with sessions() as session:
        cursor = await session.get(CoachStateCursorRow, "BNBUSDT")
        assert cursor.version == 1
        assert cursor.state == project_coach_state(current)
        assert await session.scalar(select(func.count()).select_from(StateChangeEventRow)) == 0
    assert "side does not match" in monitor.last_error


@pytest.mark.asyncio
async def test_state_change_api_order_filter_limit_and_status(sessions):
    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override
    async with sessions() as session:
        first = coach(plan_id=None)
        await persist_observation(
            session, symbol="BNBUSDT", state=project_coach_state(first), coach=first
        )
        await persist_observation(
            session,
            symbol="BNBUSDT",
            state=project_coach_state(coach(plan_id=None, risk="PASS")),
            coach=first,
            observed_at=NOW,
        )
        eth = project_coach_state(first)
        await persist_observation(session, symbol="ETHUSDT", state=eth, coach=None)
        eth2 = dict(eth)
        eth2["risk_policy_status"] = "BREACH"
        await persist_observation(
            session, symbol="ETHUSDT", state=eth2, coach=None, observed_at=NOW.replace(minute=53)
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/state-changes", params={"limit": 1})
        assert response.status_code == 200
        assert response.json()[0]["symbol"] == "ETHUSDT"
        assert (await client.get("/state-changes", params={"symbol": "bnbusdt"})).json()[0][
            "symbol"
        ] == "BNBUSDT"
        assert (await client.get("/state-changes", params={"limit": 201})).status_code == 422
        status = (await client.get("/state-change-monitor/status")).json()
        assert "enabled" in status and "last_error" in status
        assert "api_key" not in status and "secret" not in status
    app.dependency_overrides.clear()
