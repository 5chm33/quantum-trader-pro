# Legacy Quantum Trader Pro Audit

## Purpose and Handling

The original project was preserved as an immutable archive, cryptographically checksummed, and staged in a protected working area. No historical launcher, persistence loop, strategy module, broker integration, serialized model, or test script was imported or executed. The professional repository is a clean reconstruction based on verified requirements and failure modes, not an in-place cleanup that could accidentally retain an order path or credential.

## Audit Coverage

| Area | Coverage |
|---|---:|
| Python and launcher files read | 381 of 381 |
| Test and verification scripts classified | 104 |
| Structured artifacts inspected safely | 372 |
| Archived logs streamed | 1,106 unique logs; 35.61 GB uncompressed |
| Model artifacts loaded | 0; 25 unsafe serialized models were indexed only |
| Archive period observed in logs | April 9, 2025 through March 7, 2026 |
| Exact duplicate groups | 967 groups covering 6,392 files |

The log audit streamed every unique archived log rather than extracting the full dataset. It retained only aggregate counts, dates, redacted locations, and bounded evidence; no credential values were copied into the audit output.

## Original Startup Chain

The documented Windows startup instructions launched a persistence wrapper that invoked `scripts/execute_trading_plan_DEFINITIVE_WORKING_VERSION.py --mode live`. That core attempted to assemble AI, market-data, allocation, risk, options, rebalancing, and Alpaca execution components. Separate launchers started scheduled tasks and a 24/7 Kalshi process.

The central core was not safe to reproduce. Static contract analysis confirmed five call-signature mismatches and two broken local imports in its first-hop dependencies. Its error paths could replace unavailable AI or pricing data with time/hash-derived synthetic scores and estimated prices while continuing under “dynamic intelligence” language. Direct order submission and position-closing call sites existed across multiple modules.

## Verified Engineering Findings

| Category | Evidence |
|---|---|
| Syntax | Four Python source files failed compilation |
| Core contracts | Five incompatible calls and two broken local imports |
| Code quality | 15,475 Ruff findings across the staged source tree |
| Security patterns | Bandit reported 19 high-, 116 medium-, and 13 low-severity static findings; review confirmed live-order, dynamic-execution, and credential risks among them |
| Credentials | Independent scanners identified 51 high-confidence credential artifacts and 91 files requiring remediation; values were never retained in reports |
| Dependencies | Conflicting manifests, undeclared imports, unused declarations, and ten known vulnerabilities across six resolved packages |
| Portability | Windows absolute paths, batch launchers, scheduled tasks, and Python-version-specific model artifacts |
| Test quality | 78 of 104 test-like files had no assertions; 33 required network access; 30 contained broker side effects; four had syntax errors |
| Maintainability | Thousands of exact duplicates, “working/final/fixed” backup generations, generated logs, and obsolete launchers obscured the authoritative implementation |

Static scanner totals are indicators, not exploit counts. The audit manually traced the documented launcher and core, then confirmed the material execution, credential, fallback, and interface defects against source locations.

## Data and Model Findings

The structured-data inventory contained 239 JSON files, seven CSV files, three Excel workbooks, four SQLite databases, one gzip file, 93 generated outputs, and 25 serialized model artifacts. Model files were not deserialized because pickle/joblib-style formats can execute code during loading and were not required to establish repository safety.

Most opportunity-analysis files contained internal rankings or error states rather than authenticated market outcomes. The automation database had zero automation-run, performance, and trade-history rows. The online-learning database had 440 `AI_BUY` rows but no exit prices and no populated profit/loss values.

## Profitability Evidence Boundary

The archive proves that the project ran for an extended period and generated a large diagnostic corpus. It does not independently prove a profitable brokerage equity curve.

| Evidence expected for a verified claim | Archive result |
|---|---|
| Authenticated broker order and fill IDs | Not present in the outcome database |
| Entry and exit prices for completed trades | Exit price absent for all 440 outcome rows |
| Realized profit/loss | No positive, negative, or completed values in the outcome database |
| Fees and execution costs | Not recorded in the outcome schema |
| Deposits and withdrawals | No cash-flow reconciliation fields |
| Account identity and beginning/ending equity | Not present in the outcome database |
| Independent daily equity series | Not present in the automation performance tables |
| Internal Kalshi performance files | Zero total P&L and zero completed trades in the dated snapshots |
| Bot-memory performance series | 404 snapshots, 0–56 reported trades, daily P&L fixed at zero |

The exhaustive log corpus contained large volumes of status, warning, error, balance, and rejection text. The aggregate scanner found no exact `filled`, `submit_order`, or `pnl` tokens. Even if such strings had existed, diagnostic text alone would not replace broker reconciliation.

> The defensible résumé statement is that the project was a long-running algorithmic-trading experiment that motivated a deterministic, audited simulation platform. “Verified profitable live bot” should not be stated unless authenticated brokerage records are supplied later.

## Reconstruction Decisions

| Legacy risk | Clean implementation |
|---|---|
| Default `--mode live` launcher | Enum and CLI represent only `simulation` |
| Direct strategy/core order calls | Broker available only through an injected application port |
| Silent synthetic data fallback | Invalid or missing source data raises a clear error |
| Same-process global state | Explicit immutable models and injected dependencies |
| Same-bar or unclear execution timing | Approved orders fill only on a later event open |
| Mixed cash/P&L calculations | Reconciled portfolio aggregate with `Decimal` accounting |
| Halt blocking exits | Recorded target-to-cash override and risk-reducing sell permission |
| Duplicate persistence loops | PID-bearing single-instance lock and finite service job |
| Unverifiable logs | Ordered SQLite ledger, canonical payload hashes, and deterministic reports |
| Conflicting dependencies | One package manifest and no third-party runtime dependency |
| Credential-bearing source | No credential path or broker SDK in the repository |
| Inflated documentation claims | Transparent methodology, limitations, benchmark language, and evidence boundary |

## Grading Interpretation

The historical artifact scored **34.5/100 (F)** on a weighted safe-reproducibility and deployment rubric, while its ambition and learning value were assessed separately as **A-**. That distinction matters: the original project attempted an unusually broad system for a first build, but another engineer could not safely install, reproduce, or validate it.

The cleaned core scored **88.15/100 (B+)** before final documentation, CI, deployment, and repository publication. See the delivery audit for the final portfolio grade after those assets are validated.

## Preservation

The original archive checksum, staged source manifest, redacted secret index, static-analysis outputs, structured-data inventory, complete log-scan aggregates, detailed parallel review reports, and architecture traces remain outside the GitHub repository. This keeps the repository safe and focused while preserving an evidence chain for future remediation or broker-record reconciliation.
