<div align="center">

# Quantum Trader Pro

**A deterministic, simulation-only trading research engine with fail-closed risk controls, next-event execution modeling, and a cryptographically traceable event ledger.**

*Originally built as a first algorithmic-trading project, then reconstructed into a safe, reproducible portfolio-grade engineering system.*

[![Quality Gate](https://img.shields.io/badge/quality%20gate-passing-brightgreen)](.github/workflows/quality.yml)
[![Tests](https://img.shields.io/badge/tests-270%20passing-brightgreen)](tests)
[![Coverage](https://img.shields.io/badge/coverage-90.63%25-brightgreen)](tests)
[![Engineering Grade](https://img.shields.io/badge/engineering%20grade-A%2B-blue)](docs/ENGINEERING_GRADE.md)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](pyproject.toml)
[![Execution](https://img.shields.io/badge/execution-simulation%20only-blueviolet)](SAFETY.md)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

</div>

---

## What It Does

Quantum Trader Pro replays a local OHLCV dataset through an explicit sequence of market-data validation, deterministic signal generation, target-position construction, fail-closed risk review, next-event simulated execution, reconciled portfolio accounting, and append-only event recording. Every generated report is derived from the same ordered observations and declares its fees, slippage, conservative execution buffer, benchmark availability, end-of-test policy, and fill methodology.

The repository now contains a **fixed-sandbox Alpaca paper adapter, transactional journals, full-state reconciler, fail-closed pre-trade controls, strict out-of-band credential loader, and default-paused operator-control store**, but none is exposed through an operator command. One-use HMAC approvals bind resume and cancel actions to the exact code, configuration, account, namespace, action, nonce, and expiry. The cancel kill switch pauses first, touches only deterministic bot-owned paper orders, verifies terminal and residual state, reconciles, and remains paused. Every executable command still accepts only `simulation`; authenticated paper acceptance and position flattening remain absent, and the live gate always rejects. This remains a portfolio and research-engineering project, not an autonomous capital-deployment system.

| Capability | Implementation |
|---|---|
| Market data | Strict local CSV replay with timezone normalization, OHLC validation, optional all-or-none `adjusted_close`, gap checks, ordering checks, and SHA-256 provenance |
| Strategy | Transparent moving-average target-allocation model with deterministic warm-up behavior |
| Risk | Fee-, slippage-, and gap-aware buy sizing plus post-fill exposure, reserve, order-notional, drawdown, realized-loss, and duplicate-intent controls |
| Emergency behavior | New exposure is blocked while a recorded target-to-cash override remains permitted |
| Execution | Orders fill only at the next eligible event open with declared fees and slippage |
| Accounting | Cash, holdings, average cost, realized P&L, unrealized P&L, fees, and equity reconcile on every event |
| Auditability | Ordered SQLite event ledger with canonical JSON payload hashes |
| Reporting | JSON, Markdown, equity, fill, and flat-to-flat trade CSVs with total-return-proxy availability, price diagnostics, exposure, turnover, drawdown, expectancy, and risk state |
| Process safety | Single-instance lock and explicit output-overwrite protection |
| Broker boundary | Expiring paper arming; fixed paper origin; pre-submit journal; exclusive attempt claim; no-blind-retry recovery; reconciliation; market and portfolio gates; strict credential files; default pause; one-use resume/cancel approvals; owned-orders-only verified cancel; no operator-enabled paper command, flatten service, or live adapter |

---

## Evaluation Card

The frozen [`qtpro-walk-forward-v1`](evaluation/protocol_v1.json) protocol was committed **before** retrieving its declared multi-asset panel or observing a walk-forward result. It retained every candidate, cost, and robustness trial rather than publishing only the winner. The current moving-average strategy **failed the pre-holdout promotion gate**, so the final 252 observations for each asset remain unopened.

| Evaluation field | Observed result | Frozen gate | Outcome |
|---|---:|---:|---|
| Assets | SPY, QQQ, IWM, EFA, TLT, GLD | All six complete | Pass |
| Retained trials | 2,604 | Retain every attempt | Pass |
| Untouched base-cost test folds | 84 | At least 48 | Pass |
| Median base excess return | **−2.27%** | At least 0% | **Fail** |
| Folds beating adjusted-close benchmark | **15.48%** | At least 55% | **Fail** |
| Median 2×-cost excess return | **−2.28%** | At least 0% | **Fail** |
| Median 5×-cost excess return | −2.34% | At least −5% | Pass |
| Worst test drawdown | **−34.33%** | No worse than −30% | **Fail** |
| Risk-halted cost scenarios | **6** | 0 | **Fail** |
| Final holdout | **Locked** | Open only after satisfactory pre-holdout evidence | Preserved |

![Preregistered pre-holdout gate](docs/assets/walk_forward_gate.png)

![Walk-forward robustness diagnostics](docs/assets/walk_forward_robustness.png)

The complete public evidence bundle includes all **2,016 validation trials**, **252 untouched test/cost trials**, **336 robustness trials**, fold selections, source checksums, gates, and deterministic hashes in [`evaluation/results/v1-preholdout/`](evaluation/results/v1-preholdout/). The full evaluation was executed independently twice; every core artifact was **byte-for-byte identical**. The readable [`research protocol`](docs/RESEARCH_PROTOCOL.md) and [`methodology`](docs/METHODOLOGY.md) explain the selection order, adjusted-close benchmark, cost model, start-date sensitivity, and lockbox boundary.

The new strategy campaign is governed separately so it cannot retune or relabel the failed v1 result. [`STRATEGY_GOVERNANCE.md`](docs/STRATEGY_GOVERNANCE.md) freezes grade caps and holdout rules; the citation-validated [`STRATEGY_HYPOTHESES.md`](docs/STRATEGY_HYPOTHESES.md) defines twelve falsifiable equity and defined-risk options families, permanent baselines, point-in-time data needs, and candidate ceilings before any new experiment is preregistered. The provider-neutral [`DATA_CONTRACTS.md`](docs/DATA_CONTRACTS.md) specification and its 18 hash-pinned schemas define the causal, licensing, corporate-action, options-lifecycle, and snapshot evidence every future data adapter must satisfy. The new [`EXPERIMENT_LEDGER.md`](docs/EXPERIMENT_LEDGER.md) contract retains candidate registration, preregistration, every terminal attempt, comparison barriers, gate evidence, and one-time candidate-bound holdouts; it is a research-integrity control, not a performance claim. [`REFERENCE_INGESTION.md`](docs/REFERENCE_INGESTION.md) documents the implemented SEC, Treasury, local equity, corporate-action, and immutable snapshot adapters, including their strict publication-time and licensing boundaries. [`FORECAST_SIGNALS.md`](docs/FORECAST_SIGNALS.md) specifies the new immutable time-series and cross-sectional forecast contracts, bounded volatility transform, frozen-universe guard, and permanent equal-weight, unscaled-trend, and cash comparators; it contains no new return result or activation path. [`OPTIONS_LIFECYCLE.md`](docs/OPTIONS_LIFECYCLE.md) specifies immutable option contracts and deliverables, independently retained Greeks inputs, defined-risk strategy restrictions, partial-fill protection, and explicit exercise, assignment, expiry, and adjustment receipts; it adds neither a trade route nor an options-performance claim. [`PORTFOLIO_CONSTRUCTION.md`](docs/PORTFOLIO_CONSTRUCTION.md) specifies deterministic factor-aware forecast blending, point-in-time loading receipts, explicit family/instrument/gross/net/factor constraints, and all-cash fail-closed outcomes; it adds no performance conclusion or execution path. [`EXECUTION_COSTS.md`](docs/EXECUTION_COSTS.md) specifies point-in-time quote/volume receipts, declared spread/fee/impact estimates, bounded participation, explicit partial/no-trade outcomes, and capacity diagnostics; it creates neither an order path nor a performance claim. [`INFERENCE_AND_ROBUSTNESS.md`](docs/INFERENCE_AND_ROBUSTNESS.md) specifies causal return evidence, serial-dependence diagnostics, deterministic circular moving-block comparison receipts, and complete adverse-scenario reporting; it declares no alpha, promotion, adjusted significance result, or activation path.

> **Interpretation:** This is a successful falsification and reproducibility result, not evidence of an alpha edge. It does not authenticate the legacy project’s reported live history, prove future profitability, or authorize live capital. The earlier v0.1.0 single-SPY engineering run remains available in the [baseline release](https://github.com/5chm33/quantum-trader-pro/releases/tag/v0.1.0) for historical comparison, but it is not the headline strategy evaluation.

---

## Architecture

![Quantum Trader Pro architecture](docs/assets/system_architecture.png)

The architecture uses ports and adapters so market data, brokerage, and persistence remain outside the domain model. The strategy never calls a broker directly. The engine records the market event, reconciles existing fills, marks the portfolio, evaluates circuit breakers, generates a signal, translates it into an intent, applies risk policy, and only then hands an approved order to the simulated broker.

The current implementation is deliberately finite rather than an always-on market daemon. A system service can schedule or launch a simulation job; paper credentials can now be loaded only from strict out-of-band files, but no public command consumes them or submits an order. See [`ARCHITECTURE.md`](ARCHITECTURE.md), [`SAFETY.md`](SAFETY.md), and [`THREAT_MODEL.md`](THREAT_MODEL.md) for the current design boundary. The A+ safety and acceptance evidence is documented in [`docs/LIVE_READINESS.md`](docs/LIVE_READINESS.md), [`docs/OPERATOR_CONTROLS.md`](docs/OPERATOR_CONTROLS.md), [`docs/FAILURE_INJECTION.md`](docs/FAILURE_INJECTION.md), and [`docs/BROKER_THREAT_MODEL.md`](docs/BROKER_THREAT_MODEL.md); [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) covers installation and [`docs/DEPLOYMENT_EVIDENCE.md`](docs/DEPLOYMENT_EVIDENCE.md) records the accepted cloud state.

---

## One-Click Demo

Quantum Trader Pro requires **Python 3.11 or newer**, but the offline demo requires no package installation, data download, account, API key, or broker connection. Download and extract the latest [release zip](https://github.com/5chm33/quantum-trader-pro/releases/latest), then use the launcher for your operating system:

| Operating system | One-click action |
|---|---|
| Windows | Double-click **`launch_demo.cmd`** |
| macOS or Linux | Double-click **`launch_demo.sh`**, or run `./launch_demo.sh` |
| Any platform with Python | Run `python launch_demo.py` |

The launcher processes the bundled, clearly labeled synthetic fixture and creates a unique folder under `quantum-trader-demo-runs/` containing the SQLite ledger, JSON and Markdown reports, equity curve, fills, and `round_trip_trades.csv`. It discloses whether adjusted-close benchmark data is unavailable, cancels pending orders at the final bar, and marks any remaining position to the final close. It can only invoke `simulation`; it contains no paper/live endpoint or credential path.

If Python is not installed, use the official installer from [python.org](https://www.python.org/downloads/), enable the Windows “Add Python to PATH” option, and run the launcher again.

## Developer Installation

Development tooling is resolved by the committed cross-platform `uv.lock`; the runtime package itself has no third-party dependencies. Install the exact environment manager used by protected CI, then synchronize without updating the lock:

```bash
git clone https://github.com/5chm33/quantum-trader-pro.git
cd quantum-trader-pro
python -m pip install --disable-pip-version-check uv==0.12.1
uv sync --locked --extra dev
uv run quantum-trader preflight
uv run quantum-trader demo
```

`make quality` runs every local check through that locked environment. The one-click simulation launchers remain dependency-free and do not require this developer setup.

For research, provide a real local CSV with the required columns shown below. Rows must be strictly increasing, prices must form a valid OHLC bar, volume must be non-negative, and timestamps must be ISO-8601 values. Naive timestamps are interpreted as UTC by default.

```csv
datetime,open,high,low,close,volume
2024-01-02T14:30:00+00:00,100.00,101.25,99.75,100.80,1250000
```

The CLI refuses to overwrite an existing event ledger or report set unless `--overwrite` is supplied explicitly.

---

## Output Artifacts

| Artifact | Purpose |
|---|---|
| `events.sqlite3` | Ordered decision-to-fill ledger with canonical payload hashes |
| `simulation_report.json` | Machine-readable configuration, methodology, metrics, and final state |
| `simulation_report.md` | Human-readable evaluation card and methodology disclosure |
| `equity_curve.csv` | Timestamped reconciled cash, market value, equity, P&L, and fees |
| `fills.csv` | Simulated fill IDs, side, quantity, price, fees, slippage, and notional |
| `round_trip_trades.csv` | Flat-to-flat trade attribution with entry/exit notional, fees, realized P&L, duration, and return |

The report embeds the input checksum through the source identifier and adds a metrics checksum. Repeating the same code, source bytes, and configuration produces the same core artifacts.

---

## Quality Gate

The repository quality gate is intentionally stricter than the historical prototype. It requires a current lockfile, formatting and lint checks, strict static typing, unit/integration/smoke tests, at least 90% branch coverage, source security scanning, retained-research integrity, immutable workflow actions, package builds, and installed/preflight/demo checks.

```bash
python -m pip install --disable-pip-version-check uv==0.12.1
uv sync --locked --extra dev
make quality
```

The v0.2.0 baseline plus the public strategy-research branch currently has **270 passing tests** and **90.63% branch coverage**, with no strict-type errors, Ruff findings, or Bandit findings. The public v0.2.0 release tag remains an immutable engineering baseline; these new checks do not alter its released strategy-evidence boundary.

The suite includes every injected submission boundary, a literal subprocess `os._exit` followed by two fresh-process recoveries with exactly one fake external side effect, close/reopen recovery, partial fills, fill-during-cancel, timeout and non-success response classification, operator pause races, corrupt-path rejection, injected transaction rollback, and simulated `SQLITE_FULL` rollback plus clean recovery. The locked 43-package development graph had **zero known vulnerabilities** in the 2026-08-12 acceptance audit; protected CI re-audits it and retains a CycloneDX SBOM. All five external workflow references are pinned to immutable commit SHAs. Protected CI repeats the gate on Python 3.11 and 3.12 and validates the one-click launchers on Linux, macOS, and Windows.

The exact cloud commit, wheel checksum, rollback, `systemd` boundary, 2.1 hardening score, and byte-identical validation artifacts are recorded in [`docs/DEPLOYMENT_EVIDENCE.md`](docs/DEPLOYMENT_EVIDENCE.md). The installed service remains disabled and inactive.

---

## Repository Map

| Path | Responsibility |
|---|---|
| `src/quantum_trader/domain/` | Immutable models, equity forecasts, factor-aware portfolio construction, research-only execution-cost/capacity and dependence-aware inference estimates, options contracts/lifecycle accounting, strategy, risk, execution, market controls, approvals, and request budgets |
| `src/quantum_trader/application/` | Simulation, evaluation, crash-safe paper execution, reconciliation, pre-trade control, operator action, lifecycle, and reporting orchestration |
| `src/quantum_trader/ports/` | Simulation and external broker, control-data, broker journal, immutable experiment ledger, operator-control, market-data, and event-store interfaces |
| `src/quantum_trader/adapters/` | CSV replay, point-in-time SEC/Treasury/local reference ingestion, immutable snapshot writing, simulation, fixed-origin paper/control-data clients, strict credential files, and hardened SQLite stores including the immutable experiment ledger |
| `tests/unit/` | Domain invariants and defensive-path coverage |
| `tests/integration/` | Deterministic replay, broker reconciliation, operator actions, literal process termination, partial fills, cancel races, and crash recovery |
| `tests/smoke/` | Installed CLI, artifact, and prohibited-mode checks |
| `docs/` | Architecture, methodology, strategy governance, hypothesis evidence, immutable experiment ledger, point-in-time reference ingestion, forecast-signal, portfolio-construction, execution-cost, inference-and-robustness, and options-lifecycle contracts, failure injection, operator controls, legacy audit, deployment, and visual evidence |
| `research/governance/` | Machine-enforced grade policy, baseline identities, frozen evidence checksums, and bounded hypothesis catalog |
| `research/schemas/` | Eighteen provider-neutral point-in-time equity, event, volatility, options, rate, and immutable snapshot contracts plus a hash manifest |
| `deployment/` | Hardened simulation-only systemd templates |

---

## Project History and Evidence Boundary

The current evidence-weighted research-software assessment is **A+ (97.95/100)**; the original audited v0.1.0 baseline remains recorded at A- (92.45/100). The complete rubric, failed-strategy distinction, blocked paper activation, and separate assessment of the original project’s ambition are documented in [`docs/ENGINEERING_GRADE.md`](docs/ENGINEERING_GRADE.md).

The original archive contained 381 Python and launcher files, 35.61 GB of historical logs, multiple databases, model artifacts, backup generations, Windows scheduled-task launchers, Alpaca integration, and Kalshi components. It also contained hard-coded credential findings, conflicting dependency manifests, syntax and interface failures, silent synthetic fallbacks, and direct live-order call sites. The original files remain preserved outside this repository and were never executed during the audit.

The archive documents substantial engineering effort and long-running operation, but it does **not independently prove a year of profitable broker execution**. The only outcome database contains 440 `AI_BUY` rows with no exit price or recorded P&L, the automation performance tables are empty, and the available records lack broker fill IDs, fees, account identifiers, and cash-flow reconciliation. This repository therefore makes no verified-live-profitability claim. See [`docs/LEGACY_AUDIT.md`](docs/LEGACY_AUDIT.md) for the full evidence boundary.

---

## Known Limitations

The offline simulator currently models one long-only asset per run and does not apply dividends, corporate actions, queue position, market impact, halts, borrow, margin, options, or taxes. Its bundled demo uses synthetic data and is not performance evidence. The preregistered six-asset walk-forward campaign failed its promotion gate, so the final holdout remains locked and the strategy has no validated alpha claim.

The internal paper layer now has fixed-sandbox authentication contracts, broker calendars, current-state controls, deterministic order IDs, partial-fill reconciliation, durable recovery, and operator safeguards, but the configured external account preflight remains unauthenticated and **no public paper command exists**. Physical power loss, real device-level storage exhaustion, authenticated rate-limit and outage behavior, broker-originated partial fills, paper service-manager recovery, and a validated flatten action remain acceptance work. All live execution remains unavailable.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Changes must preserve the simulation-only boundary, maintain deterministic replay, add tests for new behavior, and pass the complete quality gate before review.

---

## License

MIT — see [`LICENSE`](LICENSE).

## References

[1]: https://finance.yahoo.com/quote/SPY/history/ "Yahoo Finance — SPY Historical Data"
[2]: https://www.nasdaq.com/market-activity/etf/spy/historical "Nasdaq — State Street SPDR S&P 500 ETF Trust (SPY) Historical"
