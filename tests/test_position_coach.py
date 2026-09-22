from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import trading_copilot.main as main_module
from trading_copilot.domain.account import NormalizedAccount, NormalizedPosition
from trading_copilot.domain.lifecycle import PositionLifecycle
from trading_copilot.domain.models import Side
from trading_copilot.domain.position_coach import CoachExecutionState, RiskPolicyStatus
from trading_copilot.main import app
from trading_copilot.persistence.database import get_session
from trading_copilot.persistence.models import (
    Base,
    DecisionSnapshotRow,
    ExecutionRuleRow,
    FibDefinitionRow,
    RangeDefinitionRow,
    TradePlanRow,
)
from trading_copilot.services.position_coach import evaluate_position_coach, load_coach_records

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def position(symbol="BNBUSDT", side=Side.LONG, mark=600, stop=550):
    return NormalizedPosition(
        symbol=symbol,
        side=side,
        quantity=Decimal(2),
        average_entry=Decimal(600),
        mark_price=Decimal(str(mark)),
        unrealized_pnl=Decimal(20),
        stop_loss=Decimal(str(stop)) if stop is not None else None,
        structural_risk_usdt=Decimal(100) if stop is not None else None,
        protected=stop is not None,
    )


def plan(
    symbol="BNBUSDT",
    side="LONG",
    invalidation="550",
    warning=None,
    max_risk="0.1",
    setup_type=None,
    add_conditions=None,
):
    return TradePlanRow(
        id=f"plan-{symbol}",
        symbol=symbol,
        side=side,
        setup_type=setup_type or ("RANGE_LONG" if side == "LONG" else "RANGE_SHORT"),
        thesis="Planned pullback",
        lifecycle_status="ACTIVE",
        hard_invalidation=Decimal(invalidation),
        thesis_warning=Decimal(warning) if warning else None,
        max_risk_percent=Decimal(max_risk),
        correlation_group="crypto_beta",
        target_ladder=[],
        add_conditions=(
            [{"type": "CONFIRMATION_REQUIRED"}] if add_conditions is None else add_conditions
        ),
    )


def account(*positions):
    return NormalizedAccount(
        equity_usdt=Decimal(10000),
        max_group_risk_pct=Decimal("0.1"),
        risk_budget_usdt=Decimal(1000),
        total_structural_risk_usdt=Decimal(100),
        total_structural_risk_pct=Decimal("0.01"),
        within_risk_budget=True,
        positions=list(positions),
    )


def lifecycle(item):
    return PositionLifecycle(
        symbol=item.symbol,
        current_side=item.side,
        current_quantity=item.quantity,
        current_average_entry=item.average_entry,
        history_complete=True,
        matches_current_position=True,
        source_fill_count=1,
        selected_fill_count=1,
    )


def range_at(symbol="BNBUSDT", side=Side.LONG, mark="600"):
    value = Decimal(mark)
    return RangeDefinitionRow(
        symbol=symbol,
        range_low=value if side == Side.LONG else value - Decimal(100),
        range_high=value if side == Side.SHORT else value + Decimal(100),
    )


def live(reaction=None, age_seconds=0):
    return {
        "timestamp_ms": int((NOW - timedelta(seconds=age_seconds)).timestamp() * 1000),
        "reaction_1m": {"state": reaction} if reaction else None,
        "funding_rate": 0.0001,
        "open_interest": 1000,
    }


def evaluate(
    item=None,
    active_plan=True,
    reaction=None,
    age_seconds=0,
    ranges=True,
    rules=None,
    other_positions=(),
    other_plans=None,
    market=None,
    fibs=None,
):
    item = item or position()
    active = (
        plan(item.symbol, item.side.value.upper())
        if active_plan is True
        else (None if active_plan is False else active_plan)
    )
    all_positions = (item, *other_positions)
    plans = {item.symbol: active} if active else {}
    plans.update(other_plans or {})
    return evaluate_position_coach(
        position=item,
        account=account(*all_positions),
        lifecycle=lifecycle(item),
        plan=active,
        rules=rules or [],
        plans_by_symbol=plans,
        market=market
        or {
            "timeframes": {
                "1h": {"regime": "range", "stoch_rsi_k": 15, "stoch_rsi_d": 20},
                "4h": {"regime": "range"},
            }
        },
        live=live(reaction, age_seconds),
        fibs=fibs or [],
        ranges=[range_at(item.symbol, item.side, str(item.mark_price))] if ranges else [],
        now=NOW,
    )


def test_open_position_with_active_plan_normally_holds():
    result = evaluate()
    assert result.execution.state == CoachExecutionState.HOLD
    assert result.execution.add_allowed is False


def test_position_without_plan_holds_and_risk_is_indeterminate():
    result = evaluate(active_plan=False)
    assert result.plan.status == "MISSING"
    assert result.execution.state == "HOLD"
    assert result.execution.add_allowed is False
    assert result.risk.policy_status == RiskPolicyStatus.INDETERMINATE


@pytest.mark.parametrize(
    ("side", "mark", "invalidation"),
    [(Side.LONG, "550", "550"), (Side.SHORT, "650", "650")],
)
def test_hard_invalidation_boundary_has_priority(side, mark, invalidation):
    item = position(side=side, mark=mark, stop=invalidation)
    active = plan(side=side.value.upper(), invalidation=invalidation)
    result = evaluate(item=item, active_plan=active, reaction="sell_continuation")
    assert result.execution.state == CoachExecutionState.INVALIDATE
    assert result.execution.add_allowed is False


def test_warning_crossed_does_not_invalidate():
    item = position(side=Side.LONG, mark=570)
    active = plan(invalidation="550", warning="580")
    result = evaluate(item=item, active_plan=active, reaction="buy_continuation")
    assert result.execution.state == CoachExecutionState.HOLD
    assert result.execution.warnings


def test_location_without_reaction_cannot_add():
    result = evaluate(reaction=None)
    assert result.market_context.location == "LOWER_RANGE_EXTREME"
    assert result.execution.add_allowed is False


def test_confirmed_reaction_with_risk_breach_cannot_add():
    result = evaluate(active_plan=plan(max_risk="0.001"), reaction="buy_continuation")
    assert result.risk.policy_status == RiskPolicyStatus.BREACH
    assert result.execution.add_allowed is False


def test_confirmed_reaction_with_indeterminate_risk_cannot_add():
    unknown = position("ETHUSDT", stop=None)
    result = evaluate(reaction="buy_continuation", other_positions=(unknown,))
    assert result.risk.policy_status == RiskPolicyStatus.INDETERMINATE
    assert result.execution.add_allowed is False


def test_all_required_evidence_allows_add():
    result = evaluate(reaction="buy_continuation")
    assert result.risk.policy_status == RiskPolicyStatus.PASS
    assert result.execution.state == CoachExecutionState.ADD
    assert result.execution.add_allowed is True


def test_no_predefined_add_condition_holds_despite_valid_market_evidence():
    result = evaluate(active_plan=plan(add_conditions=[]), reaction="buy_continuation")
    assert result.execution.state == CoachExecutionState.HOLD
    assert result.execution.add_allowed is False
    assert "no predefined add condition" in " ".join(result.execution.blocking_reasons)


def test_predefined_add_condition_must_be_satisfied():
    result = evaluate(
        active_plan=plan(add_conditions=[{"type": "LOCATION_VALID"}]),
        reaction="buy_continuation",
        ranges=False,
    )
    assert result.execution.state == CoachExecutionState.HOLD
    assert result.execution.add_allowed is False
    assert "LOCATION_VALID" in " ".join(result.execution.blocking_reasons)


def test_unknown_predefined_add_condition_is_indeterminate_and_blocks_add():
    result = evaluate(
        active_plan=plan(add_conditions=[{"type": "MOON_PHASE"}]),
        reaction="buy_continuation",
    )
    assert result.execution.state == CoachExecutionState.HOLD
    assert result.execution.add_allowed is False
    assert "MOON_PHASE" in " ".join(result.execution.blocking_reasons)


@pytest.mark.parametrize(
    ("side", "wrong_regime"),
    [(Side.LONG, "downtrend"), (Side.SHORT, "uptrend")],
)
def test_trend_pullback_wrong_4h_regime_cannot_add(side, wrong_regime):
    item = position(side=side)
    active = plan(
        side=side.value.upper(),
        setup_type="TREND_PULLBACK",
        add_conditions=[{"type": "CONTEXT_VALID"}],
    )
    fib = FibDefinitionRow(
        symbol="BNBUSDT",
        direction=side.value.upper(),
        swing_low=Decimal(500),
        swing_high=Decimal(700),
    )
    result = evaluate(
        item=item,
        active_plan=active,
        reaction="buy_continuation" if side == Side.LONG else "sell_continuation",
        fibs=[fib],
        market={
            "timeframes": {
                "1h": {"regime": wrong_regime, "stoch_rsi_k": 15, "stoch_rsi_d": 20},
                "4h": {"regime": wrong_regime},
            }
        },
    )
    assert result.execution.add_allowed is False
    assert result.market_context.playbook_state == "NO_SETUP"


def test_valid_trend_pullback_uses_existing_playbook_evaluation():
    active = plan(setup_type="TREND_PULLBACK")
    fib = FibDefinitionRow(
        symbol="BNBUSDT", direction="LONG", swing_low=Decimal(500), swing_high=Decimal(700)
    )
    result = evaluate(
        active_plan=active,
        reaction="buy_continuation",
        fibs=[fib],
        market={
            "timeframes": {
                "1h": {"regime": "uptrend", "stoch_rsi_k": 15, "stoch_rsi_d": 20},
                "4h": {"regime": "uptrend"},
            }
        },
    )
    assert result.market_context.playbook_state == "READY"
    assert any(
        condition["name"] == "4h_regime_aligned"
        for condition in result.market_context.playbook_conditions
    )


def test_range_playbook_outside_4h_range_regime_cannot_add():
    result = evaluate(
        reaction="buy_continuation",
        market={
            "timeframes": {
                "1h": {"regime": "uptrend", "stoch_rsi_k": 15, "stoch_rsi_d": 20},
                "4h": {"regime": "uptrend"},
            }
        },
    )
    assert result.execution.add_allowed is False
    assert result.market_context.playbook_state == "NO_SETUP"


@pytest.mark.parametrize(("reaction", "age"), [(None, 0), ("buy_continuation", 121)])
def test_missing_or_stale_reaction_cannot_add(reaction, age):
    result = evaluate(reaction=reaction, age_seconds=age)
    assert result.execution.add_allowed is False


def test_unknown_correlated_position_prevents_pass():
    result = evaluate(
        reaction="buy_continuation", other_positions=(position("ETHUSDT", stop=None),)
    )
    assert result.risk.policy_status == RiskPolicyStatus.INDETERMINATE
    assert "ETHUSDT" in result.risk.incomplete_symbols


def test_plan_invalidation_has_execution_plan_provenance():
    result = evaluate()
    assert result.risk.provenance["BNBUSDT"] == "execution_plan"
    assert result.plan.stop_provenance == "execution_plan"


def test_exchange_stop_never_masquerades_as_execution_plan():
    other = position("ETHUSDT", stop=500)
    result = evaluate(other_positions=(other,))
    assert result.risk.provenance["ETHUSDT"] == "exchange_order"


def test_unsupported_rule_blocks_add():
    rule = ExecutionRuleRow(
        trade_plan_id="plan-BNBUSDT",
        action="ADD",
        rule_type="MAGIC_RULE",
        description="Unsupported",
    )
    result = evaluate(reaction="buy_continuation", rules=[rule])
    assert result.execution.add_allowed is False
    assert any("unsupported" in reason for reason in result.execution.blocking_reasons)


def test_lowercase_symbol_normalizes_in_response():
    result = evaluate(item=position(symbol="bnbusdt"), active_plan=plan("BNBUSDT"))
    assert result.symbol == "BNBUSDT"


@pytest_asyncio.fixture
async def coach_client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/coach.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override():
        async with sessions() as session:
            yield session

    async def market_stub(_):
        return {"timeframes": {"1h": {}, "4h": {}}}

    app.dependency_overrides[get_session] = session_override
    monkeypatch.setattr(main_module, "build_market_snapshot", market_stub)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, sessions
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_no_open_position_returns_404(coach_client, monkeypatch):
    client, _ = coach_client

    async def empty_account():
        return account()

    monkeypatch.setattr(main_module, "_normalized_account", empty_account)
    assert (await client.get("/positions/bnbusdt/coach")).status_code == 404


@pytest.mark.asyncio
async def test_multiple_active_plans_return_409(coach_client, monkeypatch):
    client, sessions = coach_client
    item = position()

    async def live_account():
        return account(item)

    monkeypatch.setattr(main_module, "_normalized_account", live_account)
    async with sessions() as session:
        first = plan()
        second = plan()
        second.id = "second-plan-BNBUSDT"
        session.add_all([first, second])
        await session.commit()
    assert (await client.get("/positions/BNBUSDT/coach")).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("position_side", "plan_side"),
    [(Side.SHORT, "LONG"), (Side.LONG, "SHORT")],
)
async def test_active_plan_side_mismatch_returns_409(
    coach_client, monkeypatch, position_side, plan_side
):
    client, sessions = coach_client
    item = position(side=position_side)

    async def live_account():
        return account(item)

    monkeypatch.setattr(main_module, "_normalized_account", live_account)
    async with sessions() as session:
        session.add(plan(side=plan_side))
        await session.commit()
    response = await client.get("/positions/BNBUSDT/coach")
    assert response.status_code == 409
    assert "side does not match" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_coach_performs_no_journal_writes(coach_client, monkeypatch):
    client, sessions = coach_client
    item = position()

    async def live_account():
        return account(item)

    monkeypatch.setattr(main_module, "_normalized_account", live_account)
    response = await client.get("/positions/bnbusdt/coach")
    assert response.status_code == 200
    async with sessions() as session:
        count = await session.scalar(select(func.count()).select_from(DecisionSnapshotRow))
        assert count == 0


@pytest.mark.asyncio
async def test_active_plan_fib_wins_over_old_plan_fib(coach_client):
    _, sessions = coach_client
    active = plan(setup_type="TREND_PULLBACK")
    old = plan(setup_type="TREND_PULLBACK")
    old.id = "old-plan"
    old.lifecycle_status = "CLOSED"
    async with sessions() as session:
        session.add_all(
            [
                active,
                old,
                FibDefinitionRow(
                    trade_plan_id=active.id,
                    symbol="BNBUSDT",
                    direction="LONG",
                    swing_low=500,
                    swing_high=700,
                ),
                FibDefinitionRow(
                    trade_plan_id=old.id,
                    symbol="BNBUSDT",
                    direction="LONG",
                    swing_low=100,
                    swing_high=200,
                ),
            ]
        )
        await session.commit()
        _, _, fibs, _, _ = await load_coach_records(session, "BNBUSDT")
    assert len(fibs) == 1
    assert fibs[0].trade_plan_id == active.id


@pytest.mark.asyncio
async def test_active_plan_range_wins_over_old_plan_range(coach_client):
    _, sessions = coach_client
    active = plan()
    old = plan()
    old.id = "old-plan"
    old.lifecycle_status = "CLOSED"
    async with sessions() as session:
        session.add_all(
            [
                active,
                old,
                RangeDefinitionRow(
                    trade_plan_id=active.id, symbol="BNBUSDT", range_low=500, range_high=700
                ),
                RangeDefinitionRow(
                    trade_plan_id=old.id, symbol="BNBUSDT", range_low=100, range_high=200
                ),
            ]
        )
        await session.commit()
        _, _, _, ranges, _ = await load_coach_records(session, "BNBUSDT")
    assert len(ranges) == 1
    assert ranges[0].trade_plan_id == active.id


@pytest.mark.asyncio
async def test_multiple_active_plan_definitions_remain_ambiguous(coach_client):
    _, sessions = coach_client
    active = plan(setup_type="TREND_PULLBACK")
    async with sessions() as session:
        session.add(active)
        session.add_all(
            [
                FibDefinitionRow(
                    trade_plan_id=active.id,
                    symbol="BNBUSDT",
                    direction="LONG",
                    swing_low=500,
                    swing_high=700,
                ),
                FibDefinitionRow(
                    trade_plan_id=active.id,
                    symbol="BNBUSDT",
                    direction="LONG",
                    swing_low=400,
                    swing_high=800,
                ),
            ]
        )
        await session.commit()
        _, _, fibs, _, _ = await load_coach_records(session, "BNBUSDT")
    result = evaluate(active_plan=active, reaction="buy_continuation", fibs=fibs, ranges=False)
    assert result.market_context.location_status == "INDETERMINATE"
    assert result.execution.add_allowed is False


@pytest.mark.asyncio
async def test_standalone_definition_is_fallback_when_plan_has_none(coach_client):
    _, sessions = coach_client
    active = plan()
    standalone = RangeDefinitionRow(
        trade_plan_id=None, symbol="BNBUSDT", range_low=500, range_high=700
    )
    async with sessions() as session:
        session.add_all([active, standalone])
        await session.commit()
        _, _, _, ranges, _ = await load_coach_records(session, "BNBUSDT")
    assert len(ranges) == 1
    assert ranges[0].trade_plan_id is None
