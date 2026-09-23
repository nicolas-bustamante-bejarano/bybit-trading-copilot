from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_copilot.domain.trigger import TriggerResult
from trading_copilot.persistence.models import TriggerAttemptRow, TriggerTransitionRow
from trading_copilot.services.trigger_context import TriggerContextResolution


class TriggerPersistenceConsistencyError(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _level(value: float) -> Decimal:
    return Decimal(str(value))


def _validate_required_context(context: TriggerContextResolution) -> None:
    if not context.eligible:
        raise TriggerPersistenceConsistencyError("trigger context is not eligible")
    required = {
        "arm_key": context.arm_key,
        "arm_source": context.arm_source,
        "armed_at": context.armed_at,
        "reference_level": context.reference_level,
        "reference_source": context.reference_source,
    }
    missing = [name for name, value in required.items() if value is None or value == ""]
    if missing:
        raise TriggerPersistenceConsistencyError(
            f"eligible trigger context is incomplete: {missing[0]}"
        )


def _validate_result_identity(
    context: TriggerContextResolution, result: TriggerResult
) -> None:
    mismatches: list[str] = []
    if result.symbol != context.symbol:
        mismatches.append("symbol")
    if result.setup_type != context.setup_type:
        mismatches.append("setup_type")
    if result.side != context.side:
        mismatches.append("side")
    if context.armed_at is not None and _utc(result.armed_at) != _utc(context.armed_at):
        mismatches.append("armed_at")
    if context.reference_level is not None and _level(result.reference_level) != _level(
        context.reference_level
    ):
        mismatches.append("reference_level")
    if mismatches:
        raise TriggerPersistenceConsistencyError(
            f"trigger result does not match context: {mismatches[0]}"
        )


def _validate_existing_context(
    attempt: TriggerAttemptRow, context: TriggerContextResolution
) -> None:
    mismatches: list[str] = []
    if attempt.symbol != context.symbol:
        mismatches.append("symbol")
    if attempt.setup_type != context.setup_type.value:
        mismatches.append("setup_type")
    if context.armed_at is None or _utc(attempt.armed_at) != _utc(context.armed_at):
        mismatches.append("armed_at")
    if context.reference_level is None or attempt.reference_level != _level(
        context.reference_level
    ):
        mismatches.append("reference_level")
    if (
        context.reference_source is None
        or attempt.reference_source != context.reference_source.value
    ):
        mismatches.append("reference_source")
    if mismatches:
        raise TriggerPersistenceConsistencyError(
            f"existing trigger attempt context mismatch: {mismatches[0]}"
        )


async def persist_trigger_result(
    session: AsyncSession,
    *,
    context: TriggerContextResolution,
    result: TriggerResult,
) -> TriggerAttemptRow:
    _validate_required_context(context)
    _validate_result_identity(context, result)
    attempt = await session.scalar(
        select(TriggerAttemptRow).where(
            TriggerAttemptRow.watched_setup_id == context.watched_setup_id,
            TriggerAttemptRow.arm_key == context.arm_key,
        )
    )
    payload = result.model_dump(mode="json")
    if attempt is None:
        attempt = TriggerAttemptRow(
            watched_setup_id=context.watched_setup_id,
            symbol=context.symbol,
            setup_type=context.setup_type.value,
            arm_key=context.arm_key,
            arm_source=context.arm_source.value,
            arm_transition_id=context.arm_transition_id,
            armed_at=context.armed_at,
            reference_level=_level(context.reference_level),
            reference_source=context.reference_source.value,
            reference_metadata=dict(context.reference_metadata),
            retest_tolerance_bps=Decimal(str(context.retest_tolerance_bps)),
            failure_tolerance_bps=Decimal(str(context.failure_tolerance_bps)),
            state=result.state.value,
            result=payload,
            version=1,
            first_evaluated_at=result.evaluated_at,
            last_evaluated_at=result.evaluated_at,
        )
        session.add(attempt)
    else:
        _validate_existing_context(attempt, context)
        previous_state = attempt.state
        previous_result = dict(attempt.result)
        attempt.result = payload
        attempt.last_evaluated_at = result.evaluated_at
        if previous_state != result.state.value:
            attempt.state = result.state.value
            attempt.version += 1
            session.add(
                TriggerTransitionRow(
                    trigger_attempt_id=attempt.id,
                    symbol=attempt.symbol,
                    setup_type=attempt.setup_type,
                    from_state=previous_state,
                    to_state=result.state.value,
                    timestamp=result.evaluated_at,
                    result_before=previous_result,
                    result_after=payload,
                    version=attempt.version,
                )
            )
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(attempt)
    return attempt
