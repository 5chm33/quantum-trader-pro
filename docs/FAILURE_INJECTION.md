# Failure Injection and Recovery Evidence

Quantum Trader Pro treats **order identity, durable intent, broker acknowledgement, fill reconciliation, and operator state as separate facts**. The paper execution service does not infer one from another, and it never retries a submission merely because a response was not observed. This document records the deterministic failure tests completed before an authenticated sandbox acceptance run exists.

> **Scope boundary:** these tests exercise local orchestration, durable stores, a literal subprocess that terminates without cleanup, the normalized paper adapter, and deterministic broker doubles. They do not claim that authenticated brokerage connectivity, a physical storage device, a service manager, or a host power-loss event has been certified.

## Submission State Contract

The order path is deliberately asymmetric. Before any externally visible action, a canonical order payload hash and deterministic client order ID are persisted. A unique attempt digest then claims the order as `started`. Only that claimed state may reach the paper adapter. A response becomes `acknowledged`; broker-authoritative reconciliation becomes `reconciled`. Known validation rejection becomes terminal `rejected`. Any uncertain outcome becomes `ambiguous`, pauses the system, and requires client-ID lookup and reconciliation rather than a second submission.

SQLite documents transaction atomicity as an all-or-nothing property and uses a commit record in WAL mode. The journals use WAL with `synchronous=FULL`, and every reconciliation projection is committed in one transaction.[1] [2] Runtime startup and recovery also invoke SQLite integrity checks, while acknowledging that software checks cannot prove the behavior of every storage stack.[3]

| Injected boundary or failure | Durable expectation | External-side-effect expectation | Verified recovery behavior |
|---|---:|---:|---|
| After pre-trade assessment | No submission row | Zero submissions | A clean later attempt may start normally. |
| After durable persistence | `persisted` | Zero submissions | Recovery confirms absence by client ID and marks the never-started intent `rejected`. |
| After exclusive start claim | `started` | Zero submissions | Absence remains ambiguous; the system stays paused and does not retry. |
| After broker response, before acknowledgement | `started` | Exactly one submission | Restart performs client-ID lookup, acknowledges, and reconciles without a second POST. |
| Literal subprocess `os._exit(137)` after broker response | On-disk `started` plus one fake broker order | Exactly one fake external side effect | Two separate recovery processes both resolve the same client ID to `reconciled`; submission count remains one and operator state remains paused. |
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
| Simulated `SQLITE_FULL` through `max_page_count` | Original transaction state | No partial projection | Fill and checkpoint remain absent, journal integrity remains `ok`, capacity restoration permits one clean commit. |
| Process restart during kill action | `in_progress` action retained | No automatic replay | Resume and approval reuse remain blocked; one explicit terminal failure record is allowed. |
| Corrupt, symlinked, broadly readable, foreign, or unsafe journal path | Store refuses to open | No broker access | Fail closed before database or network use. |

## Tested Invariants

The failure suite establishes that **one deterministic client order ID can cause at most one tested paper submission side effect**. Existing durable state blocks ordinary execution, unresolved state blocks every new order globally, and recovery has no submission method. All post-start unexpected exceptions are treated as ambiguous, not rejected. All uncertainty paths pause the operator store.

The reconciliation transaction retains fills by immutable execution ID, rejects conflicting duplicate economics, permits only one ownership-enrichment transition, validates order-state monotonicity, and advances an activity checkpoint only in the same transaction as the retained evidence. Partial executions are projected from fills rather than inferred from final order status.

The operator path starts paused, consumes action-specific HMAC approvals once, persists in-progress state before cancellation, ignores foreign orders, verifies terminal broker outcomes, reconciles after cancellation, and remains paused even after success. A fill that wins the cancel race is therefore represented as a fill and position, not mislabeled as a successful cancellation.

## Reproduction

Run the focused failure suite from a source checkout:

```bash
python -m pip install --disable-pip-version-check uv==0.12.1
uv sync --locked --extra dev
uv run pytest \
  tests/integration/test_literal_process_recovery.py \
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

The final local acceptance suite reports **162 passing tests and 90.23% branch coverage**. It runs through the committed cross-platform dependency lock. The protected public workflow repeats formatting, lint, strict typing, branch coverage, source and locked-dependency security analysis, SBOM generation, evidence verification, package construction, installed-wheel preflight, and one-click launcher checks across Python 3.11, Python 3.12, Linux, macOS, and Windows.

## Residual Risks and Required External Evidence

A literal subprocess now terminates with `os._exit(137)` after its one fake broker side effect, and fresh processes recover twice without a duplicate submission. A separate `SQLITE_FULL` simulation proves transactional rollback and clean recovery. These do not reproduce an operating-system `SIGKILL` during every boundary, service-manager restart, physical volume exhaustion, kernel panic, filesystem failure, or host power loss. Authenticated timeout behavior, broker rate-limit responses, market-data disconnection, and real sandbox partial fills also remain acceptance work.

The configured external account remains unauthenticated, so no claim is made that current connector credentials, account status, market-data entitlement, or paper order submission work. **Live execution remains structurally unavailable.** Position flattening also remains unavailable; the current kill action cancels bot-owned open orders and reconciles any resulting positions but does not liquidate them.

## References

[1]: https://www.sqlite.org/atomiccommit.html "SQLite: Atomic Commit"
[2]: https://www.sqlite.org/wal.html "SQLite: Write-Ahead Logging"
[3]: https://sqlite.org/pragma.html#pragma_integrity_check "SQLite: PRAGMA integrity_check"
