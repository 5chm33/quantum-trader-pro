# Quantum Trader Pro — Engineering Grade

> The rubric measures **portfolio and safe-deployment readiness**, not creativity or effort. The original project’s ambition and learning value are materially stronger than its reproducibility grade.

| Version | Weighted score | Grade | Interpretation |
|---|---:|---:|---|
| Original legacy prototype | 34.50/100 | **F** | Impressive scope, but unsafe and unverifiable for another engineer to deploy |
| Original ambition and learning value | Qualitative | **A-** | Unusually broad systems exploration for a first project |
| Clean simulation platform | 92.45/100 | **A-** | Strong, reproducible, portfolio-ready engineering repository |

## Weighted Rubric

| Category | Weight | Legacy | Clean platform | Evidence |
|---|---:|---:|---:|---|
| Architecture and modularity | 15% | 5.5/10 | 9.2/10 | The legacy tree had broad components but broken contracts, duplicate launchers, and unclear authority. The clean platform has ports and adapters, immutable models, dependency injection, and one explicit entry point. |
| Correctness and reliability | 15% | 3.5/10 | 9.1/10 | The legacy core had syntax, import, and signature failures plus silent synthetic fallbacks. The clean engine reconciles accounting, validates inputs, enforces next-event causality, and exits risk safely. |
| Safety and secret handling | 15% | 2.0/10 | 9.8/10 | The legacy launcher defaulted to live mode and contained credential and direct-order paths. The clean runtime cannot represent paper or live execution and has no credential or broker dependency. |
| Testing and verification | 15% | 3.0/10 | 9.2/10 | Most historical test-like files lacked assertions or had network/broker side effects. The clean repository has 42 passing unit, integration, and CLI smoke tests with 92.14% coverage. |
| Reproducibility and dependencies | 10% | 3.0/10 | 9.5/10 | Conflicting manifests and versioned backups were replaced by one typed package, no third-party runtime dependency, checksummed inputs, deterministic IDs, and byte-identical reruns. |
| Performance evidence and methodology | 10% | 2.5/10 | 8.2/10 | The archive lacks authenticated completed-trade reconciliation. The clean platform retains a real-data run, explicit costs, price-only benchmark, drawdown, ledger, caveats, and hashes, but not walk-forward validation. |
| Documentation and portfolio usability | 10% | 4.5/10 | 9.5/10 | Ambiguous historical startup and unsupported claims were replaced by a professional README, architecture, safety, threat model, methodology, legacy audit, contribution, security, and deployment guides. |
| Operations and deployment | 10% | 3.5/10 | 9.3/10 | Restart loops and mixed Windows/Linux assumptions were replaced by a finite least-privilege systemd service with a 2.1 “OK” exposure score and two successful byte-identical cloud runs. |

The weighted clean score is:

`(15×9.2 + 15×9.1 + 15×9.8 + 15×9.2 + 10×9.5 + 10×8.2 + 10×9.5 + 10×9.3) ÷ 10 = 92.45`

## Why the Legacy Grade Is Not a Judgment on the Project’s Value

As a first build, the historical project attempted broker integration, scheduling, risk logic, portfolio allocation, machine-learning components, options, prediction markets, monitoring, and year-scale operation. That ambition is the reason for the separate **A- learning-value assessment**. The deployment grade is lower because a résumé repository must be judged on whether another engineer can install it without secrets, identify its authoritative core, run tests without side effects, reproduce its evidence, and understand its limitations.

## Remaining Gaps

The clean repository is graded as a simulation and research-engineering platform, not a live-trading product. A complete exchange-holiday calendar, dividends and corporate actions, multi-asset allocation, partial-fill and market-impact models, chained or signed ledger integrity, walk-forward evaluation, multiple regimes, and external paper-broker reconciliation remain future work.

A paper-trading adapter could be a reasonable separate milestone after adding broker-specific authentication, idempotent client order IDs, rate-limit and outage handling, account reconciliation, a sandbox test account, and a new threat-model review. Live trading is intentionally excluded and should not be inferred from this grade.
