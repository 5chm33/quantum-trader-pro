# Phase 13 Data-Readiness Decision

**Decision:** **BLOCKED — no equity or ETF falsification attempt is registered or run.**

Phase 13 is deliberately stopped at the data-admission gate. The project now has point-in-time schemas, snapshot contracts, forecasts, portfolio constraints, cost models, inference diagnostics, and a preregistration framework. It does **not** yet have a complete independently sourced ETF dataset that meets the frozen campaign’s requirements for historical availability, total-return reconciliation, executable-liquidity inputs, and a separately sealed campaign lockbox. Running an attractive backtest without those controls would weaken, rather than advance, the evidence standard.

## Required Admission Evidence

| Requirement | Required retained evidence | Current Phase 13 status |
|---|---|---|
| Historical point-in-time availability | Vendor or primary-source timestamps that show when each bar, action, and reference input became usable. | **Unavailable.** Public historical charts retrieved today do not provide historical-vintage revision records. |
| Total-return reconstruction | Split/dividend/corporate-action events with announcement, effective, and availability timing, reconciled to the selected total-return series. | **Unavailable.** No independently verified action history is frozen for the proposed dataset. |
| Execution and liquidity inputs | Point-in-time bid/ask, volume, session, and data-feed coverage sufficient for the declared cost/participation model. | **Unavailable.** No qualified historical quote/volume snapshot has been admitted. |
| Frozen universe | Dated membership snapshot and availability receipt. | **Unavailable.** The proposed fixed ETF list can be described, but not yet bound to an admitted dataset. |
| Preregistered configuration | Candidate budget, fold plan, regimes, benchmark, costs, inference plan, snapshot, and code commit bound into a ledger freeze. | **Not created.** It cannot be truthfully bound before the preceding inputs exist. |
| New campaign lockbox | A separate, unretrieved byte range and explicit-approval path. | **Not created.** No provider-qualified source is available to define a credible lockbox query. |

## Public-Data Attempts

Two public routes were assessed only for a bounded pre-holdout acquisition interval. Neither supplied a usable campaign dataset.

| Route | Bounded request outcome | Disposition |
|---|---|---|
| Yahoo Finance public chart endpoint | The fixed-host read-only request did not complete in the sandbox within five minutes and was terminated. | No response set or manifest accepted. The separate 2019–2025 lockbox query was never issued. |
| Stooq daily CSV endpoint | Returned an HTML JavaScript verification wall rather than CSV. The page’s instruction to calculate a challenge and submit a verification POST was not executed. | No observations accepted. |
| Existing v1 local public-data copy | Contains only a legacy public-chart provenance record and a historical subset. It has no independent historical-vintage availability, complete corporate-action, or qualified liquidity evidence for the new campaign. | May not be relabeled as a Phase 13 campaign snapshot or used to alter the failed v1 result. |

> **Fail-closed outcome:** No candidate, attempt, comparison group, or holdout has been registered in the immutable experiment ledger. No calculation has been performed on any post-2018 row, and no separate lockbox range has been fetched.

## Why a Public Chart Is Insufficient

A present-day adjusted-price history can be useful for exploratory falsification, but it does not, by itself, establish the historical availability of each data revision, dividend, split, symbol mapping, or the quote/volume conditions needed to apply a cost model. The project’s frozen strategy policy requires point-in-time data, total-return/risk-matched benchmarks, spread/fees/impact/participation, capacity, and an immutable new-campaign lockbox before a strategy can advance.[1] The data-contract layer further requires explicit availability, provenance, and snapshot receipts.[2]

This finding does **not** mean that H01 time-series momentum, other equity hypotheses, or defined-risk options are disproved. It means the repository has not earned the right to evaluate them under its stated A+ evidence rules. A public-data-only backtest could be added later as an explicitly non-promotion educational prototype, but it must remain segregated from ledger-backed acceptance evidence.

## Unblock Criteria

The next admissible action is to obtain a source with documented rights and historical point-in-time coverage for the declared instruments and period. Before any candidate is registered, the source must be normalized through the published contracts, frozen into an immutable snapshot, and reviewed for these specific fields:

| Data component | Minimum acceptance condition |
|---|---|
| Bars and sessions | Historical final/correction status and provider availability time are retained per record. |
| Corporate actions | Dividends, splits, distributions, and symbol changes have time-stamped announcement/effective/availability receipts. |
| Quotes and volume | Historical bid/ask and volume inputs match the planned execution-cost model’s time granularity. |
| Universe | Membership/delisting history is dated and available by each decision cutoff. |
| Benchmark and rates | Total-return and risk-free series have declared construction and availability. |
| Options extension | Contract, quote, surface/Greeks, lifecycle, and adjustment data have real point-in-time coverage; this is separately required for Phase 14. |

Once those prerequisites exist, the campaign may register its exact bounded candidate family, freeze its protocol in the ledger, seal a distinct lockbox without retrieving its bytes, and run the declared pre-holdout falsification tests. A failure must be published; a pass can only become eligible for later gates under the frozen governance policy.

## Evidence Boundary

This is a **data-readiness rejection**, not an investment conclusion, a candidate rejection, a performance result, a strategy grade, a holdout opening, a paper-trading authorization, or live-trading authorization. The v1 failed campaign remains unchanged and its holdout remains locked.

## References

[1]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/research/governance/strategy_grade_policy_v1.json "Quantum Trader Pro — Frozen Strategy-Grade Policy"
[2]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/DATA_CONTRACTS.md "Quantum Trader Pro — Point-in-Time Data Contracts"
