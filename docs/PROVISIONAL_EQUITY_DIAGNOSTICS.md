# Provisional Daily-Equity Independent Diagnostics

> **Classification: provisional, non-promotion diagnostics only.** These diagnostics are derived from the private parent snapshot and the already completed development-only H01/H04 result. They do not change any candidate parameter, create a replacement candidate, write to the immutable ledger, transition candidate state, seal or open a holdout, evaluate options, or create paper/live orders.

This report answers the narrow questions raised after the parent result: whether the candidate’s result changes relative to SPY and lower-risk equity/cash comparators, whether the observed return gap is more consistent with allocation, trend timing, or the volatility transform, and whether the aggregate result concealed materially different asset-level outcomes. The new diagnostics **do not reverse the parent decision**: the fixed H01/H04 configuration remains development-only and is not advanced.

## Frozen Basis and Controls

| Item | Retained value |
|---|---|
| Parent protocol | `qtpro-provisional-daily-equity-v1`, SHA-256 `dd3e6136546f9e58ca732b0bcfc8c73a12b7f9a7adf07ea0204d35ba3040c406` |
| Parent summary | SHA-256 `0f2b5b228c1a0aa63b85e0a13b92fa37d2d8b39828c6d1928219eca0ffbb610b` |
| Snapshot | `qtpro-provisional-daily-equity-snapshot-v1`, content SHA-256 `5c7ae8818605635b50f7bca5fb70d60b2e696fd7df8c2b74ebbb760354cf3923` |
| Diagnostic protocol | [`provisional_daily_equity_diagnostics_v1.json`](../research/protocols/provisional_daily_equity_diagnostics_v1.json), SHA-256 `a1c58912722370c174ef234a8315ce4d8dd54df72b06925f79bb291282c017fb` |
| Code | Commit `6b674b9f0bb9e61dfdf21a2c2342a95331d32495` |
| Derived artifacts | `diagnostics.json` SHA-256 `60cd9856000f5d53f8541ed437af1c5fbf1643efbd1da9ab2800a6c3e4666694`; `diagnostics.md` SHA-256 `e103d3e6232404e73d610e0661248810708d1ee527fc69da5b70273a871a94cf`; receipt SHA-256 `8839defe3e8fe7f67bacc1da2a53836476a6c024de0c572ea8e823e8dbb6c43b` |
| Reproduction | Two isolated runs produced byte-identical diagnostic JSON, Markdown, and receipt artifacts. |
| Ledger/holdout control | The derived receipt records `ledger_write_performed: false`, `candidate_state_transition_performed: false`, and `holdout_action_performed: false`. |

The sample remains six synchronized ETF adjusted-price series from 2015-08-03 through 2018-12-31, evaluated as 504 post-close-to-next-adjusted-close returns. The `2019-01-01` boundary was not read by the snapshot builder and is not a holdout. Raw provider data is private and is not redistributed.[1]

## Benchmark Alignment

The parent candidate is compared with additional predeclared descriptive references. SPY is represented by its adjusted-close return proxy. The cash-plus-equity reference is **60% SPY and 40% zero-return cash, rebalanced daily**, because no point-in-time cash-rate input is admitted. The volatility-matched equal-weight reference uses the same 63-bar trailing window, 12% target, 0.00–1.50 cap, and **21-bar rebalancing interval** as the fixed H04 overlay.

| Portfolio or benchmark | Cumulative return | Annualized return | Annualized volatility | Maximum drawdown | Annualized turnover |
|---|---:|---:|---:|---:|---:|
| H01/H04 parent candidate | 11.7963% | 5.7338% | 8.3275% | -9.6599% | 4.2800x |
| SPY adjusted-return proxy | 36.1714% | 16.6925% | 10.5379% | -10.1019% | 0.0000x |
| 60% SPY / 40% zero-return cash | 20.6770% | 9.8531% | 6.3227% | -6.1408% | 0.0000x |
| Volatility-matched equal weight | 32.0339% | 14.9060% | 10.4327% | -11.0036% | 0.7887x |
| Equal-weight buy-and-hold parent baseline | 23.7252% | 11.2318% | 8.1250% | -7.9597% | 0.0000x |

The candidate had lower realized volatility and a slightly smaller maximum drawdown than the SPY proxy, but it trailed both SPY and the 60% SPY/40% cash reference in cumulative and annualized return. It also trailed the volatility-matched equal-weight reference by **20.2376 percentage points** of cumulative return while showing lower realized volatility. These descriptive comparisons leave open the possibility that the candidate altered exposure, but do not support an investability or value-added claim because the sample and execution inputs are insufficient.

## Descriptive Return Attribution

The bridge reports each cumulative difference against its immediately preceding reference; it is **non-additive** and not a causal factor model. The beta figures are ordinary-sample beta proxies from the same 504 daily adjusted-price returns, not factor exposures estimated from a qualified data set.

| Bridge component | Cumulative return or difference |
|---|---:|
| SPY market-reference proxy | 36.1714% |
| Equal-weight allocation relative to SPY | -12.4462% |
| Trend timing relative to equal weight | -12.9374% |
| Volatility scaling relative to trend | +1.0086% |
| H01/H04 candidate relative to equal weight | -11.9289% |

| Portfolio | SPY beta proxy |
|---|---:|
| H01/H04 candidate | 0.6674 |
| Trend-only baseline | 0.6518 |
| Equal-weight buy-and-hold | 0.7054 |

Within this narrow bridge, the volatility transform increased cumulative return relative to the unscaled trend baseline by 1.0086 percentage points, but neither transform nor trend timing closed the gap to the equal-weight reference. The candidate’s retained 0/5/15-basis-point one-way-turn sensitivities were **11.7963%**, **11.3197%**, and **10.3721%** cumulative return, respectively. Those are scenario sensitivities—not historical cost estimates—because no historical bid/ask, quote size, impact, borrow, financing, tax, or cash-rate series has been admitted.

## Asset-Level Diagnostic

Each sleeve applies the fixed parent H01 direction and H04 multiplier to one ETF at the same 21-bar rebalance frequency, without the parent portfolio’s one-sixth sizing. Its comparison is only that ETF’s adjusted-price buy-and-hold proxy; it is not a new candidate, a tradable standalone recommendation, or a signal-family conclusion.

| ETF | H01/H04 sleeve cumulative return | Sleeve annualized turnover | ETF buy-and-hold proxy cumulative return |
|---|---:|---:|---:|
| EFA | 29.6555% | 3.9085x | 25.5749% |
| GLD | -36.6326% | 10.0302x | -12.0903% |
| IWM | 30.4222% | 1.7334x | 43.4546% |
| QQQ | 59.3351% | 1.6355x | 59.0496% |
| SPY | 50.6958% | 1.1807x | 36.1714% |
| TLT | -28.0084% | 7.1918x | -9.8092% |

The aggregate result is not driven by a uniform outcome. The fixed sleeve outperformed its adjusted-price buy-and-hold proxy for EFA, QQQ, and SPY, while substantially trailing for GLD, IWM, and TLT; GLD and TLT also had the highest retained sleeve turnover. This explains why a six-asset aggregate can differ materially from individual sleeves, but it does not authorize removing weak assets or retuning the fixed configuration after observing the result.

## Adjustment Timing and Interpretation Boundary

The adjusted-price decision safety remains **unverified**. A retrospective adjusted close can incorporate split or distribution information whose announcement, effective, correction, and provider-availability times are not retained. Consequently, neither the parent result nor these diagnostics assert that adjusted prices were usable at the simulated decision timestamp. The analysis cannot establish executable fills, historical total-return accuracy, precise transaction costs, capacity, or a tradable short exposure.[1]

The zero-holdout result is a control observation, not a passing result: it confirms that neither the parent development run nor this derived diagnostic retrieved or opened a final holdout. A future evidence-grade candidate must be separately registered, use a qualified point-in-time snapshot and action policy, include its comparison set and promotion gates before execution, and seal an unretrieved final holdout.[2] [3]

## Decision

The updated decision is unchanged: **do not advance the fixed provisional H01/H04 configuration.** The independent diagnostics make that decision more interpretable, not more permissive. They show modest volatility-transform uplift versus the unscaled trend version and heterogeneous asset-level behavior, but they do not eliminate the aggregate shortfall to the permanent equal-weight, SPY, cash-plus-equity, or volatility-matched equal-weight references. Options remain unvalidated pending qualified historical options data.

This is research and analysis only, not personalized financial advice.

## References

[1]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/PHASE13_DATA_READINESS.md "Quantum Trader Pro — Phase 13 Equity and ETF Data-Readiness Decision"
[2]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/PREREGISTRATION_PROTOCOL.md "Quantum Trader Pro — Preregistration, Walk-Forward, Regime, and Lockbox Protocol"
[3]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/research/governance/strategy_grade_policy_v1.json "Quantum Trader Pro — Frozen Strategy-Grade Policy"
