# Architecture

## Product boundary

The system is a discretionary trading operating system. It discovers, evaluates, visualizes, risk-gates, journals, and reviews. It never places, amends, cancels, transfers, or withdraws. The trader executes manually on Bybit.

```text
SCAN → REGIME → LOCATION → REACTION → TRIGGER → RISK / SIZE
     → PROBE → ADD / HOLD / REDUCE → EXIT / INVALIDATE → REVIEW
```

Reaction and trigger are distinct. A price touching a level, an isolated wick through resistance, or adverse movement does not establish a trigger or permit an add.

## Current runtime flow

```text
Bybit public WebSocket ──→ in-memory book / flow / reaction state
Bybit public REST ───────→ multi-timeframe market snapshot
Bybit read-only private ─→ normalized account / positions / fills
                                      ↓
Persistent plans ─────────────→ shared position-coach composition
                                      ↓
                              material state projection
                                      ↓
                         cursor comparison and transition
                                      ↓
             atomic cursor + event + optional decision snapshot
                                      ↓
                       read-only dashboard decision feed
```

`GET /positions/{symbol}/coach` and the background monitor share Python composition logic. The GET route has no persistence side effects. The monitor fetches the normalized account once per cycle, evaluates each open position, and reconciles previously open symbols only after a successful account response.

## State-change persistence

`CoachStateCursor` is mutable latest state, one row per symbol. `StateChangeEvent` is append-only history. The projection intentionally includes only state-like facts: position presence, active plan, execution state, ADD permission, risk status, reaction status/classifier, playbook state, thesis warning, invalidation, and confidence.

Mark-price, PnL, equity, EMA, Stoch RSI, funding, and open-interest movement do not create events alone. Evidence values can be preserved inside the single automatic decision snapshot created for an eligible transition.

The first observation stores a silent version-one baseline. A later transition increments the version. The cursor update, state-change event, and optional snapshot use one database commit, with `UNIQUE(symbol, to_version)` preventing duplicate events. Automatic snapshots set `action_considered` from the coach and leave `action_taken` empty.

## Playbook families and timeframes

The MVP has three contextual families:

1. Trend Pullback: regime → pullback location → reaction → trigger → staged execution.
2. Range Extreme / Deviation-Reclaim: range context → extreme → deviation/rejection/reclaim → trigger → staged execution.
3. Macro Breakout / Reclaim: macro context → base → approach → breakout attempt → acceptance → retest → continuation → staged execution.

Context and execution timeframes are explicit. Typical macro context uses 3D/1D, structure and acceptance use 4H/1H, and execution triggers use 15m/5m. Slow context does not require tick-level recomputation.

Future Macro Breakout state progression is:

```text
MACRO_CONTEXT → BASE_FORMING → APPROACHING_BREAKOUT → BREAKOUT_ATTEMPT
→ ACCEPTANCE_PENDING → BREAKOUT_ACCEPTED → RETEST_PENDING → RETEST_HOLD
→ PROBE_ALLOWED → CONTINUATION_CONFIRMED → ADD_ALLOWED
```

A failed attempt transitions to `FAILED_BREAKOUT`, then `WAIT` or invalidation. Initial structural inputs are manual trendlines, horizontal zones, range boundaries, prior support/resistance, and macro targets.

## Staged sizing and leverage semantics

Plans will define their own stage allocations rather than use one global split. A typical sequence begins at zero risk, deploys a 20–30% probe, and permits later adds only after new predefined evidence appears since the prior execution stage.

```text
risk_budget = account_equity × configured_risk_percent
stop_distance = abs(planned_entry - structural_invalidation)
max_quantity = risk_budget / stop_distance
position_notional = quantity × entry
effective_exposure = position_notional / account_equity
```

Each stage will expose stage risk dollars, stage quantity, cumulative quantity, and remaining risk capacity. Portfolio and correlation-group capacity remains authoritative.

Risk is loss at structural invalidation. Exposure is notional relative to equity. Leverage is exchange margin configuration selected only after safe quantity is known. Leverage never expands risk budget, quantity, or stage allocation. Future guidance will use exchange-reported margin and liquidation data and will not present an approximation as exact exchange truth.

## Low-latency direction

The future critical path is:

```text
Bybit WebSocket → in-memory normalization → feature/state update
→ setup/trigger evaluation → execution coach → state-change event
→ realtime browser transport
```

REST calls and synchronous database persistence do not belong in this hot path. Market messages can be evaluated independently while browser rendering is coalesced around 50–100 ms. Meaningful transitions can be persisted asynchronously.

Instrumentation will carry `exchange_timestamp`, `received_at`, `normalized_at`, `evaluated_at`, `alert_created_at`, and `browser_received_at`. Engineering targets measured after backend receipt are:

- normalization under 25 ms p95
- setup/trigger evaluation under 50 ms p95
- state-change generation under 100 ms p95
- local/browser end-to-end under 250 ms p95

These are measurement targets, not guaranteed latency.

## Planned production deployment

A Railway project will contain:

- `copilot-api`: FastAPI, Bybit public WebSocket, read-only private integration, state-change monitor, future setup/trigger engine, and future realtime browser transport
- `copilot-web`: Next.js trading workstation
- PostgreSQL: plans, journal, state, and setup history

The backend starts with one replica because live market and order-book state are process-local. Horizontal scaling waits for shared live state, leader election, or an equivalent design. Health checks and Alembic pre-deploy migrations are required.

Bybit credentials remain backend-only. No Bybit secret may appear in a `NEXT_PUBLIC` variable, frontend bundle, or browser request. Local development will gain a root command such as `make dev` so both services start together.
