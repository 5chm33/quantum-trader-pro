# Phase 13 Equity and ETF Data-Readiness Decision

**Evidence-grade decision:** **BLOCKED.** No equity or ETF result can support a strategy-grade claim, candidate promotion, final-holdout access, paper trading, or live trading until an independently sourced point-in-time, total-return, and execution-liquidity-complete snapshot is admitted.

**Provisional decision:** **PERMITTED, under a separately frozen non-promotion protocol.** A reproducible adjusted-daily-price prototype may be used to reject weak H01/H04 equity and ETF ideas, provided it retains provenance, adjustment-method, timestamp, missing-data, universe, and execution limitations. It is development-only evidence and cannot be relabeled as an A+ campaign.

This distinction preserves the important fail-closed boundary while avoiding an unnecessary all-or-nothing rule. A modest daily-price prototype can falsify a weak trend hypothesis; it cannot prove the hypothesis is tradable, establish capacity, validate precise costs, or validate an options extension.

## Evidence-Grade Admission Requirements

| Requirement | Required retained evidence | Evidence-grade Phase 13 status |
|---|---|---|
| Historical point-in-time availability | Vendor or primary-source timestamps showing when each bar, action, and reference input became usable. | **Unavailable.** Current public historical charts have no retained vintage/revision history. |
| Total-return reconstruction | Split/dividend/corporate-action events with announcement, effective, and availability timing, reconciled to the selected total-return series. | **Unavailable.** No independently verified action history is frozen. |
| Execution and liquidity inputs | Point-in-time bid/ask, volume, session, and data-feed coverage sufficient for the declared cost/participation model. | **Unavailable.** No qualified historical quote/volume snapshot is admitted. |
| Frozen universe | Dated membership snapshot and availability receipt. | **Unavailable.** The fixed ETF universe is a convenience basket, not a historical constituent reconstruction. |
| Preregistered campaign | Candidate budget, fold plan, regimes, benchmarks, costs, inference plan, snapshot, and code commit bound in the immutable ledger. | **Not created.** It cannot be truthfully bound before the preceding inputs exist. |
| New campaign lockbox | A separate unretrieved byte range with an explicit-approval path. | **Not created.** No provider-qualified source defines a credible evidence-grade lockbox yet. |

> **Fail-closed outcome:** No evidence-grade candidate, attempt, comparison group, or holdout has been registered. The v1 campaign and its lockbox remain unchanged.

## Segregated Provisional Daily-Equity Prototype

The project now permits one narrow, explicitly labelled development path: [`provisional_daily_equity_v1.json`](../research/protocols/provisional_daily_equity_v1.json). Its purpose is to **falsify**, not promote, a fixed H01 time-series trend forecast with a bounded H04 volatility transform against the permanent equal-weight, unscaled-trend, and cash baselines.

| Attribute | Frozen provisional boundary |
|---|---|
| Dataset | Legacy local adjusted daily-price CSVs originally sourced from Yahoo Finance, preserved outside the repository. |
| Included rows | Six fixed ETFs from 2015-08-03 through 2018-12-31; each source parser stops at 2019-01-01 rather than scanning later rows. |
| Snapshot | `qtpro-provisional-daily-equity-snapshot-v1`, content SHA-256 `5c7ae8818605635b50f7bca5fb70d60b2e696fd7df8c2b74ebbb760354cf3923`, retained privately because redistribution rights were not established. |
| Timestamp convention | A daily adjusted-close value is assumed usable after the close. This convention is not independently verified. |
| Corporate actions | The provider-adjusted series is used as a proxy; no independent dividend/split/action reconciliation exists. |
| Candidate | Fixed H01 252-bar sign trend with H04 63-bar realized-volatility scaling, 12% target, 0.00–1.50 multiplier bound, and 21-bar rebalance interval. |
| Return convention | Post-close decision to next adjusted-close mark-to-market return; this is not a next-open executable-fill simulation. |
| Costs | Zero, 5, and 15 bps one-way turnover sensitivities only; no historical spread, impact, borrow, tax, capacity, or option cost is inferred. |
| Output status | Pending. No prototype computation, selection, promotion, or final-holdout action has been performed. |

The snapshot builder is [`build_provisional_daily_equity_snapshot.py`](../scripts/build_provisional_daily_equity_snapshot.py). It refuses a non-provisional protocol, requires sorted source files and validated OHLCV rows, produces a hash-bound private manifest, and stops reading each source immediately when it reaches the excluded boundary. It is designed to make the limited basis auditable rather than to transform it into point-in-time evidence.

## Public-Data Acquisition Attempts

Two additional public routes were assessed for a fresh bounded acquisition. Neither supplied a usable new snapshot.

| Route | Bounded request outcome | Disposition |
|---|---|---|
| Yahoo Finance public chart endpoint | A fixed-host read-only request for a separate bounded range did not complete in the sandbox within five minutes and was terminated. | No new response set or manifest accepted. The separate 2019–2025 lockbox query was never issued. |
| Stooq daily CSV endpoint | Returned an HTML JavaScript verification wall rather than CSV. Its challenge/POST instruction was not executed. | No observations accepted. |

The provisional snapshot is therefore a controlled subset of retained legacy adjusted-price CSVs, not a newly sourced evidence-grade data set and not a relabeling of the failed v1 campaign.

## What the Prototype Can and Cannot Do

| It can do | It cannot do |
|---|---|
| Reject a fixed daily trend/volatility configuration that is fragile or weak even under an optimistic adjusted-price proxy. | Demonstrate alpha, future profitability, or an A+ strategy grade. |
| Check deterministic arithmetic, warm-up handling, baseline inclusion, cost-sensitivity direction, and simple multi-asset consistency. | Establish historical data availability, contemporaneous ETF membership, corporate-action correctness, market impact, capacity, short borrow, taxes, or live execution quality. |
| Produce a transparent reason to stop pursuing a weak equity hypothesis before acquiring richer data. | Open or substitute for a final holdout, promote a candidate, authorize paper trading, validate options, or enable live trading. |

## Unblock Criteria for Evidence-Grade Campaigns

The next admissible evidence-grade action is to obtain a source with documented rights and historical point-in-time coverage for the declared instruments and period. Before a candidate is registered, the source must be normalized through the published contracts, frozen into an immutable snapshot, and reviewed for the following fields.

| Data component | Minimum acceptance condition |
|---|---|
| Bars and sessions | Historical final/correction status and provider availability time retained per record. |
| Corporate actions | Dividends, splits, distributions, and symbol changes with time-stamped announcement, effective, and availability receipts. |
| Quotes and volume | Historical bid/ask and volume inputs matching the planned execution-cost model’s time granularity. |
| Universe | Membership/delisting history dated and available by each decision cutoff. |
| Benchmarks and rates | Total-return and risk-free series with declared construction and availability. |
| Options extension | Contract, quote, surface/Greeks, lifecycle, and adjustment data with point-in-time coverage; separately required by Phase 14. |

## Evidence Boundary

This document is neither an investment conclusion nor a performance result. It does not reject or validate H01, H04, another equity family, or an options family. It does not open a holdout or change paper/live execution state. The preserved v1 strategy result remains failed under its original protocol, and its holdout remains locked.

## References

[1]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/research/governance/strategy_grade_policy_v1.json "Quantum Trader Pro — Frozen Strategy-Grade Policy"
[2]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/DATA_CONTRACTS.md "Quantum Trader Pro — Point-in-Time Data Contracts"
