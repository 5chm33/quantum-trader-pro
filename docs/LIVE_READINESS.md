# Live-Readiness Contract

> **Current authorization boundary:** Quantum Trader Pro may be engineered and tested for brokerage integration, but real-money order submission remains disabled until a separate, explicit operator acceptance decision. The default one-click path is offline simulation.

## Meaning of “A+”

An A+ score is an evidence standard rather than a promise of profitability. It means the repository makes its operating mode unambiguous, fails closed when inputs or broker state are uncertain, records every external side effect, survives duplicate and delayed events, reconciles after interruption, and proves those properties with automated and paper-environment evidence.

It does **not** mean that a strategy will make money, that a paper result predicts live performance, or that live execution is safe without account-specific review. Alpaca states that paper trading omits market impact, information leakage, latency slippage, queue position, price improvement, regulatory fees, and dividends.[1]

## Execution Profiles

| Profile | Network | Credentials | External orders | Default | One-click |
|---|---:|---:|---:|---:|---:|
| `simulation` | No | No | No | **Yes** | **Yes** |
| `paper` | Yes | Paper-only | Sandbox only | No | After credential setup |
| `live` | Yes | Live-only | Real money | No | **Intentionally no** |

The live profile must never be inferred from a URL, API key, or missing flag. It requires a live-specific configuration, an expiring arming record, an operator-supplied acknowledgment, a clean preflight, and a live-capable deployment policy. Paper and live credentials must be physically or logically separated; Alpaca issues distinct keys and endpoints for the environments.[1] [2]

## Current Implementation Status

The repository now models `simulation`, `paper`, and `live` as explicit identities, but **availability is a separate fail-closed decision**. Existing replay and one-click commands still accept only `simulation`. A paper arming record can be constructed only with the exact paper acknowledgment, expires within 24 hours, and is cryptographically bound to code, configuration, account fingerprint, and strategy namespace. The live gate always rejects.

| Capability | Status | Evidence boundary |
|---|---|---|
| Offline simulation | Implemented | Existing deterministic replay and one-click tests |
| Paper arming record | Implemented | Expiry, acknowledgment, fingerprint, namespace, and preflight matrix tests |
| Normalized account/clock/order/fill/activity contracts | Implemented | Validation and transition-state unit tests |
| Deterministic client order ID | Implemented | Stable namespace/account/intent identity tests |
| Durable submission journal | Implemented | Mode-`0600` SQLite journal, full-sync durability, pre-submit idempotency, validated transitions, duplicate conflict detection, and integrity checks |
| Full-state reconciliation | Implemented but not operator-enabled | Account fingerprint/status, open orders, positions, paginated fills, client-ID ownership, unresolved submissions, execution deduplication, atomic projections, and checkpoint commit |
| Market and portfolio controls | Implemented but not operator-enabled | Broker clock plus bounded holiday/early-close calendar; real-time IEX/SIP quotes; account, reconciliation, position, asset, and quote freshness; spread, order policy, buying power, open commitments, exposure, cash reserve, and durable order-rate gates |
| External request budgets | Implemented | Thread-safe sliding windows default to 120 requests/minute with a non-configurable 180/minute hard ceiling, below Alpaca’s documented 200/minute account throttle |
| Alpaca paper adapter | Implemented but not operator-enabled | Fixed `https://paper-api.alpaca.markets` origin, injectable HTTPS transport, normalized reads, one-submit idempotency, client-ID timeout recovery, verified cancellation, and activity pagination |
| Secret isolation | Implemented but not operator-enabled | Absolute out-of-band credential directory; allowlisted regular files; no symlinks; service-user ownership; mode `0600` or stricter; bounded reads; single-line text; redacted representations |
| Operator pause and resume | Implemented but not operator-enabled | New stores start paused; pause needs no approval; resume requires a one-use HMAC approval, exact fingerprints, valid paper context, store integrity, ready reconciliation, and account readiness |
| Cancel owned orders | Implemented but not operator-enabled | Pauses first; uses a distinct one-use approval; cancels only deterministic strategy-owned orders; verifies terminal and residual state; reconciles; remains paused |
| Flatten positions | **Unavailable** | Approval identity is reserved, but no flattening implementation or command exists |
| Paper credentials and command | **Not yet enabled** | No public command loads the strict credential bundle or submits an external order; authenticated paper acceptance evidence is still absent |
| Live execution | **Unavailable** | Gate and preflight report explicitly reject live execution; the adapter rejects the live origin |

This status is intentionally narrower than “paper-ready.” Durable journals, full-state REST reconciliation, market/session/stale-state/portfolio/order-rate controls, strict secret loading, default pause, one-use approvals, reconciliation-bound resume, and owned-order cancellation are now implemented and tested. The next phases must add crash/partial-fill/cancel-race/failure-injection evidence, a validated flatten design, and authenticated paper acceptance before a paper command can exist. The current adapter remains exercised only through deterministic transport fixtures because the configured external account preflight was not authenticated; no broker order was attempted.

## Non-Negotiable Invariants

| ID | Invariant | Required evidence |
|---|---|---|
| LR-01 | No order is submitted before its intent, risk decision, deterministic client ID, and requested payload are durably recorded. | Crash-after-persist test |
| LR-02 | An ambiguous timeout never causes a blind retry. The client first retrieves the order by deterministic `client_order_id`.[3] | Timeout/idempotency test |
| LR-03 | Duplicate or out-of-order trade events cannot duplicate fills, cash, fees, or positions. | Event permutation and duplicate tests |
| LR-04 | New exposure is rejected unless the account is active, unblocked, unsuspended, and within local and broker buying-power limits.[4] | Account-state matrix tests |
| LR-05 | New exposure is rejected outside the broker-reported session or when clock/calendar state cannot be verified. | Clock outage and holiday tests |
| LR-06 | New exposure is rejected when the quote or bar is missing, crossed, malformed, or older than the configured maximum age. | Stale-data tests |
| LR-07 | Price, quantity, notional, position, portfolio exposure, daily loss, drawdown, order rate, and duplicate limits are checked immediately before submission. | Boundary and burst tests |
| LR-08 | A kill switch stops new exposure immediately; cancel and flatten actions verify broker outcomes rather than assuming success. Alpaca notes cancellation is not guaranteed.[2] | Cancel failure and residual-position tests |
| LR-09 | Startup and reconnect reconcile account, positions, open orders, and paginated activities before the engine can arm. | Restart and pagination tests |
| LR-10 | Orders not owned by the configured strategy namespace halt automation unless an explicit coexistence policy is configured. | Foreign-state tests |
| LR-11 | Paper/live secrets never enter source, logs, reports, command history, exception text, or test fixtures. | Independent secret scans and redaction tests |
| LR-12 | Every broker transition, including partial fills, rejection, cancellation, expiry, replacement, suspension, and uncommon pending states, is handled explicitly.[5] | State-machine transition coverage |
| LR-13 | Simulation, paper, and live evidence are labeled and stored separately. | Artifact-schema tests |
| LR-14 | A code or configuration change invalidates the previous acceptance record. | Version/config fingerprint tests |
| LR-15 | Real-money execution cannot be enabled by the one-click launcher. | CLI and launcher negative tests |

## Broker Ownership and Idempotency

Every external intent receives a deterministic client order ID derived from a versioned namespace, strategy identifier, account fingerprint, symbol, side, target quantity, decision timestamp, and intent fingerprint. Alpaca documents `client_order_id` as the mechanism for organizing, tracking, and retrieving orders.[3]

The adapter follows this transaction boundary:

1. Persist `intent_created` and the deterministic client ID.
2. Persist `risk_approved` with the full limit snapshot.
3. Persist `submission_started` before the network call.
4. Submit once.
5. If a response is lost, query by client ID before any retry.
6. Persist the broker order ID and normalized status.
7. Apply fills only by unique broker execution ID or an equivalent stable composite key.
8. Reconcile REST orders, positions, account values, and activities after startup, reconnect, and every detected gap.

The `trade_updates` stream is used for timely state changes, but it is not treated as an infallible ledger. Alpaca exposes fill, partial-fill, cancel, reject, replace, expiry, and less-common transitional events; REST reconciliation remains mandatory after disconnection.[5]

## Pre-Trade Controls

The following controls apply to every paper or live order, including risk-reducing orders where relevant:

| Control | Fail-closed behavior |
|---|---|
| Account status | Halt if inactive, blocked, suspended, or inconsistent |
| Asset status | Reject if not tradable or if fractionability/shortability conflicts with the request |
| Session | Reject new exposure when the market is closed or clock/calendar is unavailable |
| Data freshness | Reject if the decision input exceeds its maximum age |
| Price collars | Reject non-positive, malformed, crossed, or outlier prices |
| Quantity and notional | Clamp or reject above explicit per-order limits |
| Gross/net exposure | Reject above symbol, strategy, and portfolio limits |
| Buying power and reserve | Reject when either broker or local reserve is insufficient |
| Daily loss/drawdown | Reject new exposure and permit only bounded risk reduction |
| Order rate | Reject bursts and loops across rolling windows |
| Duplicate intent | Return the existing intent result without submitting again |
| Self-conflict | Reject opposing open bot-owned orders unless executing an approved replacement |

These controls adopt conservative engineering concepts from SEC market-access guidance, which describes preset credit/capital limits and rejection of erroneous price, size, and duplicative orders, and from FINRA guidance emphasizing controlled development, pre-production testing, validation, and post-change monitoring.[6] [7]

## Kill-Switch Semantics

A kill switch has three distinct actions so an operator cannot accidentally flatten real positions while intending only to pause:

| Action | Effect |
|---|---|
| `pause` | **Implemented internally.** Reject new exposure before any broker/market read; retain existing broker state; no approval required |
| `cancel` | **Implemented internally.** Pause first, consume a one-use approval, cancel only bot-owned paper orders, verify terminal and residual states, reconcile, and stay paused |
| `flatten` | **Unavailable.** Reserved approval identity only; no position-closing implementation or public command exists |

`flatten` is never automatic on process failure, startup mismatch, or data outage. It requires an explicit operator action because emergency market orders can themselves create loss. Unresolved orders or positions keep the system in a halted, non-armed state and produce a high-severity operator report.

## Secret Boundary

Paper credentials are optional and never part of the base installation. The strict loader accepts only named files in an absolute out-of-band credential directory, such as a systemd credential directory. It rejects symlinks, traversal, foreign ownership, broad permissions, oversized values, multiline text, whitespace, and short operator keys. Broker key values are not accepted as CLI arguments or ordinary configuration/environment values. The bundle and credential representations are always redacted, and live credentials have no loader or adapter path.

The detailed state machine, acknowledgment strings, credential rules, and cancel sequence are documented in [Operator Controls and Secret Isolation](OPERATOR_CONTROLS.md).

## Paper Acceptance Gate

A live-capable build remains **paper-only** until it completes an operator-reviewed acceptance campaign satisfying all of the following:

| Gate | Minimum evidence |
|---|---|
| Duration | At least 20 distinct market sessions |
| Broker evidence | Authenticated paper order IDs, execution IDs, and paginated activity checkpoints |
| Reconciliation | Zero unexplained cash, position, order, or fill divergence at each checkpoint |
| Idempotency | Zero duplicate external submissions across deliberate timeout and restart tests |
| Recovery | Successful recovery from process termination, network interruption, stale data, and stream reconnect |
| Safety | Demonstrated pause, cancel, and flatten drills with verified outcomes |
| Observability | Complete health, order-state, risk, reconciliation, and operator-action evidence |
| Review | Acceptance record tied to exact commit, configuration fingerprint, and dependency lock |

Paper acceptance is necessary but not sufficient for real-money activation because paper trading does not reproduce all live execution effects.[1]

## Live Activation Gate

Real-money activation is outside the current task. If requested later, it requires a separate review of the exact account, symbols, strategy limits, tax/regulatory considerations, secrets, deployment host, monitoring path, and maximum tolerable loss. The operator must approve a bounded canary configuration; no general “enable live” switch will be provided.

## References

[1]: https://docs.alpaca.markets/us/docs/paper-trading "Alpaca Paper Trading"
[2]: https://alpaca.markets/sdks/python/trading.html "Alpaca-py Trading"
[3]: https://docs.alpaca.markets/us/docs/working-with-orders#using-client-order-ids "Alpaca Client Order IDs"
[4]: https://docs.alpaca.markets/us/docs/account-plans#the-account-object "Alpaca Account Object"
[5]: https://docs.alpaca.markets/us/docs/websocket-streaming "Alpaca WebSocket Trade Updates"
[6]: https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0 "SEC Market Access Risk Controls FAQ"
[7]: https://www.finra.org/rules-guidance/notices/15-09 "FINRA Regulatory Notice 15-09"
