# Changelog

All notable changes to Quantum Trader Pro are documented in this file.

## [Unreleased]

### Added

- Dependency-free one-click demo launchers for Windows, macOS, Linux, and direct Python use, validated on all three operating-system runners.
- Optional all-or-none `adjusted_close` ingestion and explicit total-return-proxy benchmark availability.
- Flat-to-flat round-trip trade attribution with expectancy, profit factor, win/loss, holding-time, exposure, and turnover metrics.
- Deterministic `round_trip_trades.csv`, end-of-test order cancellation events, and open-position disclosure.
- Fee-, slippage-, and execution-gap-aware buy sizing with post-fill commitment, exposure, and cash-reserve checks.
- Public live-readiness contract, broker threat model, and draft A+ pull request.
- Machine-readable preregistered protocol, validation-only candidate selection, untouched rolling test folds, base/2×/5× cost scenarios, start-offset and execution-buffer robustness checks, complete trial retention, and a one-time holdout receipt.
- Public `v1-preholdout` evidence bundle with complete validation, test, robustness, selection, source-checksum, and gate artifacts.
- Explicit simulation/paper/live profile identities with a separate availability gate, expiring paper-only arming records, exact acknowledgment, and code/configuration/account/namespace fingerprint binding.
- Normalized broker account, clock, position, approved-order, order-state, fill-activity, pagination, cancellation, and durable submission-journal contracts.
- Deterministic broker-safe client order IDs and a fail-closed transition validator covering duplicate updates, partial fills, cancel races, terminal states, and out-of-order regressions.
- Fixed-origin Alpaca paper adapter with standard-library HTTPS transport, normalized account/clock/position/order/activity reads, deterministic one-submit behavior, lookup-first timeout and non-success recovery, verified cancellation outcomes, and official activity pagination.
- Redacted paper credentials and HTTP errors, strict sandbox-origin/path allowlisting, canonical request bodies, lowercase boolean query encoding, and stable per-object response hashes.
- Mode-`0600`, full-sync SQLite broker journal with pre-submit idempotency, validated submission transitions, duplicate-content conflict detection, atomic broker projections, activity checkpoints, and integrity checks.
- Full-state paper reconciler for account identity/status, unresolved submissions, open-order ownership, paginated fills, fill-to-order ownership, duplicate executions, expected positions, broker positions, foreign state, and stalled pagination.
- Fixed-origin read-only control-data adapters for broker asset eligibility, bounded holiday and early-close calendars, and real-time IEX/SIP latest quotes.
- One fail-closed pre-trade controller requiring fresh reconciliation, account, position, asset, clock, calendar, and quote state before any future paper submission.
- Regular-hours-only policy, spread and order-policy validation, broker buying-power reservation, open-order commitment, gross/symbol exposure, cash-reserve, sell-quantity, rolling order-rate, per-session order-count, and future-timestamp controls.
- Thread-safe local API request budgets defaulting to 120 requests per minute with a hard 180/minute ceiling below the documented 200/minute broker-account throttle.
- Strict out-of-band paper credential loading with absolute paths, no symlinks, service-user ownership, mode `0600` files, bounded reads, one-line text validation, minimum 256-bit operator keys, and fully redacted representations.
- Mode-`0600`, full-sync operator-control store that starts paused, records only reason hashes, and rejects approval replay or concurrent actions.
- Action-specific, expiring, one-use HMAC approvals bound to paper mode, namespace, code, configuration, account, nonce, acknowledgment, and exact action.
- Reconciliation-bound resume gate and pause-first cancel kill switch that touches only deterministic bot-owned paper orders, verifies terminal and residual state, reconciles, and remains paused.
- Crash-safe paper execution orchestrator with canonical payload hashes, exclusive durable attempt claims, pre-submit persistence, acknowledged/ambiguous/rejected/reconciled states, global unresolved-submission blocking, pause-on-uncertainty, and a recovery API that cannot submit.
- Deterministic failure-injection matrix covering every submission boundary, operator pause races, close/reopen recovery, literal subprocess termination plus repeated fresh-process recovery, non-success HTTP classification, partial fills, fill-during-cancel, in-progress operator restart, corrupt journal paths, simulated `SQLITE_FULL`, and mid-transaction rollback plus clean retry.
- Committed cross-platform `uv.lock`, locked local and protected CI environments, immutable GitHub Action SHAs, locked dependency vulnerability audit, and retained CycloneDX SBOM artifacts.

### Changed

- Replaced the exit-fill headline diagnostic with true round-trip trade metrics.
- Raised the default declared execution-price buffer to 1,000 basis points while retaining post-fill breach detection.
- Made the single-instance lock native on both Windows and POSIX.
- Updated GitHub Actions to validate source, installed wheel, and one-click wrappers on Windows, macOS, and Linux.
- Replaced the obsolete single-SPY headline evaluation with the preregistered six-asset negative result while retaining v0.1.0 as historical engineering evidence.
- Kept every existing replay and one-click command simulation-only; strict credentials and operator actions remain internal-only, flattening is unavailable, the configured external preflight remains unauthenticated, and the live gate always rejects.

### Validation

- Expanded the locked local suite to 162 passing tests with 90.23% branch coverage after literal process-recovery and storage-exhaustion acceptance.
- Verified exactly one tested broker submission side effect across post-response, post-acknowledgment, and post-reconciliation hard crashes; close/reopen recovery used deterministic client-ID lookup and never issued a second POST.
- Verified exact two-execution partial-fill projection across restart, honest fill-during-cancel reconciliation, terminal never-submitted pause races, all-or-nothing rollback after an injected SQLite trigger failure, and clean recovery after simulated `SQLITE_FULL`.
- Audited the locked 43-package development graph with zero known vulnerabilities on 2026-08-12; protected CI regenerates the audit and CycloneDX SBOM.
- Verified the one-click demo produces six deterministic, checksummed, simulation-only artifacts and an explicit finite-run end state.
- Retained 2,604 preregistered trials across six assets and 84 untouched base-cost folds; the strategy failed its pre-holdout gate with −2.27% median excess return and 15.48% positive-fold share.
- Repeated the complete pre-holdout evaluation independently and confirmed every core artifact was byte-for-byte identical.
- Preserved the final 252 observations per asset unopened because the pre-holdout evidence did not pass.

## [0.1.0] — 2026-08-12

### Added

- Deterministic simulation engine with explicit market-data, strategy, risk, portfolio, broker, and event-store boundaries.
- Strict OHLCV CSV validation, timezone normalization, data-gap checks, and SHA-256 source provenance.
- Moving-average target-allocation strategy with transparent warm-up behavior.
- Fail-closed order, exposure, cash-reserve, drawdown, realized-loss, and duplicate-intent controls.
- Recorded emergency target-to-cash override that keeps risk-reducing exits available after a halt.
- Next-event simulated fills with configurable per-order fees, per-share fees, and basis-point slippage.
- Reconciled cash, position, average-cost, realized-P&L, unrealized-P&L, fee, and equity accounting.
- Append-only SQLite event ledger with canonical JSON payload hashes.
- JSON, Markdown, equity-curve, and fills reports with explicit methodology and price-only benchmark labeling.
- Single-instance process lock, output-overwrite protection, and simulation-only preflight.
- Unit, integration, and CLI smoke suites with 42 passing tests and 92.14% coverage.
- Strict Ruff, mypy, Bandit, build, and GitHub Actions quality gates.
- Architecture, safety, threat-model, legacy-audit, methodology, deployment, contribution, and security documentation.

### Security

- Removed all broker SDK, credential, account, production-endpoint, dynamic-execution, serialized-model, and live-order paths from the distributable project.
- Made paper and live execution unrepresentable in the runtime enum and CLI.
- Added source scanning, dependency review, secret exclusions, and least-privilege systemd templates.

### Validation

- Replayed 1,255 real SPY daily observations from August 2021 through August 2026 under declared costs.
- Retained a transparent result showing 60.21% strategy return versus 73.66% unadjusted buy-and-hold price return, rather than selecting only favorable evidence.
- Repeated the full run and confirmed byte-identical JSON, Markdown, equity, fill, and SQLite artifacts.

### Historical Note

The original first-project archive remains preserved outside this repository. It is not shipped because it contains credentials, unsafe live-order paths, broken contracts, duplicate versions, generated logs, incompatible model artifacts, and unverifiable performance state. See `docs/LEGACY_AUDIT.md`.
