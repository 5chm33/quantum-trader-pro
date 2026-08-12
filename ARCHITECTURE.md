# Quantum Trader Pro Architecture Reference

Quantum Trader Pro is a finite, deterministic simulation pipeline. Its architecture separates pure trading-domain logic from data acquisition, execution, and persistence so no strategy or portfolio component can reach a network broker directly.

![System architecture](docs/assets/system_architecture.png)

## Design Goals

| Goal | Architectural response |
|---|---|
| Prevent accidental capital deployment | The execution policy represents only `simulation`; paper and live strings are rejected before adapter construction |
| Make results reproducible | Input bytes are checksummed, clocks are injected, identifiers are content-derived, event order is stable, and reports use canonical values |
| Keep accounting inspectable | Cash, positions, average cost, fees, realized P&L, unrealized P&L, and equity reconcile at every observation |
| Model causality honestly | An intent generated from event *t* can fill only on a later eligible event |
| Fail closed | Invalid data and risk failures stop new exposure; a recorded full exit remains permissible |
| Prevent duplicate cores | A process-wide advisory lock records and guards the active PID |
| Preserve audit evidence | Every market event, signal, intent, risk decision, accepted order, cancellation, fill, rejection, risk halt, equity point, and run boundary is appended to SQLite |

## Package Boundaries

| Package | Owns | Must not own |
|---|---|---|
| `domain` | Immutable value objects, portfolio aggregate, risk policy, strategy, and clocks | Filesystem, SQLite, networking, CLI parsing |
| `application` | Engine orchestration, lifecycle lock, metrics, and report generation | Broker-specific SDK calls or hidden global state |
| `ports` | Structural interfaces for market data, broker, and event storage | Concrete implementation logic |
| `adapters` | CSV replay, deterministic simulated fills, and SQLite persistence | Strategy decisions or risk policy |
| `cli` | Explicit configuration, dependency assembly, output protection, and preflight | Trading logic or direct broker access |

The package uses dependency injection rather than singleton registries. The engine receives fully constructed port implementations and domain services; replacing an adapter does not alter the strategy contract.

## Runtime Sequence

| Step | Action | Recorded evidence |
|---:|---|---|
| 1 | Validate the requested mode and output path | Preflight output or safe error |
| 2 | Acquire the single-instance lock | PID in `.simulation.lock` |
| 3 | Open the checksummed, strictly ordered CSV stream | Source identifier in `run_started` |
| 4 | Record the market event | `market_event` |
| 5 | Ask the simulated broker to reconcile fills from previously approved orders | `fill` or `fill_rejected` |
| 6 | Apply valid fills, then recheck buy commitment, exposure, and cash reserve at the actual fill price | `fill` and optional `risk_halt` |
| 7 | Mark positions at the current close and reconcile equity | `equity` |
| 8 | Observe drawdown and realized-loss circuit breakers | Optional `risk_halt` |
| 9 | Generate a transparent target-allocation signal | `signal` |
| 10 | If halted with exposure, replace the target with zero | `risk_override_signal` |
| 11 | Convert the effective target into a share-delta intent | `order_intent` |
| 12 | Apply duplicate, conservative execution-cost, notional, exposure, reserve, drawdown, and loss controls | `risk_decision` |
| 13 | Queue an approved order for a later event | `order_accepted` |
| 14 | Cancel any still-pending orders and mark remaining positions to the final close | `order_canceled_end_of_test` when needed |
| 15 | End with a final reconciled portfolio and six-artifact report bundle | `run_completed` plus output artifacts |

## Core Invariants

The domain models reject non-finite decimals, naive timestamps, invalid symbols, inconsistent OHLC bars, non-positive quantities and prices, negative volume, invalid target fractions, and contradictory risk decisions. `EquityPoint` requires `equity == cash + market_value`, preventing a report from carrying an unreconciled total.

The portfolio is long-only. A buy that would create negative cash and a sell larger than the owned position both raise errors. Average cost is updated by weighted purchase price; realized P&L is recognized on sales, while fees are tracked separately and deducted from cash.

The risk manager remembers every intent ID. A repeated intent is denied even if all other limits pass. Buy sizing reserves fixed and per-share fees plus declared slippage and a conservative next-open gap buffer before applying order, position, and cash-reserve limits. The actual buy fill is rechecked at its execution price. New buys are denied after an emergency halt, while a sell is limited to the owned quantity and remains eligible as a risk-reducing exit.

## Deterministic Identity

Market-event, signal, intent, order, fill, and round-trip trade IDs are generated from stable business fields with namespaced UUIDv5 identifiers. Run IDs are generated from the input source identifier and canonical configuration. The SQLite store serializes payloads as sorted compact JSON and records a SHA-256 digest for each payload.

A rerun with the same source bytes and configuration produces the same IDs, event ordering, reports, CSV outputs, and SQLite database. The v0.1.0 retained validation matched byte for byte; the updated bundle adds deterministic `round_trip_trades.csv` and is revalidated in the A+ evaluation phase.

## Simulated Execution Contract

The simulated broker queues only an allowed decision whose intent and correlation IDs match. It rejects duplicate intent submission and decisions that enlarge the requested quantity. A queued order cannot fill on an event with a timestamp equal to or earlier than its intent timestamp.

Eligible fills use the next event’s open. Buy prices add the configured basis-point slippage; sell prices subtract it. Fees combine a fixed per-order amount and a per-share amount. Before a buy is queued, risk sizing uses the reference close plus declared slippage and an execution-gap buffer; the realized buy fill is rechecked afterward. The model is intentionally simple and does not claim to reproduce partial fills, queue position, spread dynamics, market impact, auctions, or halts.

## Event Ledger

The SQLite adapter uses WAL mode, full synchronous commits, foreign-key enforcement, and a busy timeout. Python’s `sqlite3` module provides the process-local interface to SQLite,[1] while canonical JSON payload hashes make later integrity checks deterministic.

| Event type | Meaning |
|---|---|
| `run_started` / `run_completed` | Explicit simulation boundary and final state |
| `market_event` | Validated OHLCV observation and source provenance |
| `fill` / `fill_rejected` | Simulated execution result and portfolio acceptance |
| `risk_halt` | First observed circuit-breaker transition with its explicit reason |
| `order_canceled_end_of_test` | Pending order canceled under the declared finite-run policy |
| `equity` | Reconciled cash, market value, P&L, fees, and total equity |
| `signal` | Original deterministic strategy output |
| `risk_override_signal` | Emergency target-to-cash substitution |
| `order_intent` | Requested side and quantity before risk policy |
| `risk_decision` | Explicit allow/deny result and approved quantity |
| `order_accepted` | Approved intent queued for a later observation |
| `risk_observation_error` | Defensive halt caused by unexpected risk-evaluation failure |

## Process Lifecycle

`SingleInstanceLock` uses a non-blocking native byte lock through `msvcrt` on Windows and `flock` through `fcntl` on POSIX.[2] [3] The lock file contains the owner PID for operator visibility. A second process receives a deterministic error instead of starting another core. The CLI also refuses to reuse report, ledger, or trade-attribution files unless the operator supplies `--overwrite`.

The engine is finite: it processes a bounded local input and exits. The systemd template in `deployment/` uses `Type=oneshot` and does not turn the project into an always-on market daemon.

## Extension Rules

A new strategy must implement the `Strategy` protocol and return only a target fraction plus rationale; it must not submit orders. A new data adapter must emit strictly increasing, timezone-aware `MarketEvent` values with source provenance. A new broker adapter must preserve intent/decision ID matching, idempotency, next-event causality, explicit fees, and reconciliation.

A paper-broker adapter would require a separate threat model, credential loader, broker sandbox account, idempotent client order IDs, order-status reconciliation, outage and rate-limit handling, contract tests, and a disabled-by-default build path. A live-broker adapter is intentionally outside this repository.

## References

[1]: https://docs.python.org/3/library/sqlite3.html "Python Standard Library — sqlite3"
[2]: https://docs.python.org/3/library/fcntl.html "Python Standard Library — fcntl"
[3]: https://docs.python.org/3/library/msvcrt.html "Python Standard Library — msvcrt"
