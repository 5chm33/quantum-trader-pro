# Quantum Trader Pro Safety Guardrails

Quantum Trader Pro is a **simulation-only research engine**. It is not a broker, investment adviser, signal service, or live-trading daemon. Historical simulations can lose money in future conditions and do not establish expected returns.

## Execution Boundary

| Mode | Represented in code | CLI accepted | Network broker | Capital at risk |
|---|---:|---:|---:|---:|
| Offline simulation | Yes | Yes | No | No |
| Paper trading contracts | Yes | No | Internal adapter only; no command | No authenticated use |
| Live trading | Identity only | No | No adapter or command | No |

`ExecutionMode` makes simulation, paper, and live identities explicit, but availability is enforced separately. Every public parser and one-click launcher still accepts only `simulation`; `ExecutionPolicy.require_simulation` provides a second application-level check. Preflight reports the implemented paper safety contracts while explicitly reporting paper commands, authenticated paper acceptance, position flattening, and live execution as unavailable.

No broker SDK is a runtime dependency, and the standard one-click path requires no API key, account ID, production endpoint, or network session. Fixed-origin paper adapters exist behind internal contracts, but constructing or invoking them is not a configuration toggle; it requires the still-unexposed secret, approval, reconciliation, and operator boundaries.

## Risk Philosophy

The engine fails closed for new exposure. Invalid data, inconsistent accounting, duplicate intent IDs, non-positive equity, excessive realized loss, and excessive drawdown cannot silently become an order. The risk manager can reduce requested quantity but cannot enlarge it.

An emergency halt must not trap an existing position. When a circuit breaker fires, the engine records a target-to-cash override. New buys are denied, while a sell remains eligible up to the owned quantity. The next-event fill model still applies; the system does not pretend that an exit can occur at the same close that triggered it.

## Data Safety

Market data must be local, ordered, and inspectable. The CSV adapter validates its required columns, timestamp ordering, timezone, gaps, OHLC consistency, volume, and numeric finiteness. It records a SHA-256 digest of the exact input bytes in the source identifier.

The bundled `legacy_synthetic_daily.csv` file exists only for tests and smoke demonstrations. It is labeled synthetic and must not be cited as market evidence. The retained SPY validation report identifies its source period, checksum, fee assumptions, slippage assumptions, and price-only benchmark limitation.

## Secret Policy

Secrets must never be committed, printed, embedded in fixtures, included in reports, stored in SQLite payloads, or passed as CLI values. The standard repository path needs no secret. `.gitignore` excludes environment files, keys, certificates, local databases, generated reports, caches, and credentials.

The internal paper loader accepts only allowlisted files from an absolute out-of-band credential directory. It rejects symlinks, traversal, non-regular files, foreign ownership, group/world-readable permissions, oversized values, multiline text, whitespace, and short operator keys. Credential and bundle representations are redacted. No public command invokes the loader, and live credentials have no loader or adapter path. See [`docs/OPERATOR_CONTROLS.md`](docs/OPERATOR_CONTROLS.md).

## Process Safety

A PID-bearing advisory lock prevents two simulation cores from using the same output path. Existing reports and ledgers are not overwritten without the explicit `--overwrite` flag. The cloud deployment uses a finite `Type=oneshot` service rather than a restart loop.

Internal paper controls use a separate mode-`0600`, full-sync SQLite store that starts paused. Pause requires no approval and blocks pre-trade processing before external reads. Resume and cancel use distinct expiring, one-use HMAC approvals; cancel affects only deterministic bot-owned paper orders, verifies terminal and residual state, reconciles, and remains paused. Position flattening and every public paper command remain unavailable.

The service template applies a restrictive file-creation mask, private temporary directory, privilege escalation prevention, system-call and kernel protections, and write access only to the declared state and input directories. Operators should still review the unit on their own distribution because available systemd hardening directives can vary.

## Financial-Evidence Boundary

The historical archive is preserved separately and is not shipped as executable code. Its logs and internal state files do not independently prove profitable broker execution. This repository therefore distinguishes three statements:

| Statement | Status |
|---|---|
| “The historical project ran and generated extensive logs.” | Supported by the archive |
| “The clean engine produced a profitable five-year SPY simulation.” | Supported for the declared input and methodology |
| “The legacy bot produced verified live profit for a year.” | Not substantiated by the available broker evidence |

A verified live-performance claim would require authenticated broker statements or fill exports, deposits and withdrawals, fees, corporate actions, account-level equity reconciliation, and an immutable timestamped methodology.

## Operator Checklist

Before running a simulation, the operator should verify the input source, digest, symbol, timestamp convention, gap policy, strategy windows, capital, execution costs, and risk limits. After the run, the operator should confirm that no pending orders remain, no fills were rejected, the final position state is understood, and the report describes the correct benchmark.

Before publishing results, the operator must label synthetic data, distinguish price return from dividend-adjusted total return, avoid annualizing very short samples, disclose selection and tuning, and retain the exact configuration and source digest.

## Reporting Vulnerabilities

Please report a suspected security issue privately through GitHub’s security-advisory interface rather than opening a public issue. See [`SECURITY.md`](SECURITY.md) for supported versions and response expectations.
