# Phase 13 Daily-Equity and ETF Data-Readiness Decision

> **Evidence-grade decision: blocked.** No daily-equity or ETF result can support a strategy-grade claim, candidate promotion, final-holdout access, paper trading, or live trading until a qualified, separately frozen campaign snapshot is admitted.

> **Provisional decision: permitted only as non-promotion falsification.** A reproducible adjusted-daily-price prototype may reject a weak fixed H01/H04 configuration when it retains its limitations. It is development-only evidence and cannot be relabeled as an A+ campaign.

This document uses a **proportionate daily-equity standard**. A daily ETF campaign does not require OPRA-quality options data before an option instrument, option signal, option portfolio allocation, or option execution rule enters that campaign. It does require the daily-equity evidence needed to make its stated timing, adjustment, universe, liquidity, cost, benchmark, and promotion claims auditable.

## Evidence-Grade Daily-Equity Admission Requirements

| Requirement | Minimum retained evidence before registration | Current status |
|---|---|---|
| Historical decision-time availability | Provider timestamp, final/correction status, and documented availability policy for every daily bar and reference input. | **Unavailable.** Current public charts have no retained vintage/revision evidence. |
| Adjustment and corporate-action policy | A declared adjusted/unadjusted price convention plus dividends, splits, symbol changes, and other relevant corporate actions with effective and availability times. The campaign must state whether an adjustment could have been known at each decision cutoff. | **Unavailable.** No independently verified action history or time-bounded adjustment policy is frozen. |
| Declared universe and eligibility | An ex-ante asset list or a dated membership/eligibility rule, including start dates, delistings, substitutions, missing-history policy, and a frozen-universe receipt. A fixed ETF basket is permitted only if frozen before results. | **Unavailable.** The prior six-ETF basket was a provisional convenience basket, not a new registered campaign universe. |
| Trading calendar and missing-data handling | Exchange/session calendar, holidays, early closes, stale/missing-bar rule, suspension rule, and the no-trade or all-cash fallback. | **Unavailable.** No provider-qualified calendar/coverage receipt is frozen. |
| Liquidity and defensible costs | Historical daily volume and the fields needed by the selected, predeclared execution model. A daily campaign may use a conservative bar/volume cost model if it retains the model inputs and assumptions; it must use quotes if it claims quote-based execution. | **Unavailable.** No qualified historical daily volume/liquidity snapshot or admitted cost calibration is frozen. |
| Realistic cash and benchmark inputs | A declared total-return market reference and a contemporaneous short-rate or Treasury-bill total-return series with availability evidence. Zero-return cash must remain a stress baseline, not the sole realistic cash comparator. | **Unavailable.** The repository can ingest rate curves, but no campaign-qualified rate snapshot is frozen. |
| Campaign protocol and comparison family | Candidate family and ceiling, permanent and additional baselines, exposure/turnover/capacity assumptions, folds, embargoes, regimes, inference plan, failed/inconclusive/promoted rules, and code identity. | **Not created.** It must be bound only after the preceding evidence is frozen. |
| Separate final lockbox | A hash-bound, unretrieved provider query/range that is excluded from candidate development and requires explicit approval to open. | **Not created.** No qualified provider source has yet defined the fresh campaign snapshot. |

> **Fail-closed outcome:** No evidence-grade daily-equity candidate, attempt, comparison group, or holdout has been registered. The published v1 campaign and its lockbox remain unchanged.

## Segregated Provisional Daily-Equity Result

The project completed one narrow development-only path: [`provisional_daily_equity_v1.json`](../research/protocols/provisional_daily_equity_v1.json). It fixed an H01 time-series trend forecast with an H04 volatility transform before execution and compared it with the permanent equal-weight, unscaled-trend, and zero-cash baselines. The resulting candidate remained at `development` and is explicitly **not advanced**.[1]

| Attribute | Frozen provisional boundary |
|---|---|
| Dataset | Legacy local adjusted daily-price CSVs originally sourced from Yahoo Finance and retained outside the repository. |
| Included rows | Six fixed ETFs from 2015-08-03 through 2018-12-31; the parser stopped at 2019-01-01 rather than scanning later rows. |
| Snapshot | `qtpro-provisional-daily-equity-snapshot-v1`, content SHA-256 `5c7ae8818605635b50f7bca5fb70d60b2e696fd7df8c2b74ebbb760354cf3923`; it remains private because redistribution rights were not established. |
| Timing and actions | A daily adjusted close was assumed usable after close. This assumption and corporate-action availability are unverified. |
| Candidate | Fixed H01 252-bar sign trend with H04 63-bar realized-volatility scaling, 12% target, 0.00–1.50 multiplier bound, and 21-bar rebalancing. |
| Return/cost treatment | Post-close decision to next adjusted-close mark-to-market return; 0/5/15-bps one-way turnover sensitivities only. This is not a next-open fill or historical quote/impact simulation. |
| Result | The fixed configuration trailed equal weight and additional independent SPY, cash-plus-equity, and volatility-matched references. It remains development-only. [1] [2] |

The snapshot builder, [`build_provisional_daily_equity_snapshot.py`](../scripts/build_provisional_daily_equity_snapshot.py), refuses a non-provisional protocol, validates sorted local OHLCV rows, creates a hash-bound private manifest, and stops immediately at the excluded boundary. It makes the limited data basis auditable; it does not convert the input into point-in-time evidence.

## Public-Data Acquisition Attempts

Two public routes were assessed for a fresh bounded acquisition. Neither supplied a usable new snapshot.

| Route | Bounded request outcome | Disposition |
|---|---|---|
| Yahoo Finance public chart endpoint | A fixed-host read-only request for a separate bounded range did not complete in the sandbox within five minutes and was terminated. | No response set or manifest was accepted. The distinct lockbox range was never requested. |
| Stooq daily CSV endpoint | Returned an HTML JavaScript verification page rather than CSV. Its challenge/POST instruction was not executed. | No observations were accepted. |

The private provisional snapshot is therefore neither a newly sourced daily-equity evidence set nor a relabeling of the failed v1 campaign.

## What the Provisional Result Can and Cannot Do

| It can do | It cannot do |
|---|---|
| Reject a fixed trend/volatility configuration that is weak even under an optimistic adjusted-price proxy. | Demonstrate alpha, future profitability, or an A+ strategy grade. |
| Check deterministic arithmetic, warm-up behavior, baseline inclusion, non-tuning diagnostics, and the direction of simple turnover sensitivities. | Establish decision-time adjustment availability, historical total-return correctness, executable fills, spreads, market impact, capacity, short borrow, taxes, or live execution quality. |
| Give a transparent reason to stop pursuing a fixed equity configuration before acquiring richer data. | Open or substitute for a final holdout, promote a candidate, authorize paper trading, or validate an options extension. |

## Fresh Campaign Freeze Checklist

Before the first provider-qualified daily-equity candidate is registered, one campaign protocol must freeze the following items **before any outcome is viewed**.

| Protocol element | Required freeze |
|---|---|
| Assets | Eligible assets, start/end rules, eligibility at every decision date, substitutions, and delisting/missing-data treatment. |
| Source and snapshot | Provider, exact query, entitlement/right classification, raw and normalized hashes, adjustment policy, and data availability policy. |
| Candidates | Candidate family, semantic version, maximum candidate count, and any permitted parameter values. |
| Comparators | Equal-weight, trend-only, zero-cash stress, realistic cash/T-bill, market, and volatility-matched references with exact construction. |
| Trading assumptions | Calendar, decision and return timestamps, exposure limits, rebalancing, turnover, volume/participation, cost, and capacity rules. |
| Validation | Training/validation/lockbox dates, walk-forward folds, embargoes, regimes, statistical comparison family, and robustness scenarios. |
| Decision rules | Predeclared thresholds for benchmark-relative return, drawdown, turnover, capacity, stability, failed/inconclusive/promoted states, and explicit holdout eligibility. |

## Options Scope

The options data gate remains separately documented in [`PHASE14_OPTIONS_DATA_READINESS.md`](PHASE14_OPTIONS_DATA_READINESS.md). Historical OPRA-quality contract, quote, lifecycle, and Greeks-input data is **not a prerequisite** to the first daily-equity-only campaign. It becomes mandatory before any option affects a signal, portfolio allocation, execution, cost, lifecycle, benchmark, or performance claim.

## Evidence Boundary

This document is neither an investment conclusion nor a performance result. It does not validate H01, H04, another equity family, or an options family. It does not open a holdout or change paper/live execution state. The preserved v1 strategy result remains failed under its original protocol, and its holdout remains locked.

## References

[1]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/PROVISIONAL_EQUITY_FALSIFICATION.md "Quantum Trader Pro — Provisional Daily-Equity Falsification Result"
[2]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/PROVISIONAL_EQUITY_DIAGNOSTICS.md "Quantum Trader Pro — Provisional Daily-Equity Independent Diagnostics"
[3]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/research/governance/strategy_grade_policy_v1.json "Quantum Trader Pro — Frozen Strategy-Grade Policy"
