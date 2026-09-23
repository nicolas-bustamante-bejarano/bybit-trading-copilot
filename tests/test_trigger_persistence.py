from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trading_copilot.domain.models import Side
from trading_copilot.domain.scanner import ScannerSetupType
from trading_copilot.domain.trigger import (
    TriggerArmSource,
    TriggerPattern,
    TriggerReferenceSource,
    TriggerResult,
    TriggerState,
)
from trading_copilot.persistence.models import (
    Base,
    ScannerTransitionRow,
    TriggerAttemptRow,
    TriggerTransitionRow,
    WatchedSetupRow,
)
from trading_copilot.services.trigger_context import TriggerContextResolution
from trading_copilot.services.trigger_persistence import (
    TriggerPersistenceConsistencyError,
    persist_trigger_result,
)

ARMED_AT = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
EVALUATED_AT = ARMED_AT + timedelta(minutes=10)


@pytest_asyncio.fixture
async def database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trigger.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine, sessions
    finally:
        await engine.dispose()


def context(
    *,
    watched_setup_id="setup-1",
    arm_key="TRANSITION:arm-a",
    arm_transition_id="arm-a",
    reference_level=100.0,
    armed_at=ARMED_AT,
    symbol="BTCUSDT",
    setup_type=ScannerSetupType.RANGE_LONG,
):
    side = Side.LONG if setup_type.value.endswith("_LONG") else Side.SHORT
    return TriggerContextResolution(
        symbol=symbol,
        watched_setup_id=watched_setup_id,
        setup_type=setup_type,
        side=side,
        eligible=True,
        armed_at=armed_at,
        arm_source=TriggerArmSource.ARM_TRANSITION,
        arm_key=arm_key,
        arm_transition_id=arm_transition_id,
        reference_level=reference_level,
        reference_source=TriggerReferenceSource.RANGE_LOW,
        armed_state={"location": {"range_low": reference_level}},
        retest_tolerance_bps=20,
        failure_tolerance_bps=25,
        reference_metadata={"boundary": "range_low"},
    )


def result(
    state=TriggerState.RECLAIMED,
    *,
    evaluated_at=EVALUATED_AT,
    reaction_state=None,
    symbol="BTCUSDT",
    setup_type=ScannerSetupType.RANGE_LONG,
    side=Side.LONG,
    armed_at=ARMED_AT,
    reference_level=100.0,
):
    return TriggerResult(
        symbol=symbol,
        setup_type=setup_type,
        side=side,
        pattern=TriggerPattern.DEVIATION_RECLAIM,
        state=state,
        reference_level=reference_level,
        armed_at=armed_at,
        evaluated_at=evaluated_at,
        trigger_confirmed=state == TriggerState.CONFIRMED,
        anchor_bar_end_ms=1_000,
        confirmation_bar_end_ms=2_000 if state == TriggerState.CONFIRMED else None,
        local_15m_acceptance=state == TriggerState.CONFIRMED,
        reaction_state=reaction_state,
        reaction_supportive=True if reaction_state else None,
        anchor_high=101,
        anchor_low=99,
        anchor_close=100.5,
        confirmation_close=102 if state == TriggerState.CONFIRMED else None,
        evidence_present=["DEVIATION_RECLAIM"],
        evidence_missing=[],
        blocking_reasons=[],
        next_conditions=[],
    )


async def seed_watched(session, setup_id="setup-1", symbol="BTCUSDT"):
    row = WatchedSetupRow(
        id=setup_id,
        symbol=symbol,
        setup_type=ScannerSetupType.RANGE_LONG.value,
        status="TRIGGER_ARMED",
        state={"preserved": True},
        version=2,
        created_at=ARMED_AT,
    )
    session.add(row)
    await session.commit()
    return row


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", [TriggerState.WAITING, TriggerState.RECLAIMED, TriggerState.CONFIRMED]
)
async def test_first_observation_is_silent_version_one(database, state):
    _, sessions = database
    async with sessions() as session:
        await seed_watched(session)
        attempt = await persist_trigger_result(
            session, context=context(), result=result(state)
        )
        transition_count = await session.scalar(
            select(func.count(TriggerTransitionRow.id))
        )

    assert attempt.version == 1
    assert attempt.state == state.value
    assert attempt.result == result(state).model_dump(mode="json")
    assert aware(attempt.first_evaluated_at) == EVALUATED_AT
    assert aware(attempt.last_evaluated_at) == EVALUATED_AT
    assert transition_count == 0


@pytest.mark.asyncio
async def test_same_state_updates_result_without_version_or_transition(database):
    _, sessions = database
    later = EVALUATED_AT + timedelta(minutes=5)
    async with sessions() as session:
        await seed_watched(session)
        initial = await persist_trigger_result(
            session, context=context(), result=result()
        )
        first_evaluated_at = initial.first_evaluated_at
        updated_result = result(evaluated_at=later, reaction_state="sell_absorption")
        updated = await persist_trigger_result(
            session, context=context(), result=updated_result
        )
        transition_count = await session.scalar(
            select(func.count(TriggerTransitionRow.id))
        )

    assert updated.version == 1
    assert updated.result == updated_result.model_dump(mode="json")
    assert updated.first_evaluated_at == first_evaluated_at
    assert aware(updated.last_evaluated_at) == later
    assert transition_count == 0


@pytest.mark.asyncio
async def test_material_state_changes_increment_once_and_snapshot_results(database):
    _, sessions = database
    reclaimed = result(TriggerState.RECLAIMED)
    developing = result(
        TriggerState.DEVELOPING, evaluated_at=EVALUATED_AT + timedelta(minutes=5)
    )
    confirmed = result(
        TriggerState.CONFIRMED, evaluated_at=EVALUATED_AT + timedelta(minutes=10)
    )
    async with sessions() as session:
        await seed_watched(session)
        await persist_trigger_result(session, context=context(), result=reclaimed)
        await persist_trigger_result(session, context=context(), result=developing)
        attempt = await persist_trigger_result(
            session, context=context(), result=confirmed
        )
        transitions = list(
            await session.scalars(
                select(TriggerTransitionRow).order_by(TriggerTransitionRow.version)
            )
        )

    assert attempt.version == 3
    assert [item.version for item in transitions] == [2, 3]
    assert transitions[0].from_state == TriggerState.RECLAIMED.value
    assert transitions[0].to_state == TriggerState.DEVELOPING.value
    assert transitions[0].result_before == reclaimed.model_dump(mode="json")
    assert transitions[0].result_after == developing.model_dump(mode="json")
    assert transitions[1].result_after == confirmed.model_dump(mode="json")


@pytest.mark.asyncio
async def test_failure_and_recovery_are_all_preserved_without_scanner_mutation(database):
    _, sessions = database
    states = [
        TriggerState.RETEST_HELD,
        TriggerState.FAILED,
        TriggerState.RETEST_HELD,
        TriggerState.CONFIRMED,
    ]
    async with sessions() as session:
        watched_row = await seed_watched(session)
        for index, state in enumerate(states):
            await persist_trigger_result(
                session,
                context=context(),
                result=result(
                    state, evaluated_at=EVALUATED_AT + timedelta(minutes=5 * index)
                ),
            )
        attempt = await session.scalar(select(TriggerAttemptRow))
        transitions = list(
            await session.scalars(
                select(TriggerTransitionRow).order_by(TriggerTransitionRow.version)
            )
        )
        unchanged = await session.get(WatchedSetupRow, watched_row.id)
        scanner_transition_count = await session.scalar(
            select(func.count(ScannerTransitionRow.id))
        )

    assert attempt.version == 4
    assert [item.to_state for item in transitions] == [
        TriggerState.FAILED.value,
        TriggerState.RETEST_HELD.value,
        TriggerState.CONFIRMED.value,
    ]
    assert unchanged.status == "TRIGGER_ARMED"
    assert unchanged.state == {"preserved": True}
    assert scanner_transition_count == 0


@pytest.mark.asyncio
async def test_restart_reuses_attempt_then_same_state_is_idempotent(database):
    _, sessions = database
    async with sessions() as session:
        await seed_watched(session)
        created = await persist_trigger_result(
            session, context=context(), result=result(TriggerState.RECLAIMED)
        )
        attempt_id = created.id

    developing = result(
        TriggerState.DEVELOPING, evaluated_at=EVALUATED_AT + timedelta(minutes=5)
    )
    async with sessions() as session:
        changed = await persist_trigger_result(
            session, context=context(), result=developing
        )
    async with sessions() as session:
        unchanged = await persist_trigger_result(
            session,
            context=context(),
            result=result(
                TriggerState.DEVELOPING,
                evaluated_at=EVALUATED_AT + timedelta(minutes=10),
            ),
        )
        transition_count = await session.scalar(
            select(func.count(TriggerTransitionRow.id))
        )

    assert changed.id == unchanged.id == attempt_id
    assert unchanged.version == 2
    assert transition_count == 1


@pytest.mark.asyncio
async def test_rearm_creates_independent_attempt(database):
    _, sessions = database
    async with sessions() as session:
        await seed_watched(session)
        first = await persist_trigger_result(
            session, context=context(), result=result(TriggerState.RECLAIMED)
        )
        second_context = replace(
            context(),
            arm_key="TRANSITION:arm-b",
            arm_transition_id="arm-b",
            armed_at=ARMED_AT + timedelta(hours=1),
        )
        second_result = result(
            TriggerState.WAITING, armed_at=ARMED_AT + timedelta(hours=1)
        )
        second = await persist_trigger_result(
            session, context=second_context, result=second_result
        )
        attempts = list(
            await session.scalars(select(TriggerAttemptRow).order_by(TriggerAttemptRow.arm_key))
        )

    assert first.id != second.id
    assert [item.arm_key for item in attempts] == [
        "TRANSITION:arm-a",
        "TRANSITION:arm-b",
    ]
    assert [item.version for item in attempts] == [1, 1]
    assert attempts[0].state == TriggerState.RECLAIMED.value


@pytest.mark.asyncio
async def test_duplicate_attempt_identity_is_rejected_by_database(database):
    _, sessions = database
    async with sessions() as session:
        await seed_watched(session)
        persisted = await persist_trigger_result(
            session, context=context(), result=result()
        )
        session.add(
            TriggerAttemptRow(
                id="duplicate",
                watched_setup_id=persisted.watched_setup_id,
                symbol=persisted.symbol,
                setup_type=persisted.setup_type,
                arm_key=persisted.arm_key,
                arm_source=persisted.arm_source,
                arm_transition_id=persisted.arm_transition_id,
                armed_at=persisted.armed_at,
                reference_level=persisted.reference_level,
                reference_source=persisted.reference_source,
                reference_metadata={},
                retest_tolerance_bps=20,
                failure_tolerance_bps=25,
                state="WAITING",
                result={},
                version=1,
                first_evaluated_at=EVALUATED_AT,
                last_evaluated_at=EVALUATED_AT,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_same_arm_key_is_allowed_for_different_watched_setups(database):
    _, sessions = database
    async with sessions() as session:
        await seed_watched(session, "setup-1")
        await seed_watched(session, "setup-2", "ETHUSDT")
        first = await persist_trigger_result(
            session, context=context(watched_setup_id="setup-1"), result=result()
        )
        second_context = context(watched_setup_id="setup-2", symbol="ETHUSDT")
        second = await persist_trigger_result(
            session,
            context=second_context,
            result=result(symbol="ETHUSDT"),
        )

    assert first.id != second.id


@pytest.mark.asyncio
async def test_duplicate_transition_version_is_rejected(database):
    _, sessions = database
    async with sessions() as session:
        await seed_watched(session)
        await persist_trigger_result(session, context=context(), result=result())
        await persist_trigger_result(
            session, context=context(), result=result(TriggerState.DEVELOPING)
        )
        existing = await session.scalar(select(TriggerTransitionRow))
        session.add(
            TriggerTransitionRow(
                trigger_attempt_id=existing.trigger_attempt_id,
                symbol="BTCUSDT",
                setup_type=ScannerSetupType.RANGE_LONG.value,
                from_state="WAITING",
                to_state="DEVELOPING",
                timestamp=EVALUATED_AT,
                result_before={},
                result_after={},
                version=existing.version,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_deleting_watched_setup_cascades_attempt_and_transition(database):
    _, sessions = database
    async with sessions() as session:
        watched_row = await seed_watched(session)
        await persist_trigger_result(session, context=context(), result=result())
        await persist_trigger_result(
            session, context=context(), result=result(TriggerState.DEVELOPING)
        )
        await session.delete(watched_row)
        await session.commit()
        attempts = await session.scalar(select(func.count(TriggerAttemptRow.id)))
        transitions = await session.scalar(select(func.count(TriggerTransitionRow.id)))

    assert attempts == 0
    assert transitions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["symbol", "setup_type", "armed_at", "reference_level"])
async def test_existing_attempt_context_mismatch_rejects_without_mutation(database, field):
    _, sessions = database
    async with sessions() as session:
        await seed_watched(session)
        original = await persist_trigger_result(
            session, context=context(), result=result()
        )
        attempt_id = original.id
        changes = {
            "symbol": "ETHUSDT",
            "setup_type": ScannerSetupType.RANGE_SHORT,
            "armed_at": ARMED_AT + timedelta(minutes=1),
            "reference_level": 101.0,
        }
        changed_context = replace(context(), **{field: changes[field]})
        changed_result = result(
            symbol=changed_context.symbol,
            setup_type=changed_context.setup_type,
            side=changed_context.side,
            armed_at=changed_context.armed_at,
            reference_level=changed_context.reference_level,
        )
        with pytest.raises(TriggerPersistenceConsistencyError, match="context mismatch"):
            await persist_trigger_result(
                session, context=changed_context, result=changed_result
            )
        await session.rollback()
        reloaded = await session.get(TriggerAttemptRow, attempt_id)

    assert reloaded.version == 1
    assert reloaded.state == TriggerState.RECLAIMED.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("symbol", "ETHUSDT"),
        ("setup_type", ScannerSetupType.RANGE_SHORT),
        ("side", Side.SHORT),
        ("armed_at", ARMED_AT + timedelta(minutes=1)),
        ("reference_level", 101.0),
    ],
)
async def test_result_identity_mismatch_rejects_before_mutation(database, field, value):
    _, sessions = database
    async with sessions() as session:
        await seed_watched(session)
        mismatched = result().model_copy(update={field: value})
        with pytest.raises(TriggerPersistenceConsistencyError, match="does not match"):
            await persist_trigger_result(
                session, context=context(), result=mismatched
            )
        attempt_count = await session.scalar(select(func.count(TriggerAttemptRow.id)))

    assert attempt_count == 0


@pytest.mark.asyncio
async def test_transition_failure_rolls_back_attempt_atomically(database):
    engine, sessions = database
    async with sessions() as session:
        await seed_watched(session)
        original = await persist_trigger_result(
            session, context=context(), result=result()
        )
        attempt_id = original.id

    def fail_transition(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("INSERT INTO TRIGGER_TRANSITIONS"):
            raise RuntimeError("injected trigger transition failure")

    event.listen(engine.sync_engine, "before_cursor_execute", fail_transition)
    try:
        async with sessions() as session:
            with pytest.raises(RuntimeError, match="injected trigger transition failure"):
                await persist_trigger_result(
                    session,
                    context=context(),
                    result=result(TriggerState.DEVELOPING),
                )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail_transition)

    async with sessions() as session:
        reloaded = await session.get(TriggerAttemptRow, attempt_id)
        transition_count = await session.scalar(
            select(func.count(TriggerTransitionRow.id))
        )

    assert reloaded.version == 1
    assert reloaded.state == TriggerState.RECLAIMED.value
    assert transition_count == 0


@pytest.mark.asyncio
async def test_trigger_indexes_and_constraints_exist(database):
    engine, _ = database

    def inspect_schema(connection):
        inspector = inspect(connection)
        return {
            "attempt_indexes": {item["name"] for item in inspector.get_indexes("trigger_attempts")},
            "attempt_unique": {
                item["name"] for item in inspector.get_unique_constraints("trigger_attempts")
            },
            "transition_unique": {
                item["name"] for item in inspector.get_unique_constraints("trigger_transitions")
            },
            "attempt_fks": inspector.get_foreign_keys("trigger_attempts"),
            "transition_fks": inspector.get_foreign_keys("trigger_transitions"),
        }

    async with engine.connect() as connection:
        schema = await connection.run_sync(inspect_schema)

    assert "uq_trigger_attempt_arm" in schema["attempt_unique"]
    assert "uq_trigger_transition_version" in schema["transition_unique"]
    assert "ix_trigger_attempt_symbol_type" in schema["attempt_indexes"]
    assert schema["attempt_fks"][0]["options"]["ondelete"] == "CASCADE"
    assert schema["transition_fks"][0]["options"]["ondelete"] == "CASCADE"
