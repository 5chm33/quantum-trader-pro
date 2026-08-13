# Quantum Trader Pro Architecture Reference

Quantum Trader Pro is a finite, deterministic simulation pipeline with a separately isolated, disabled-by-default paper-broker boundary. Its architecture separates pure trading-domain logic from data acquisition, execution, reconciliation, and persistence so no strategy or portfolio component can reach a network broker directly.

![System architecture](docs/assets/system_architecture.png)

## Design Goals

| Goal | Architectural response |
|---|---|
| Prevent accidental capital deployment | Every public command remains simulation-only; the separate paper adapter is pinned to Alpaca’s paper origin and requires an unavailable operator arming path, while live execution is unrepresentable |
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
| `application` | Engine orchestration, lifecycle lock, metrics, reporting, and normalized reconciliation | Broker-specific HTTP calls or hidden global state |
| `ports` | Structural interfaces for market data, simulation broker, external broker, event storage, and broker journal | Concrete implementation logic |
| `adapters` | CSV replay, deterministic simulated fills, fixed-origin paper HTTP, and SQLite persistence | Strategy decisions or risk policy |
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

## Paper State and Reconciliation Sequence

The paper components are not reachable from the CLI, but their internal startup contract is explicit and tested. The reconciler first verifies the mode-`0600`, full-sync SQLite journal with `PRAGMA integrity_check`, then reads the paper account, positions, open orders, unresolved local submissions, and paginated fill activities. Deterministic client IDs resolve ambiguous submissions before any future retry. Fill activities are assigned to their referenced broker orders, inserted exactly once by execution ID, and used to project strategy-owned positions. Foreign orders, unexplained bot-namespace orders, missing activity ownership, unknown broker states, account restrictions, stalled pagination, and position mismatches all produce a non-ready report.

A reconciliation commit atomically records the account snapshot, latest order projections, complete position snapshot, new fills, resolved submissions, activity checkpoint, and redacted mismatch report. The checkpoint advances only in the same transaction as the retained activities. A repeated run is idempotent; the one permitted duplicate evolution enriches a previously unresolved fill with its broker-resolved client order ID while requiring every economic and identity field to remain unchanged.

After a ready reconciliation, the pre-trade controller reads the paper account, broker clock, positions, open orders, bounded calendar day, current asset eligibility, real-time IEX/SIP quote, and durable submission timestamps again. It requires regular session hours using the broker’s holiday and early-close calendar, rejects stale or future snapshots, validates bid/ask prices, sizes, spread, limit/day policy, buying power, open-order commitment, gross and symbol exposure, cash reserve, owned sell quantity, rolling order rate, and per-session order count. Alpaca documents calendar-provided open/close values, timestamped latest quotes, and a 200-request-per-minute account throttle; the local transports impose a lower 120/minute default with a hard 180/minute ceiling.[4] [5] [6]

These controls remain unreachable from the public CLI. A decision can only deny or approve a normalized order object; it cannot submit one, acquire credentials, promote an execution mode, or bypass the still-missing operator and acceptance gates.

## Operator and Secret Boundary

The paper credential adapter reads three allowlisted files from an absolute out-of-band directory: paper key ID, paper secret key, and a minimum 256-bit operator control key. It rejects symlinks, traversal, non-regular files, foreign ownership, group/world-readable files, oversized content, multiline text, whitespace, and relative directories. Credential and bundle representations are always redacted. No public command constructs this bundle.

The mode-`0600`, full-sync operator database starts paused. The pre-trade controller reads that state before reconciliation or any external market/broker read. Pause requires no approval. Resume requires a one-use HMAC approval bound to the exact paper action, namespace, code/configuration/account fingerprints, 128-bit nonce, acknowledgment, and expiry; it then requires a valid paper context, database integrity, ready reconciliation, and account readiness.

The cancel action pauses first, consumes a distinct approval, selects only deterministic strategy-owned client IDs, verifies a terminal broker state for every cancellation, requires zero residual owned orders, reconciles, hashes its summary, and remains paused. Foreign orders are preserved. Position flattening is intentionally absent, and the live execution gate still rejects every caller. The complete contract is in [Operator Controls and Secret Isolation](docs/OPERATOR_CONTROLS.md).[7] [8]

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

A new strategy must implement the `Strategy` protocol and return only a target fraction plus rationale; it must not submit orders. A new data adapter must emit strictly increasing, timezone-aware `MarketEvent` values with source provenance. A new external broker adapter must preserve deterministic client identity, pre-submit durability, no-blind-retry behavior, per-object payload hashes, pagination, normalized states, and reconciliation.

The current Alpaca adapter is paper-origin-only and has no operator command. Its strict credential source, broker calendar and stale-state controls, request budgets, durable journal, no-blind-retry executor, reconciliation, pause/resume/cancel controls, partial-fill projection, cancel-race handling, literal subprocess termination/recovery, simulated storage exhaustion, and transactional failure drills are implemented behind internal interfaces. Enabling an authenticated paper command still requires service-manager restart and host power-loss evidence, a validated flatten design, broker-authenticated acceptance tied to an exact commit/configuration/account fingerprint, and operator review. A live-broker adapter remains intentionally unavailable. See [`docs/FAILURE_INJECTION.md`](docs/FAILURE_INJECTION.md) for the executable failure matrix.

## References

[1]: https://docs.python.org/3/library/sqlite3.html "Python Standard Library — sqlite3"
[2]: https://docs.python.org/3/library/fcntl.html "Python Standard Library — fcntl"
[3]: https://docs.python.org/3/library/msvcrt.html "Python Standard Library — msvcrt"
[4]: https://docs.alpaca.markets/us/reference/getcalendar-1 "Alpaca Trading API — Get US Market Calendar"
[5]: https://docs.alpaca.markets/us/reference/stocklatestquote-1 "Alpaca Market Data API — Latest Quote"
[6]: https://alpaca.markets/support/usage-limit-api-calls "Alpaca Support — API Usage Limit"
[7]: https://docs.alpaca.markets/us/docs/authentication "Alpaca — Authentication"
[8]: https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#Credentials "systemd.exec — Credentials"
