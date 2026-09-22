# Product roadmap

## MVP definition

The product is a low-latency discretionary trading operating system. It discovers contextual setups before entry, visualizes regime/location/reaction/trigger evidence, records meaningful transitions, calculates risk-based size, supports staged execution, and reviews playbook adherence. The trader always executes manually on Bybit.

```text
SCAN → REGIME → LOCATION → REACTION → TRIGGER → RISK / SIZE
     → PROBE → ADD / HOLD / REDUCE → EXIT / INVALIDATE → REVIEW
```

Location earns attention. Initial evidence may earn a probe. Additional size requires new predefined confirmation. Risk determines quantity; leverage only changes margin efficiency. Adverse movement never qualifies as confirmation, and full intended risk should normally remain undeployed until the planned progression earns it.

## Implemented foundation

- FastAPI with read-only Bybit public and private integration
- 12/21 EMA, Stoch RSI, Fib, range, funding, open interest, and 1H/4H market context
- Public WebSocket order-book, trade-flow, and reaction state held in memory
- Trend Pullback, Range Long, and Range Short playbooks
- Position sizing and correlation-group risk policy
- Persistent plans, typed execution rules, decision snapshots, execution events, and reviews
- Current-position lifecycle reconstruction and evidence-based live position coach
- Next.js trading workstation with account, position, journal, and system views

## PR #17 — State Change Engine + Decision Feed (current)

- [x] Persist one mutable material-state cursor per symbol
- [x] Record append-only meaningful state changes
- [x] Create at most one automatic decision snapshot per transition
- [x] Commit cursor, event, and optional snapshot atomically
- [x] Use cursor versions for restart safety and idempotency
- [x] Establish the first observation as a silent baseline
- [x] Detect observed position closure and reopening without invented execution facts
- [x] Keep account and coach failures from becoming trading-state changes
- [x] Expose read-only state-change feed and monitor status APIs
- [x] Show recent state changes, monitor status, and automatic snapshots in the frontend

## PR #18 — Production Hosting Foundation (current)

- [x] Railway project configuration for `copilot-api`, `copilot-web`, and PostgreSQL
- [x] Deterministic FastAPI and Next.js production containers
- [x] Native GitHub auto-deploy preparation and health checks
- [x] Alembic pre-deploy migration and production PostgreSQL fail-fast configuration
- [x] Backend-only Bybit secrets and private API networking
- [x] Signed-cookie single-user workstation access gate
- [x] One backend replica while market and order-book state remain process-local
- [x] Safe SQLite-to-PostgreSQL journal transfer utility
- [x] Canonical plan-aware portfolio structural-risk summary
- [x] One-command local development with `make dev`

## PR #19 — Live Trading Workspace

- [x] Lightweight Charts workspace with public historical candles and 12/21 EMA overlays
- [x] Timeframe and symbol selection, plus persisted manual horizontal zones
- [x] Read-only, stop-based staged sizing with exchange quantity constraints and conservative friction
- [x] Correlation-group capacity gates and novel-evidence requirements for adds
- [x] Leverage shown only as a margin estimate; it never increases risk quantity
- [ ] Streamed candle updates, richer markers, trendline editing, and flow panels remain future work

## PR #20 — Setup Scanner

- Persistent watchlists and multi-symbol market evaluation
- Trend Pullback candidates
- Range Extreme / Deviation-Reclaim candidates
- Macro Breakout / Reclaim candidates
- Explicit context and execution timeframe hierarchy
- Structural-location distance and mid-range noise suppression
- States: `IGNORE`, `WATCH`, `APPROACHING_LOCATION`, `AT_LOCATION`, `REACTION_DEVELOPING`, `APPROACHING_BREAKOUT`, `BREAKOUT_ATTEMPT`, `ACCEPTANCE_PENDING`, `BREAKOUT_ACCEPTED`, `RETEST_PENDING`, `TRIGGER_ARMED`
- No numeric setup score

## PR #21 — Lower-Timeframe Trigger Engine

- Deviation/reclaim, failed breakout, breakout acceptance, and retest hold/failure
- Failed auction, rejection/acceptance, structure rotation, flow confirmation, and continuation
- Reaction and trigger remain separate evidence
- Macro Breakout lifecycle from macro context through acceptance, retest, probe, and confirmed continuation
- Manual trendlines, support/resistance zones, range boundaries, and macro targets first

## PR #22 — Realtime Alerts

- Backend WebSocket or suitable realtime browser transport
- Replace five-second polling for critical transition delivery
- Deduplicate and cool down `APPROACHING LOCATION`, `APPROACHING BREAKOUT`, `TRIGGER ARMED`, `PROBE ALLOWED`, staged ADD permissions, risk breach, thesis warning, and invalidation alerts
- Keep all exchange execution manual

## PR #23 — Trader Development Analytics

- Expectancy by playbook, regime, and location
- MAE, MFE, and entry timing relative to trigger
- Early and oversized entry frequency
- Initial risk allocation and staged sizing adherence
- Adds without new confirmation and counter-trend frequency
- Probe-first versus large-initial-entry outcomes
- Risk-policy violations, plan deviations, location quality, execution quality, and lifecycle adherence
- No motivational numeric scoring

## MVP acceptance journey

A trader opens one hosted URL, sees a symbol approaching a macro structural location, observes breakout attempt and acceptance separately, receives a lower-timeframe trigger, and gets account-equity-based risk and stage quantity. The trader manually executes on Bybit. Read-only account reconciliation observes fills, later evidence may unlock separately planned adds within remaining portfolio risk, and the journal preserves the full decision lifecycle for factual review.
