# Railway deployment

## Topology and security boundary

```text
Internet
  ↓
copilot-web (public Next.js service, signed-cookie access gate)
  ↓  Railway private network
copilot-api (private FastAPI service, one replica)
  ↓
Railway PostgreSQL
```

The browser calls same-origin `/backend/...` routes only. Next.js reads `COPILOT_API_BASE_URL` on the server and forwards requests to FastAPI over Railway private networking. FastAPI needs no public domain for normal use. Bybit credentials exist only on `copilot-api`; never use `NEXT_PUBLIC_` for backend addresses or secrets.

Run exactly one backend replica. L2 books, market features, reaction state, and stream state are process-local. Shared state and leader ownership are prerequisites for horizontal backend scaling.

## Create the Railway project

1. Connect this GitHub repository to a new Railway project.
2. Add PostgreSQL.
3. Add `copilot-api` from the repository with root directory `/` and config file `/railway.toml`.
4. Add `copilot-web` from the same repository with root directory `/web` and config file `/web/railway.toml`.
5. Give only `copilot-web` a public Railway domain.
6. Keep `copilot-api` private and note its Railway private DNS URL, including the internal port.
7. Configure both services to deploy from `main` through Railway's native GitHub integration.

The backend Docker image uses Python 3.12 and starts Uvicorn on `0.0.0.0:$PORT`. Its pre-deploy command runs `alembic upgrade head`. The frontend image uses Node 24, builds the Next.js standalone output, and starts on `0.0.0.0:$PORT`.

## Backend variables

Set on `copilot-api`:

```text
APP_ENV=prod
DATABASE_URL=${{Postgres.DATABASE_URL}}
BYBIT_BASE_URL=https://api.bybit.com
BYBIT_WS_LINEAR_URL=wss://stream.bybit.com/v5/public/linear
BYBIT_READ_ONLY_SYNC_ENABLED=true
LIVE_STREAM_ENABLED=true
STATE_CHANGE_MONITOR_ENABLED=true
STATE_CHANGE_MONITOR_INTERVAL_SECONDS=5
LIVE_STREAM_SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT
BYBIT_API_KEY=<Railway secret>
BYBIT_API_SECRET=<Railway secret>
```

Standard `postgresql://` and already-correct `postgresql+asyncpg://` URLs are accepted. Production fails during configuration if the database URL is missing or resolves to SQLite.

## Frontend variables

Set on `copilot-web`:

```text
COPILOT_API_BASE_URL=http://copilot-api.railway.internal:<private-port>
COPILOT_ACCESS_PASSWORD=<long unique password>
COPILOT_SESSION_SECRET=<at least 32 random characters>
```

Do not set `COPILOT_AUTH_DISABLED` in production. The development-only bypass is ignored when `NODE_ENV=production`. The session cookie is signed, Secure, HttpOnly, SameSite=Strict, and expires after 12 hours.

## Health and readiness

- Backend: `GET /health` checks process health only. Temporary Bybit degradation does not make the process unhealthy.
- Frontend: `GET /api/health` returns 200 only when production access-control variables are configured.
- Railway should use the health paths in each service's `railway.toml`.

## One-time local SQLite transfer

Keep the local database outside Git and back it up before transfer. Apply Alembic migrations to the empty Railway PostgreSQL database, then run from a trusted machine with private network or temporary authorized database access:

```bash
python -m trading_copilot.cli migrate-database \
  --source sqlite+aiosqlite:///./trading_copilot.db \
  --target "$PRODUCTION_DATABASE_URL"
```

The transfer uses SQLAlchemy column values directly, preserving IDs, timestamps, decimals, foreign keys, and append-only rows across plans, rules, snapshots, execution events, reviews, watched setups, Fib/range definitions, coach cursors, and state-change events. It runs in one target transaction and refuses to start if any target application table already contains data. A repeat therefore fails safely instead of duplicating history.

After transfer, compare per-table counts and inspect representative plans, snapshots, and state changes before enabling the monitor. Never upload or commit the SQLite file.

## Deployment verification

1. Confirm the backend pre-deploy migration succeeds and `/health` returns 200.
2. Confirm no public domain is assigned to `copilot-api`.
3. Open the public frontend domain and verify redirect to `/login`.
4. Verify an invalid password is rejected and a valid password sets the signed session cookie.
5. Confirm the dashboard loads account, portfolio, coach, journal, and state-change data through `/backend/...`.
6. Confirm the portfolio known-risk values agree with plan-backed position coach values and unknown exposure yields `INDETERMINATE`.
7. Confirm the browser network panel and built client chunks contain no private backend address or secrets.
8. Confirm logout clears the cookie and protected routes redirect to login.
9. Confirm `copilot-api` has one replica.

## Rollback

Railway retains deployment history. Roll back `copilot-web` and `copilot-api` to the prior successful images. Database migrations require explicit review: do not automatically downgrade a production database containing new writes. Restore the pre-deploy database backup or run a reviewed Alembic downgrade only when the migration's data implications are understood.

## Future realtime transport

The current frontend proxy is appropriate for five-second polling. PR #22 may add an authenticated realtime channel. It should preserve server-only backend routing and avoid inserting REST or database waits into the Bybit WebSocket → in-memory evaluation → coach/state-transition hot path.
