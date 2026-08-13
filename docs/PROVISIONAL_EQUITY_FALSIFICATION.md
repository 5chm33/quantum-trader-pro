# Provisional Daily-Equity Falsification Result

> **Classification: provisional, non-promotion falsification only.** This result uses a private adjusted-price prototype and is not evidence of alpha, investability, an A+ strategy grade, options performance, paper-trading readiness, or live-trading readiness. It does not open or substitute for a final holdout.

The frozen H01/H04 daily diagnostic did **not** show a basis to advance the tested candidate. Across the fixed 504-return sample, the volatility-scaled trend candidate trailed the permanent equal-weight buy-and-hold baseline on cumulative and annualized return, had a larger maximum drawdown, and incurred positive turnover. The candidate exceeded the unscaled trend comparator on this descriptive sample, but that fact is insufficient to overcome the equal-weight comparator, the short sample, unverified historical availability, proxy corporate actions, and simplified cost model. The candidate remains at **development** in the private immutable ledger; no promotion transition was attempted.

## Frozen Inputs and Boundaries

| Item | Retained value |
|---|---|
| Protocol | `qtpro-provisional-daily-equity-v1` at [`provisional_daily_equity_v1.json`](../research/protocols/provisional_daily_equity_v1.json) |
| Snapshot | `qtpro-provisional-daily-equity-snapshot-v1`, content SHA-256 `5c7ae8818605635b50f7bca5fb70d60b2e696fd7df8c2b74ebbb760354cf3923` |
| Source basis | Private legacy daily adjusted-price CSV subset, originally sourced from the Yahoo Finance public chart endpoint; raw market data is not redistributed. |
| Universe | EFA, GLD, IWM, QQQ, SPY, and TLT; 860 synchronized daily rows per asset from 2015-08-03 through 2018-12-31. |
| Exclusion boundary | The snapshot builder stopped at `2019-01-01T00:00:00+00:00`; no later source row was read. This excluded tail is **not** a holdout. |
| Candidate | H01 252-bar sign trend with H04 63-bar realized-volatility scaling, 12% target, 42 minimum return observations, 0.00–1.50 multiplier bounds, and a 21-bar rebalance interval. |
| Decision/return proxy | A post-close signal earns the next adjusted-close mark-to-market return. This is not a next-open executable-fill simulation. |
| Comparison set | Equal-weight buy-and-hold, unscaled trend-only, and cash/zero exposure—each retained in the private comparison ledger. |
| Code | Commit `b0de478865e3dc55b7bda14518195b51966e5fd7` |
| Private artifact hashes | `summary.json`: `0f2b5b228c1a0aa63b85e0a13b92fa37d2d8b39828c6d1928219eca0ffbb610b`; `report.md`: `4f52193578b824b272d45bfed1868cc5bad3d8bf3af4a54ea9334d1654d5a4aa` |
| Reproduction | Two isolated runs produced byte-identical `summary.json` and `report.md` artifacts. |

## Descriptive Portfolio Results

The results below are descriptive calculations from the fixed protocol. They are not risk-adjusted performance claims, statistical tests, or candidate-selection criteria.

| Portfolio | Cumulative return | Annualized return | Annualized volatility | Maximum drawdown | Annualized turnover |
|---|---:|---:|---:|---:|---:|
| **H01/H04 candidate** | 11.7963% | 5.7338% | 8.3275% | -9.6599% | 4.2800x |
| Trend-only baseline | 10.7877% | 5.2558% | 7.7160% | -7.4370% | 3.0000x |
| **Equal-weight buy-and-hold baseline** | **23.7252%** | **11.2318%** | 8.1250% | **-7.9597%** | 0.0000x |
| Cash baseline | 0.0000% | 0.0000% | 0.0000% | 0.0000% | 0.0000x |

The candidate’s 15-basis-point one-way-turn sensitivity reduced cumulative return to **10.3721%**. The unscaled trend baseline’s corresponding sensitivity was **9.7966%**, while equal weight remained **23.7252%** because the simplified model assigns it zero trading turnover after inception. These sensitivity figures do not establish actual implementation costs: the snapshot has no historical bid/ask, quote size, impact, participation, borrow, tax, or financing data.

## Fixed Fold Diagnostics

The 504 daily returns are split into four fixed 126-return descriptive folds. No parameter was selected from these fold results.

| Portfolio | Fold 1 cumulative return | Fold 2 cumulative return | Fold 3 cumulative return | Fold 4 cumulative return |
|---|---:|---:|---:|---:|
| H01/H04 candidate | -0.1230% | 5.4010% | 10.2601% | -3.6839% |
| Trend-only baseline | -0.2095% | 4.0032% | 7.8478% | -1.0206% |
| Equal-weight buy-and-hold baseline | 1.8987% | 9.2587% | 10.7248% | 0.3664% |
| Cash baseline | 0.0000% | 0.0000% | 0.0000% | 0.0000% |

The candidate had two negative and two positive descriptive folds. The equal-weight baseline was positive in all four folds and had the highest reported cumulative return in the fixed sample. This result is enough to stop any claim that this specific provisional H01/H04 configuration has demonstrated superiority; it is not enough to prove that time-series momentum or volatility targeting is ineffective more generally.

## Ledger and Control Verification

The private SQLite ledger recorded one campaign, one H01 candidate, a development transition, one frozen preregistration, four completed development attempts, four retained artifacts, and one completed comparison group. A read-only verification found the candidate state to be `development` and the holdout table count to be zero. It found no `holdout_sealed`, `holdout_open_approved`, `holdout_opened`, or `holdout_completed` event.

| Control | Verified outcome |
|---|---|
| Candidate state | `development` only |
| Candidate promotion | Not attempted |
| Holdout records | 0 |
| Options evaluation | Not performed |
| Paper/live orders | Not created |
| Private source data in repository | Not committed |
| Published v1 result | Not modified or relabeled |

## Interpretation and Next Decision

The correct decision from this provisional test is **not to advance this exact configuration**. It is neither a permanent rejection of H01/H04 nor evidence for a different candidate. The next evidence-grade decision remains unchanged: obtain a qualified point-in-time equity/ETF data set before creating a new promotion-capable campaign. Options remain separately blocked pending historical contract, quote, Greeks-input, lifecycle, and adjustment data.[1] [2]

The project may later run a **new, separately frozen** provisional specification to test a bounded alternative, but it must retain the same non-promotion classification and must not use this result to tune its lookback, volatility target, multiplier cap, universe, cost assumptions, or fold boundaries. Any evidence-grade successor must use the immutable experiment ledger, a qualified snapshot, permanent baselines, a separately sealed final holdout, and the governance gates already published by the project.[3]

## References

[1]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/PHASE14_OPTIONS_DATA_READINESS.md "Quantum Trader Pro — Phase 14 Options Data-Readiness Decision"
[2]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/PHASE13_DATA_READINESS.md "Quantum Trader Pro — Phase 13 Equity and ETF Data-Readiness Decision"
[3]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/research/governance/strategy_grade_policy_v1.json "Quantum Trader Pro — Frozen Strategy-Grade Policy"
