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
uvicorn trading_copilot.main:app --reload
pytest
```

Then open `http://127.0.0.1:8000/docs`.

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
- `POST /execution/plan`
- `POST /execution/project-add`

## Docs

- [Playbooks](docs/PLAYBOOKS.md)
- [Microstructure](docs/MICROSTRUCTURE.md)
- [Execution coach](docs/EXECUTION.md)
- [Roadmap](docs/ROADMAP.md)
