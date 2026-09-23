# Bybit Trading Copilot

A decision-support and execution-discipline system for discretionary crypto trading on Bybit.

## Product principles

- **No autonomous trading.** The system analyzes; the trader executes manually.
- **One playbook, not indicator soup.** Core workflow: regime → location → reaction → execution → review.
- **Risk first.** Every trade is invalidation-first, then position sizing.
- **Execution is a lifecycle.** Probe/add/reduce/exit decisions are tracked separately from the market thesis.
- **Research stays isolated.** Experimental ideas do not enter the live playbook until tested.

## V1 strategy model

- 12 / 21 EMA regime model
- Fibonacci levels: 0.236, 0.382, 0.500, 0.618, 0.786, 0.886, 1.000
- Stoch RSI: 14 / 14 / 3 / 3
- Range trading
- Bybit market data: klines, trades, L2 order book, funding, open interest
- Trend Pullback / Range Long / Range Short playbooks
- Setup state machine instead of a numeric score
- Manual execution plans and risk-gated adds
- Portfolio risk budget with correlated-position awareness
- Hindsight-safe entry replay with categorical execution grading, MAE/MFE, and thesis-flip detection

## Live market intelligence

The live collector subscribes to Bybit linear-perpetual public WebSocket feeds for:

- `orderbook.50.{symbol}` — snapshot + delta L2 reconstruction
- `publicTrade.{symbol}` — taker-side aggressive trade flow
- `tickers.{symbol}` — last/mark/index price, funding and open interest

Derived state currently includes:

- best bid / ask and spread
- depth and imbalance within 10 / 25 / 50 bps
- 60-second aggressive buy/sell delta
- 60-second book additions/removals
- near-term bid/ask replenishment
- current funding and open interest
- 1s / 1m feature bars
- buy/sell absorption and continuation hypotheses from flow-vs-price response

Book removals are deliberately labelled **removed**, not **cancelled**: L2 deltas alone cannot prove whether size disappeared because it was cancelled or filled.

## Playbook and execution layers

The playbook engine returns explicit states such as `CONTEXT_VALID`, `AT_LOCATION`, `WAITING_FOR_TRIGGER`, and `READY`. A level is treated as a location, never as an automatic entry.

The execution coach then evaluates structural risk, multi-fill weighted entry, take-profit R-multiples, correlated exposure, and proposed scale-ins. An add is policy-compliant only when its predefined confirmation is met and projected risk remains inside the configured correlation-group budget.

The service never sends exchange orders.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
alembic upgrade head
uvicorn trading_copilot.main:app --reload
pytest
```

Then open `http://127.0.0.1:8000/docs`.

### Frontend workstation

The read-only Next.js dashboard lives in `web/` and talks only to this FastAPI service. It never
receives Bybit credentials and provides no order, cancellation, transfer, or withdrawal actions.

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Set `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` when the backend runs at its default address,
then open `http://localhost:3000`. The Next.js development server proxies typed browser requests to
FastAPI so exchange connectivity and credentials remain server-side.

To enable the live collector, set:

```bash
LIVE_STREAM_ENABLED=true
LIVE_STREAM_SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT
```

Then query, for example:

```text
GET /live/BNBUSDT/state
GET /live/ETHUSDT/reaction
POST /playbook/evaluate
POST /execution/plan
POST /execution/project-add
```

## API

- `GET /health`
- `GET /live/status`
- `GET /live/{symbol}/state`
- `GET /live/{symbol}/reaction`
- `GET /market/{symbol}/snapshot`
- `POST /analysis/fib`
- `POST /playbook/evaluate`
- `POST /risk/position-size`
- `POST /risk/portfolio`
- `GET /market/{symbol}/chart` — public historical candles with 12/21 EMA values for the workspace
- `GET|POST /chart-structures` and `PATCH|DELETE /chart-structures/{id}` — persisted manual chart structure
- `POST /workspace/sizing` — read-only, stop-based staged sizing and margin estimate
- `POST /execution/plan`
- `POST /execution/project-add`
- `POST /trade-plans`
- `GET /trade-plans` and `GET/PATCH /trade-plans/{id}`
- `POST/GET /trade-plans/{id}/snapshots`
- `POST/GET /trade-plans/{id}/execution-events`
- `POST/GET /trade-plans/{id}/review`
- `GET /trade-plans/{id}/replay-review?post_hours=12` — hindsight-safe entry diagnostics and MAE/MFE replay
- `GET /positions/{symbol}/coach`
- `GET /state-changes?symbol=BNBUSDT&limit=50`
- `GET /state-change-monitor/status`

## Persistent journal

The journal uses SQLAlchemy's async API and Alembic. Local development defaults to SQLite;
set `DATABASE_URL=postgresql+asyncpg://...` for Postgres or Supabase. Run migrations before
starting the service. Decision snapshots and execution events expose create/list operations only
and preserve the recorded history. Trade reviews record factual outcomes and confidence without a
numeric quality score.

The entry-replay endpoint reconstructs the first recorded `ENTRY`/`PROBE` using public historical
Bybit candles and the journal state that existed at execution time. Entry grading uses only bars
fully closed before the fill. Post-entry candles are reserved for MAE/MFE and invalidation-touch
replay, preventing future price action from rewriting the entry decision. It also flags an
opposite-direction `EXIT`/`INVALIDATE` in the prior two hours as a same-session thesis flip.

Exchange stop fields remain account-risk proxies. Journal snapshot metadata should identify stop
provenance as `execution_plan`, `exchange_order`, `inferred`, or `unknown`; the persistence layer
does not infer that an exchange stop was the trader's original structural invalidation.

## Live position coach

`GET /positions/{symbol}/coach` combines the current read-only Bybit position, active journal
plan, account and correlation-group risk, 1H/4H market structure, persisted location definitions,
and live reaction evidence. Its open-position state precedence is `INVALIDATE`, explicit
`EXIT/REDUCE`, eligible `ADD`, then `HOLD`.

Market context and location come from the existing playbook evaluator, including its 4H regime
requirements and Fib/range rules. An add also requires a supported condition or ADD rule that was
stored in the plan before the decision. Attractive current evidence without a predefined add plan
remains `HOLD`. Unknown or unevaluable add conditions block permission and are reported.

Risk policy is `PASS` only when all material correlated positions have known structural risk and
the known total is within the plan budget. It is `BREACH` when known risk exceeds that budget and
`INDETERMINATE` when risk data or a plan budget is incomplete. Plan invalidations have
`execution_plan` provenance; exchange-derived stops retain their separate provenance.

Missing plans and missing or stale live reaction data degrade safely to `HOLD` with adds blocked.
The coach reports evidence and next conditions. It does not predict prices, place orders, or write
journal events when queried.

## State-change monitor and decision feed

The optional background monitor compares a deliberately small projection of each position coach
with the last persisted state. It records execution-state, add-permission, risk-policy, reaction,
playbook, plan, thesis-warning, invalidation, and observed position open/closed transitions. Price,
PnL, indicator, funding, open-interest, and equity ticks do not create events by themselves.

The first successful observation for a symbol stores a silent baseline. It does not claim that a
position opened while the service was offline. A later observed transition is written as one
append-only state-change event. When a compatible active plan, open position, and real mark price
exist, the same transaction also creates one automatic `DecisionSnapshot`; `action_taken` remains
empty because coach state does not prove trader execution. Cursor update, event, and optional
snapshot commit atomically and use a symbol/version uniqueness constraint for idempotency.

Account failures never become position-close events, and failed or conflicting coach evaluations
do not overwrite good cursor state. Enable the monitor explicitly:

```bash
STATE_CHANGE_MONITOR_ENABLED=true
STATE_CHANGE_MONITOR_INTERVAL_SECONDS=5
```

The dashboard's **Recent state changes** panel polls the read-only feed every five seconds. The
System page reports monitor status, and automatic snapshots appear in the existing Journal with an
`AUTO SNAPSHOT` label. This layer adds observation and journaling only; it adds no exchange
execution capability.

## Docs

- [Playbooks](docs/PLAYBOOKS.md)
- [Microstructure](docs/MICROSTRUCTURE.md)
- [Execution coach](docs/EXECUTION.md)
- [Entry replay review](docs/ENTRY_REPLAY.md)
- [Roadmap](docs/ROADMAP.md)

## Production and local supervision

Run both development services from one terminal:

```bash
make dev
```

The supervisor prefixes API/web output and stops both process groups on Ctrl+C. Local auth is explicitly disabled by the supervisor only outside production. `make test` runs backend lint/tests and frontend lint/build.

Production targets Railway with a public authenticated Next.js service, a private one-replica FastAPI service, and PostgreSQL. The browser uses same-origin `/backend/...`; only the Next.js server reads `COPILOT_API_BASE_URL`. Production fails closed without PostgreSQL and frontend access secrets. See [Deployment](docs/DEPLOYMENT.md).

The portfolio summary and position coach share the same plan-aware structural-risk calculation. Plan invalidations have `execution_plan` provenance, exchange stops retain `exchange_order`, and any unknown correlated exposure makes total risk `INDETERMINATE` rather than displaying a fabricated zero total.
