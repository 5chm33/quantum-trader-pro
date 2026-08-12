# Quantum Trader Pro Threat Model

## 1. Scope and Assets

The protected assets are the operator’s filesystem, cloud-computer availability, input data provenance, simulation configuration, event-ledger integrity, report integrity, and the public credibility of any performance statement. Brokerage credentials and live capital are outside the current implementation because no broker integration exists.

| Trust boundary | Trusted side | Untrusted side |
|---|---|---|
| CLI boundary | Parsed typed values after validation | Operator-supplied paths and arguments |
| Data boundary | Validated ordered `MarketEvent` objects | CSV bytes and field values |
| Strategy boundary | Target fraction and rationale | Any future strategy implementation |
| Risk boundary | Explicit `RiskDecision` | Requested quantity and reference price |
| Broker boundary | Allowed decision matching its intent | Any mismatched, duplicate, or enlarged request |
| Persistence boundary | Canonical JSON and typed timestamps | Arbitrary payload objects and local disk state |
| Publication boundary | Retained methodology and checksums | Selective, exaggerated, or unauthenticated claims |

## 2. Threats and Mitigations

### 2.1 Malformed or Adversarial Market Data

A CSV can contain missing fields, invalid decimals, non-finite values, negative volume, inconsistent OHLC prices, duplicate or decreasing timestamps, excessive gaps, or misleading timezone assumptions. The replay adapter rejects these conditions before the engine accepts the affected event. Each accepted source is identified by its exact SHA-256 digest.

A very large valid CSV can still consume time and disk through event and report generation. The current CLI does not impose a row-count or byte-size quota; operators should constrain untrusted inputs at the service or job boundary.

### 2.2 Filesystem Overwrite and Path Abuse

The CLI resolves input and output paths and refuses to replace its known output artifacts unless `--overwrite` is explicit. It does not extract archives, evaluate filenames as code, or create output outside the selected directory.

An authorized operator can still point `--output` at an undesirable location and request overwrite. The cloud service mitigates this by fixing the writable state directory and using systemd filesystem protections. The interactive CLI assumes the invoking user is authorized to choose local paths.

### 2.3 Duplicate Core and Race Conditions

Two processes writing the same output directory could corrupt expectations or create conflicting reports. `SingleInstanceLock` acquires a non-blocking `flock` and records the owner PID. The SQLite connection also uses WAL mode, full synchronous commits, a busy timeout, and one explicit writer per simulation.

The lock is advisory. A malicious process with the same account can ignore it or alter files directly; operating-system permissions remain the final boundary.

### 2.4 Strategy-to-Broker Privilege Escalation

A strategy could attempt to bypass risk and submit an order. The current strategy receives only a market event and returns a `Signal`; it has no broker reference. The application engine alone translates targets to intents and passes decisions to the broker.

The simulated broker verifies that a decision is allowed, references the same intent and correlation IDs, does not approve a larger quantity, and has not already seen the intent. A future adapter must preserve these checks rather than trusting upstream callers implicitly.

### 2.5 Look-Ahead and Same-Bar Execution

A simulation can report inflated results if it trades at a price already observed by the signal. Orders created at timestamp *t* cannot fill on an event at or before *t*. The default fill occurs at the next eligible event open, and slippage plus fees are applied before portfolio accounting.

This does not eliminate every research bias. Parameter tuning, universe selection, survivorship bias, corporate actions, dividend handling, and walk-forward design remain analyst responsibilities.

### 2.6 Circuit-Breaker Deadlock

A naive halt can block both new risk and the exit required to reduce existing risk. The engine observes drawdown after marking the portfolio, records the halt, replaces a non-zero target with a target-to-cash signal, and permits a sell up to the owned quantity. New buys remain denied.

An exit is still subject to the next-event model. A gap can produce a loss beyond the configured threshold; the system does not claim stop-loss price certainty.

### 2.7 Ledger or Report Tampering

Event payloads are serialized as canonical JSON and individually hashed. Run identity, source identity, and metrics identity are deterministic. Independently repeated validation artifacts can therefore be compared byte for byte.

The current ledger does not use a chained hash, digital signature, remote timestamp authority, or write-once storage. A user with filesystem access can replace both content and hashes. The design supports reproducibility, not non-repudiation against a privileged attacker.

### 2.8 Dependency and Supply-Chain Risk

The runtime package uses only the Python standard library. Development tools are version-bounded in `pyproject.toml`, and CI installs them in an isolated environment before running the full gate. Dependabot monitors the GitHub Actions and Python package ecosystems.

A future broker SDK would materially expand this threat surface and must receive a dedicated dependency review, version pinning policy, vulnerability scan, and sandbox integration test.

### 2.9 Secret Exposure

The current system requires no secret. Environment files, keys, certificates, databases, and generated local artifacts are ignored. CI uses no broker credential. Source scanning and independent secret detection are part of the release audit.

If a secret is ever committed, removing it from the latest tree is insufficient; the credential must be revoked and rotated, and repository history must be assessed.

### 2.10 Performance Misrepresentation

A technically correct simulator can still be presented misleadingly. Reports therefore declare the source checksum, period, costs, fill model, risk-free assumption, and benchmark. The retained SPY benchmark is labeled price only with dividends excluded.

The repository does not claim verified live profitability. The legacy evidence boundary is documented so large logs, internal counters, or incomplete trade rows cannot be mistaken for reconciled brokerage results.

## 3. Cloud Deployment

The provided service is simulation only and finite. It applies `NoNewPrivileges`, a private temporary directory, kernel and control-group protections, a restrictive `UMask`, and explicit writable paths. The service user should not hold broker credentials or unrelated application data.

Systemd hardening cannot protect against a compromised privileged administrator, a malicious kernel, or unauthorized modifications to the installed source. Operators should combine it with host patching, least-privilege SSH access, disk backups, and audit logs.

## 4. Known Limitations and Future Work

| Limitation | Consequence | Potential improvement |
|---|---|---|
| Per-event hash, not hash chain | Privileged tampering is not independently evident | Add previous-event digest and signed run manifest |
| No complete exchange calendar | Holidays and special sessions rely on source data | Add a versioned calendar adapter |
| No dividend/corporate-action model | Benchmark is price only | Add adjusted series and explicit cash distributions |
| Single-asset long-only portfolio | Limited research scope | Add multi-asset allocation with cross-position exposure limits |
| No row/size quota | Untrusted files can consume resources | Add configurable input and event limits |
| No paper adapter | External reconciliation is untested | Build a separate sandbox-only integration package |
| No cryptographic signature | Reports prove reproducibility, not authorship | Sign release manifests through CI provenance |

## 5. Security Review Trigger

A new review is mandatory before adding networking, credentials, broker SDKs, background restarts, user-uploaded files, multi-user access, remote APIs, container privileges, dynamic code loading, serialized model loading, or live/paper order routing.
