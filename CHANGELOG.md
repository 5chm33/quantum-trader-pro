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

### Changed

- Replaced the exit-fill headline diagnostic with true round-trip trade metrics.
- Raised the default declared execution-price buffer to 1,000 basis points while retaining post-fill breach detection.
- Made the single-instance lock native on both Windows and POSIX.
- Updated GitHub Actions to validate source, installed wheel, and one-click wrappers on Windows, macOS, and Linux.

### Validation

- Expanded the local suite to 55 passing tests before the walk-forward evaluation phase.
- Verified the one-click demo produces six deterministic, checksummed, simulation-only artifacts and an explicit finite-run end state.

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
