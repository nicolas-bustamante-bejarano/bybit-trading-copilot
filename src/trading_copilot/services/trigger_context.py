from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any

from trading_copilot.domain.models import Side
from trading_copilot.domain.scanner import (
    ScannerSetupType,
    ScannerStatus,
    enabled_setup_types,
)
from trading_copilot.domain.trigger import (
    LowerTimeframeTriggerSnapshot,
    TriggerArmSource,
    TriggerEvaluationRequest,
    TriggerReferenceSource,
)
from trading_copilot.persistence.models import (
    ScannerTransitionRow,
    ScannerWatchlistRow,
    TriggerAttemptRow,
    WatchedSetupRow,
)

DEFAULT_TRIGGER_FAILURE_TOLERANCE_BPS = 25.0


@dataclass(frozen=True)
class TriggerContextResolution:
    symbol: str
    watched_setup_id: str
    setup_type: ScannerSetupType
    side: Side
    eligible: bool
    armed_at: datetime | None = None
    arm_source: TriggerArmSource | None = None
    arm_key: str | None = None
    arm_transition_id: str | None = None
    reference_level: float | None = None
    reference_source: TriggerReferenceSource | None = None
    armed_state: dict[str, Any] = field(default_factory=dict)
    retest_tolerance_bps: float = 0
    failure_tolerance_bps: float = DEFAULT_TRIGGER_FAILURE_TOLERANCE_BPS
    blocking_reason: str | None = None
    reference_metadata: dict[str, Any] = field(default_factory=dict)


def _blocked(
    watched: WatchedSetupRow,
    setup_type: ScannerSetupType,
    side: Side,
    reason: str,
    *,
    retest_tolerance_bps: float = 0,
) -> TriggerContextResolution:
    return TriggerContextResolution(
        symbol=watched.symbol,
        watched_setup_id=watched.id,
        setup_type=setup_type,
        side=side,
        eligible=False,
        retest_tolerance_bps=retest_tolerance_bps,
        blocking_reason=reason,
    )


def _positive_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    return normalized if isfinite(normalized) and normalized > 0 else None


def _side(setup_type: ScannerSetupType) -> Side:
    return Side.LONG if setup_type.value.endswith("_LONG") else Side.SHORT


def _db_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _stored_number(value: object, *, positive: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or (number <= 0 if positive else number < 0):
        return None
    return float(number)


def _range_reference(
    state: Mapping[str, Any], setup_type: ScannerSetupType
) -> tuple[float | None, TriggerReferenceSource, dict[str, Any]]:
    location = state.get("location")
    location = location if isinstance(location, Mapping) else {}
    if setup_type == ScannerSetupType.RANGE_LONG:
        return _positive_number(location.get("range_low")), TriggerReferenceSource.RANGE_LOW, {}
    return _positive_number(location.get("range_high")), TriggerReferenceSource.RANGE_HIGH, {}


def _trend_reference(
    state: Mapping[str, Any], side: Side
) -> tuple[float | None, TriggerReferenceSource, dict[str, Any]]:
    location = state.get("location")
    if not isinstance(location, Mapping):
        return None, TriggerReferenceSource.FIB_ZONE_LOWER, {}
    active_zone = location.get("active_zone")
    zones = location.get("zones")
    if not isinstance(active_zone, str) or not active_zone or not isinstance(zones, list):
        return None, TriggerReferenceSource.FIB_ZONE_LOWER, {}
    matches = [
        zone
        for zone in zones
        if isinstance(zone, Mapping) and zone.get("name") == active_zone
    ]
    source = (
        TriggerReferenceSource.FIB_ZONE_LOWER
        if side == Side.LONG
        else TriggerReferenceSource.FIB_ZONE_UPPER
    )
    if len(matches) != 1 or matches[0].get("enabled") is not True:
        return None, source, {"active_zone": active_zone}
    boundary = "lower" if side == Side.LONG else "upper"
    return (
        _positive_number(matches[0].get(boundary)),
        source,
        {"active_zone": active_zone},
    )


def _macro_reference(
    state: Mapping[str, Any], side: Side
) -> tuple[float | None, TriggerReferenceSource, dict[str, Any]]:
    structure_id = state.get("accepted_structure_id")
    accepted_side = state.get("accepted_side")
    metadata = {
        "accepted_structure_id": structure_id if isinstance(structure_id, str) else None
    }
    if (
        not isinstance(structure_id, str)
        or not structure_id
        or accepted_side != side.value.upper()
    ):
        return None, TriggerReferenceSource.ACCEPTED_BREAKOUT_LEVEL, metadata
    return (
        _positive_number(state.get("accepted_breakout_level")),
        TriggerReferenceSource.ACCEPTED_BREAKOUT_LEVEL,
        metadata,
    )


def _expected_reference_source(
    setup_type: ScannerSetupType, side: Side
) -> TriggerReferenceSource:
    if setup_type == ScannerSetupType.RANGE_LONG:
        return TriggerReferenceSource.RANGE_LOW
    if setup_type == ScannerSetupType.RANGE_SHORT:
        return TriggerReferenceSource.RANGE_HIGH
    if setup_type in {
        ScannerSetupType.TREND_PULLBACK_LONG,
        ScannerSetupType.TREND_PULLBACK_SHORT,
    }:
        return (
            TriggerReferenceSource.FIB_ZONE_LOWER
            if side == Side.LONG
            else TriggerReferenceSource.FIB_ZONE_UPPER
        )
    return TriggerReferenceSource.ACCEPTED_BREAKOUT_LEVEL


def resolve_trigger_context(
    *,
    watched: WatchedSetupRow,
    watchlist: ScannerWatchlistRow,
    transitions: Iterable[ScannerTransitionRow],
    existing_attempt: TriggerAttemptRow | None = None,
) -> TriggerContextResolution:
    setup_type = ScannerSetupType(watched.setup_type)
    side = _side(setup_type)
    try:
        retest_tolerance = float(watchlist.retest_tolerance_bps)
    except (TypeError, ValueError):
        retest_tolerance = -1
    if not isfinite(retest_tolerance) or retest_tolerance < 0:
        return _blocked(watched, setup_type, side, "TRIGGER_CONFIG_INVALID")
    if watchlist.symbol != watched.symbol:
        return _blocked(
            watched,
            setup_type,
            side,
            "WATCHLIST_CONFIG_REQUIRED",
            retest_tolerance_bps=retest_tolerance,
        )
    if not watchlist.enabled:
        return _blocked(
            watched,
            setup_type,
            side,
            "WATCHLIST_DISABLED",
            retest_tolerance_bps=retest_tolerance,
        )
    try:
        enabled_setups = enabled_setup_types(watchlist.enabled_playbooks)
    except (AttributeError, TypeError, ValueError):
        return _blocked(
            watched,
            setup_type,
            side,
            "TRIGGER_CONFIG_INVALID",
            retest_tolerance_bps=retest_tolerance,
        )
    if setup_type not in enabled_setups:
        return _blocked(
            watched,
            setup_type,
            side,
            "SETUP_DISABLED",
            retest_tolerance_bps=retest_tolerance,
        )
    if watched.status != ScannerStatus.TRIGGER_ARMED.value:
        return _blocked(
            watched,
            setup_type,
            side,
            "NOT_ELIGIBLE",
            retest_tolerance_bps=retest_tolerance,
        )

    armed_transitions = [
        transition
        for transition in transitions
        if transition.watched_setup_id == watched.id
        and transition.to_status == ScannerStatus.TRIGGER_ARMED.value
    ]
    if any(_db_utc(transition.timestamp) is None for transition in armed_transitions):
        return _blocked(
            watched,
            setup_type,
            side,
            "ARMED_AT_UNRESOLVED",
            retest_tolerance_bps=retest_tolerance,
        )
    if armed_transitions:
        transition = max(
            armed_transitions,
            key=lambda item: (_db_utc(item.timestamp), item.version, item.id),
        )
        armed_at = _db_utc(transition.timestamp)
        arm_source = TriggerArmSource.ARM_TRANSITION
        arm_transition_id = transition.id
        arm_key = f"TRANSITION:{transition.id}"
        armed_state = transition.state_after
    elif watched.version == 1 and _db_utc(watched.created_at) is not None:
        armed_at = _db_utc(watched.created_at)
        arm_source = TriggerArmSource.FIRST_OBSERVATION_BASELINE
        arm_transition_id = None
        arm_key = f"BASELINE:{watched.id}:{armed_at.isoformat()}"
        armed_state = watched.state
    else:
        return _blocked(
            watched,
            setup_type,
            side,
            "ARMED_AT_UNRESOLVED",
            retest_tolerance_bps=retest_tolerance,
        )
    if (
        existing_attempt is not None
        and existing_attempt.watched_setup_id == watched.id
        and existing_attempt.arm_key == arm_key
    ):
        attempt_armed_at = _db_utc(existing_attempt.armed_at)
        attempt_level = _stored_number(existing_attempt.reference_level, positive=True)
        attempt_retest = _stored_number(existing_attempt.retest_tolerance_bps)
        attempt_failure = _stored_number(existing_attempt.failure_tolerance_bps)
        metadata = existing_attempt.reference_metadata
        try:
            attempt_arm_source = TriggerArmSource(existing_attempt.arm_source)
            attempt_reference_source = TriggerReferenceSource(
                existing_attempt.reference_source
            )
        except (TypeError, ValueError):
            attempt_arm_source = None
            attempt_reference_source = None
        valid_provenance = (
            existing_attempt.symbol == watched.symbol
            and existing_attempt.setup_type == setup_type.value
            and attempt_arm_source == arm_source
            and existing_attempt.arm_transition_id == arm_transition_id
            and attempt_armed_at == armed_at
            and attempt_reference_source == _expected_reference_source(setup_type, side)
        )
        if (
            attempt_armed_at is None
            or attempt_level is None
            or attempt_retest is None
            or attempt_failure is None
            or attempt_arm_source is None
            or attempt_reference_source is None
            or not isinstance(metadata, Mapping)
            or not valid_provenance
        ):
            return _blocked(
                watched,
                setup_type,
                side,
                "TRIGGER_ATTEMPT_CONTEXT_INVALID",
                retest_tolerance_bps=retest_tolerance,
            )
        return TriggerContextResolution(
            symbol=watched.symbol,
            watched_setup_id=watched.id,
            setup_type=setup_type,
            side=side,
            eligible=True,
            armed_at=attempt_armed_at,
            arm_source=attempt_arm_source,
            arm_key=arm_key,
            arm_transition_id=existing_attempt.arm_transition_id,
            reference_level=attempt_level,
            reference_source=attempt_reference_source,
            armed_state=deepcopy(dict(armed_state)) if isinstance(armed_state, Mapping) else {},
            retest_tolerance_bps=attempt_retest,
            failure_tolerance_bps=attempt_failure,
            reference_metadata=deepcopy(dict(metadata)),
        )

    if not isinstance(armed_state, Mapping):
        return _blocked(
            watched,
            setup_type,
            side,
            "TRIGGER_REFERENCE_REQUIRED",
            retest_tolerance_bps=retest_tolerance,
        )

    if setup_type in {ScannerSetupType.RANGE_LONG, ScannerSetupType.RANGE_SHORT}:
        level, source, metadata = _range_reference(armed_state, setup_type)
        missing_reason = "TRIGGER_REFERENCE_REQUIRED"
    elif setup_type in {
        ScannerSetupType.TREND_PULLBACK_LONG,
        ScannerSetupType.TREND_PULLBACK_SHORT,
    }:
        level, source, metadata = _trend_reference(armed_state, side)
        missing_reason = "TRIGGER_REFERENCE_REQUIRED"
    else:
        level, source, metadata = _macro_reference(armed_state, side)
        missing_reason = "ACCEPTED_BREAKOUT_REFERENCE_REQUIRED"
    if level is None:
        return _blocked(
            watched,
            setup_type,
            side,
            missing_reason,
            retest_tolerance_bps=retest_tolerance,
        )
    return TriggerContextResolution(
        symbol=watched.symbol,
        watched_setup_id=watched.id,
        setup_type=setup_type,
        side=side,
        eligible=True,
        armed_at=armed_at,
        arm_source=arm_source,
        arm_key=arm_key,
        arm_transition_id=arm_transition_id,
        reference_level=level,
        reference_source=source,
        armed_state=deepcopy(dict(armed_state)),
        retest_tolerance_bps=retest_tolerance,
        failure_tolerance_bps=DEFAULT_TRIGGER_FAILURE_TOLERANCE_BPS,
        reference_metadata=metadata,
    )


def compose_trigger_request(
    context: TriggerContextResolution,
    snapshot: LowerTimeframeTriggerSnapshot,
) -> TriggerEvaluationRequest:
    if not context.eligible:
        raise ValueError("trigger context is not eligible")
    if context.symbol != snapshot.symbol:
        raise ValueError("trigger context and snapshot symbols do not match")
    if context.armed_at is None or context.reference_level is None:
        raise ValueError("eligible trigger context is incomplete")
    return TriggerEvaluationRequest(
        symbol=context.symbol,
        setup_type=context.setup_type,
        side=context.side,
        reference_level=context.reference_level,
        bars_15m=snapshot.bars_15m,
        bars_5m=snapshot.bars_5m,
        armed_at=context.armed_at,
        evaluated_at=snapshot.evaluated_at,
        retest_tolerance_bps=context.retest_tolerance_bps,
        failure_tolerance_bps=context.failure_tolerance_bps,
        reaction_state=snapshot.reaction_state,
    )
