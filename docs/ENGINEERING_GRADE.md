# Quantum Trader Pro — Engineering Grade

> This rubric measures **portfolio-grade research software engineering**, not investment returns. It does not certify profitable alpha, authenticated paper operation, or real-money readiness.

| Version or dimension | Weighted score | Grade | Interpretation |
|---|---:|---:|---|
| Original legacy prototype | 34.50/100 | **F** | Impressive scope, but unsafe and unverifiable for another engineer to deploy |
| Original ambition and learning value | Qualitative | **A-** | Unusually broad systems exploration for a first project |
| Audited v0.1.0 simulation platform | 92.45/100 | **A-** | Strong, reproducible, portfolio-ready baseline |
| Current research and safety platform | **97.95/100** | **A+** | Exceptional evidence discipline, failure handling, reproducibility, and portfolio presentation |
| Current strategy evidence | Not scored as engineering | **Failed gate** | No validated alpha claim; the locked holdout remains unopened |
| Authenticated paper activation | Not yet eligible | **Blocked** | No public paper command, authenticated campaign, or flatten implementation |
| Live trading | Not eligible | **Unavailable** | No live adapter or real-money execution command exists |

The grade scale is **A+ = 97–100**, **A = 93–96.99**, **A- = 90–92.99**, **B = 80–89.99**, **C = 70–79.99**, **D = 60–69.99**, and **F below 60**.

## Weighted Rubric

| Category | Weight | Legacy | Current platform | Evidence |
|---|---:|---:|---:|---|
| Architecture and modularity | 15% | 5.5/10 | 9.8/10 | Ports and adapters separate domain policy, simulation, fixed-origin paper transport, durable journals, reconciliation, operator controls, and reporting. Strategy code cannot submit directly.[1] |
| Correctness and reliability | 15% | 3.5/10 | 9.8/10 | Exact accounting, next-event causality, deterministic identifiers, partial-fill projection, lookup-only recovery, cancel-race handling, literal process termination, and transaction rollback are executable contracts.[2] |
| Safety and secret handling | 15% | 2.0/10 | 9.9/10 | Public commands remain simulation/research-only; live execution is structurally unavailable. Optional paper internals use fixed sandbox origins, strict descriptor-relative credential files, default pause, one-use approvals, fresh reconciliation, and owned-order-only cancellation.[3] |
| Testing and verification | 15% | 3.0/10 | 9.8/10 | The locked suite has **162 passing tests** and **90.23% branch coverage**, including literal subprocess exit and repeated recovery, simulated `SQLITE_FULL`, HTTP ambiguity, stale state, market calendars, permission races, and injected database failures.[1] [2] |
| Reproducibility and dependencies | 10% | 3.0/10 | 9.9/10 | Runtime dependencies remain empty; development tools are frozen in `uv.lock`. Five workflow actions are pinned to immutable SHAs, the locked graph is vulnerability-audited, CI emits a CycloneDX SBOM, and simulation/evaluation artifacts reproduce byte-for-byte.[1] |
| Performance evidence and methodology | 10% | 2.5/10 | 9.8/10 | A preregistered six-asset protocol retained **2,604 trials** and 84 base-cost folds, published the unfavorable result, and kept the final 252 observations per asset locked after the strategy failed promotion.[4] [5] |
| Documentation and portfolio usability | 10% | 4.5/10 | 9.8/10 | The repository includes one-click launchers, architecture, methodology, research protocol, retained evidence, safety, threat models, operator controls, failure injection, legacy audit, contribution guidance, and deployment instructions.[1] |
| Operations and deployment | 10% | 3.5/10 | 9.5/10 | Exact commit `454afec9ce7e06041517b74004ef19acd331db5e` is installed from a checksummed wheel on the cloud computer. Two network-isolated systemd runs were byte-identical; the service scored **2.1 OK** and remains disabled/inactive with rollback preserved. Authenticated paper and service-manager recovery for that path remain open.[3] [6] |

The weighted current score is:

`(15×9.8 + 15×9.8 + 15×9.9 + 15×9.8 + 10×9.9 + 10×9.8 + 10×9.8 + 10×9.5) ÷ 10 = 97.95`

## Why This Can Be A+ While the Strategy Failed

The strongest engineering outcome is that the platform **rejected its own apparent edge**. The protocol and candidate set were frozen before the six-asset panel was retrieved. The evaluator retained every candidate, cost, and robustness trial, published the negative result, and refused to open the holdout. The base-cost folds produced a median excess return of **−2.27%**, only **15.48%** beat the adjusted-close benchmark, and the worst test drawdown was **−34.33%**.[4] [5]

An engineering grade rewards whether the system discovers and communicates that failure correctly. It does not convert a failed strategy into a successful investment claim.

## Why This Is Not Yet an A+ Trading Product

The public operator surface cannot submit a paper order. The configured external account preflight was unauthenticated, no real sandbox fill or broker rate-limit response has been retained, no position-flatten action exists, and paper service-manager or host-loss recovery has not been accepted. Live execution is intentionally absent.[3]

Accordingly, the accurate résumé claim is **“built an A+ research and safety engineering platform”**, not “built an A+ profitable live trading system.”

## Remaining Promotion Gates

| Gate | Current state | Evidence required before promotion |
|---|---|---|
| Authenticated sandbox connectivity | Blocked | Securely configured paper credentials; account, clock, asset, quote, order, activity, and position reads tied to exact fingerprints |
| Multi-session paper acceptance | Blocked | Several market sessions with retained reconciliation receipts, disconnect recovery, rate-limit handling, and real partial-fill evidence where available |
| Position flattening | Unavailable | Separate action, acknowledgment, bounded symbol scope, partial-fill handling, terminal verification, reconciliation, and independent tests |
| Paper service-manager recovery | Unverified | Kill/restart at durable boundaries under the deployed service with proof of no duplicate submission |
| Strategy promotion | Failed | A new preregistered strategy version must pass pre-holdout gates before a new holdout can be opened |
| Live execution | Unavailable | Out of scope; would require a separate design, threat model, broker evidence campaign, and explicit user authorization |

## Why the Legacy Grade Is Not a Judgment on the Project’s Value

As a first build, the historical project attempted broker integration, scheduling, risk logic, portfolio allocation, machine-learning components, options, prediction markets, monitoring, and year-scale operation. That ambition supports the separate **A- learning-value assessment**. Its deployment grade is lower because another engineer could not safely identify one authoritative core, reproduce the evidence, or run the system without live-order and credential risk.[7]

## References

[1]: ../README.md "Quantum Trader Pro README"
[2]: FAILURE_INJECTION.md "Failure Injection and Recovery Evidence"
[3]: LIVE_READINESS.md "Live-Readiness Contract"
[4]: RESEARCH_PROTOCOL.md "Preregistered Research Protocol"
[5]: ../evaluation/results/v1-preholdout/README.md "Pre-Holdout Evidence Bundle"
[6]: DEPLOYMENT_EVIDENCE.md "Cloud Deployment Evidence"
[7]: LEGACY_AUDIT.md "Legacy Audit"
