# Failure Injection and Recovery Evidence

Quantum Trader Pro treats **order identity, durable intent, broker acknowledgement, fill reconciliation, and operator state as separate facts**. The paper execution service does not infer one from another, and it never retries a submission merely because a response was not observed. This document records the deterministic failure tests completed before an authenticated sandbox acceptance run exists.

> **Scope boundary:** these tests exercise the local orchestration, durable stores, normalized paper adapter, and deterministic broker doubles. They do not claim that real brokerage connectivity, a physical disk, a kernel, a filesystem, or a power-loss event has been certified.

## Submission State Contract

The order path is deliberately asymmetric. Before any externally visible action, a canonical order payload hash and deterministic client order ID are persisted. A unique attempt digest then claims the order as `started`. Only that claimed state may reach the paper adapter. A response becomes `acknowledged`; broker-authoritative reconciliation becomes `reconciled`. Known validation rejection becomes terminal `rejected`. Any uncertain outcome becomes `ambiguous`, pauses the system, and requires client-ID lookup and reconciliation rather than a second submission.

SQLite documents transaction atomicity as an all-or-nothing property and uses a commit record in WAL mode. The journals use WAL with `synchronous=FULL`, and every reconciliation projection is committed in one transaction.[1] [2] Runtime startup and recovery also invoke SQLite integrity checks, while acknowledging that software checks cannot prove the behavior of every storage stack.[3]

| Injected boundary or failure | Durable expectation | External-side-effect expectation | Verified recovery behavior |
|---|---:|---:|---|
| After pre-trade assessment | No submission row | Zero submissions | A clean later attempt may start normally. |
| After durable persistence | `persisted` | Zero submissions | Recovery confirms absence by client ID and marks the never-started intent `rejected`. |
| After exclusive start claim | `started` | Zero submissions | Absence remains ambiguous; the system stays paused and does not retry. |
| After broker response, before acknowledgement | `started` | Exactly one submission | Restart performs client-ID lookup, acknowledges, and reconciles without a second POST. |
| After acknowledgement | `acknowledged` | Exactly one submission | Restart reconciles the same broker order and commits terminal state. |
| After reconciliation commit | `reconciled` | Exactly one submission | Repeated recovery is idempotent and retains the same broker identity. |
| Operator pause after preflight | No submission row | Zero submissions | Submission is blocked immediately. |
| Operator pause after persistence or start | Terminal `rejected` | Zero submissions | The never-submitted intent cannot be replayed. |
| HTTP 422 with no broker order | Terminal `rejected` | One POST, no retry | Lookup occurs before classification; response content remains redacted. |
| HTTP 500 with no broker order | `ambiguous` | One POST, no retry | Operator intervention and reconciliation are required. |
| HTTP 503 with broker order visible | Broker order recovered | One POST, no retry | Client-ID lookup returns and validates the created order. |
| Two partial fills across restart | Two immutable execution IDs | No duplicate economics | Quantities project exactly once; the activity checkpoint survives restart. |
| Fill overtakes cancel request | Terminal `filled` observation | One cancel request | Resulting position is reconciled honestly; the operator state remains paused. |
| Mid-reconciliation SQLite trigger failure | Original transaction state | No partial projection | Account, order, position, fill, checkpoint, report, and submission resolution roll back together; clean retry succeeds. |
| Process restart during kill action | `in_progress` action retained | No automatic replay | Resume and approval reuse remain blocked; one explicit terminal failure record is allowed. |
| Corrupt, symlinked, broadly readable, foreign, or unsafe journal path | Store refuses to open | No broker access | Fail closed before database or network use. |

## Tested Invariants

The failure suite establishes that **one deterministic client order ID can cause at most one tested paper submission side effect**. Existing durable state blocks ordinary execution, unresolved state blocks every new order globally, and recovery has no submission method. All post-start unexpected exceptions are treated as ambiguous, not rejected. All uncertainty paths pause the operator store.

The reconciliation transaction retains fills by immutable execution ID, rejects conflicting duplicate economics, permits only one ownership-enrichment transition, validates order-state monotonicity, and advances an activity checkpoint only in the same transaction as the retained evidence. Partial executions are projected from fills rather than inferred from final order status.

The operator path starts paused, consumes action-specific HMAC approvals once, persists in-progress state before cancellation, ignores foreign orders, verifies terminal broker outcomes, reconciles after cancellation, and remains paused even after success. A fill that wins the cancel race is therefore represented as a fill and position, not mislabeled as a successful cancellation.

## Reproduction

Run the focused failure suite from a source checkout:

```bash
python -m pytest \
  tests/integration/test_paper_execution_failures.py \
  tests/integration/test_paper_reconciliation.py \
  tests/integration/test_operator_actions.py \
  tests/unit/test_alpaca_paper_adapter.py \
  tests/unit/test_sqlite_broker_journal.py \
  tests/unit/test_sqlite_operator_control.py
```

Run the complete acceptance gate:

```bash
make quality
```

At the phase-twelve checkpoint, the full local suite reports **160 passing tests and 90.23% branch coverage**. The protected public workflow repeats formatting, lint, strict typing, branch coverage, security analysis, evidence verification, package construction, installed-wheel preflight, and one-click launcher checks across Python 3.11, Python 3.12, Linux, macOS, and Windows.

## Residual Risks and Required External Evidence

The hard-crash hook raises outside normal `Exception` handling and the tests close and reopen both SQLite stores, but this is not a literal process `SIGKILL` or physical power interruption. A later paper-acceptance phase must add subprocess termination, service-manager restart, disk-space exhaustion, authenticated timeout behavior, rate-limit responses, market-data disconnection, and real partial-fill evidence where the sandbox can produce it.

The configured external account remains unauthenticated, so no claim is made that current connector credentials, account status, market-data entitlement, or paper order submission work. **Live execution remains structurally unavailable.** Position flattening also remains unavailable; the current kill action cancels bot-owned open orders and reconciles any resulting positions but does not liquidate them.

## References

[1]: https://www.sqlite.org/atomiccommit.html "SQLite: Atomic Commit"
[2]: https://www.sqlite.org/wal.html "SQLite: Write-Ahead Logging"
[3]: https://sqlite.org/pragma.html#pragma_integrity_check "SQLite: PRAGMA integrity_check"
