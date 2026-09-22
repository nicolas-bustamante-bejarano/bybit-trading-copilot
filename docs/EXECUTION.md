# Execution Coach

The execution coach exists to improve manual execution after a playbook setup becomes actionable. It never sends, changes, or cancels an exchange order.

## Core rule

```text
STRUCTURAL INVALIDATION → RISK BUDGET → POSITION SIZE
```

The stop is not tightened merely to make a desired position fit the account. Position size must adapt to the structural stop.

## Correlated risk

Positions sharing a `correlation_group` consume one risk budget. For example, simultaneous ETH and BNB shorts can both use `crypto_beta` so the system treats them as one directional thesis rather than two independent trades.

The default group risk budget is 2% of account equity, configurable per plan.

## Multi-fill positions

A position can contain multiple execution legs. The system keeps the original fills and calculates:

- total quantity
- weighted average entry
- exact structural risk of each fill to the common hard stop
- current trade risk
- other risk already consuming the same correlation bucket
- remaining risk capacity

## Take-profit ladder

Each target includes a price and fraction of the position to close. The coach reports the R-multiple from the current weighted average entry and hard stop.

A profitable target is not selected automatically. Fib levels, range boundaries, prior structure, and live reaction remain part of the trader's analysis.

## Adds

Before an add, the system projects:

- risk per added unit
- total add risk
- projected correlation-group risk
- maximum add quantity that fits the remaining risk budget
- projected weighted entry
- projected target R-multiples

An add passes policy only when both are true:

1. the predefined confirmation condition is marked as met; and
2. projected correlated risk remains inside the configured budget.

A lower price alone is never a valid reason to average into a long, and a higher price alone is never a valid reason to average into a short.

## Lifecycle metadata

Plans can be tagged with:

```text
WAIT → PROBE → ADD → HOLD → REDUCE → EXIT / INVALIDATE
```

The current release records the intended stage but does not autonomously transition or trade.

## API

- `POST /execution/plan` — summarize current risk and TP R-multiples.
- `POST /execution/project-add` — evaluate a proposed scale-in against confirmation and risk policy.

Both endpoints are decision-support only.
