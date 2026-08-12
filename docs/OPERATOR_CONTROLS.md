# Operator Controls and Secret Isolation

Quantum Trader Pro’s paper components remain **unavailable from the public command line**. This document describes the implemented internal control boundary and the evidence still required before an authenticated paper command can be exposed. It does not authorize live trading, and no approval record can change the execution environment from paper to live.

## Control State

A newly created operator database starts **paused**. Pausing never requires approval, because disabling new orders must remain available during uncertainty. Resuming requires a separate, expiring, one-use approval plus a matching paper context, exact code/configuration/account fingerprints, an integral operator store, complete broker reconciliation, and an account that permits new exposure.

| Control | Implemented behavior | Current exposure |
|---|---|---|
| Pause | Immediately appends a durable paused state; only a reason code and reason hash are stored | Internal API only |
| Resume | Requires a one-use HMAC approval, exact fingerprints, valid paper context, SQLite integrity, ready reconciliation, and account readiness | Internal API only |
| Cancel owned orders | Pauses first, consumes a distinct approval, cancels only deterministic strategy-owned paper orders, verifies terminal outcomes and residual state, reconciles, and stays paused | Internal API only |
| Flatten positions | Approval type is reserved, but no flattening service or command exists | **Unavailable** |
| Live execution | No live adapter, endpoint, approval type, or promotion path exists | **Unavailable** |

The pre-trade controller checks the durable pause state **before** reconciliation or any market/broker read. Therefore, a paused state cannot be bypassed by a valid arming record or an otherwise acceptable order.

## One-Use Approval Contract

Every approval is bound to exactly one action, strategy namespace, paper environment, code digest, configuration digest, account digest, issuance time, expiry, 128-bit nonce, and action-specific acknowledgment. The signed payload is authenticated with HMAC-SHA-256 using an out-of-band operator key. Approval records expire after at most ten minutes and are consumed transactionally in a mode-`0600`, full-sync SQLite store.

| Action | Required acknowledgment |
|---|---|
| Resume | `I AUTHORIZE PAPER EXECUTION TO RESUME` |
| Cancel strategy-owned paper orders | `I AUTHORIZE CANCELLATION OF BOT-OWNED PAPER ORDERS` |
| Flatten strategy-owned paper positions | `I AUTHORIZE CLOSING BOT-OWNED PAPER POSITIONS` |

A replayed approval, mismatched action, expired timestamp, altered signature, wrong namespace, changed code/configuration/account fingerprint, concurrent operator action, or attempt to run a kill action while unpaused is rejected. Terminal action records retain only a SHA-256 summary, not broker payloads or operator prose.

## Secret Source

Alpaca documents separate paper and live Trading API domains and states that credentials for one account environment cannot be used for the other.[1] Trading API key ID and secret key values are transmitted in the `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY` headers.[1] The project therefore treats both as process-only secret material.

The credential loader accepts an absolute directory path supplied out of band, such as systemd’s service credential directory. systemd documents service credential facilities as part of the execution environment.[2] The loader enforces the following conditions before constructing an in-memory credential bundle:

| Requirement | Enforcement |
|---|---|
| Directory identity | Absolute, existing directory; no symlink; current service-user ownership; not group/world writable |
| File identity | Exact allowlisted names; regular files only; no symlinks or traversal |
| File permissions | Current service-user ownership and mode `0600` or stricter |
| Text shape | UTF-8, exactly one non-empty line, no whitespace |
| Size | Explicit bounded reads; operator key must contain at least 32 bytes |
| Representation | Credential and bundle `repr` values are always redacted |
| Persistence | Secrets are never serialized into approval, journal, report, test, or repository artifacts |

The code does not accept key or secret values as command-line arguments. It does not load broker credentials from ordinary project configuration files. No paper service unit is shipped until authenticated acceptance and failure-injection phases pass.

## Cancel Kill-Switch Sequence

The cancel action follows a safety-biased order:

1. Append a durable pause state before validating the action approval.
2. Verify the approval’s action, expiry, HMAC, namespace, and fingerprints.
3. Verify the already-armed paper context against the adapter.
4. List all open paper orders and select only client IDs in the deterministic strategy namespace.
5. Request cancellation for each selected order exactly once.
6. Require a broker-observed terminal status and reject changed order identity.
7. Re-read open orders and require zero residual strategy-owned orders.
8. Reconcile broker state and transactionally mark the action complete or failed.
9. Remain paused in both success and failure cases.

Foreign or manually entered orders are never canceled by this action. A failed or ambiguous cancellation is recorded as failed and leaves the system paused.

## Remaining Acceptance Gates

The controls above are software contracts, not authenticated brokerage evidence. Before an operator paper command can be exposed, the project must still demonstrate crash recovery around every side-effect boundary, partial-fill and cancel-race handling, network timeout and rate-limit behavior, corrupted-state response, authenticated paper-account reconciliation, multi-session soak operation, and explicit operator runbooks. Position flattening remains unavailable until its sizing, residual exposure, partial fills, market closure, and halt behavior are validated separately.

## References

[1]: https://docs.alpaca.markets/us/docs/authentication "Alpaca — Authentication"
[2]: https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#Credentials "systemd.exec — Credentials"
