# Dependence-Aware Inference and Robustness Diagnostics

**Status:** Implemented as immutable **research diagnostics only**. These contracts do not declare an alpha, select a candidate, promote a strategy, modify the immutable experiment ledger, open the locked v1 holdout, enable paper trading, or enable live trading.

Phase 11 supplies deterministic contracts for preserving causal return observations, describing serial dependence, creating reproducible circular moving-block bootstrap receipts for an explicitly complete candidate family, and retaining a complete set of preregistered robustness-scenario outcomes. It is deliberately designed to make later evidence harder to overstate, not to turn diagnostic statistics into an investment conclusion.

## Why Dependence and Selection Must Be Retained

The Sharpe ratio is an estimate built from unknown expected returns and volatility. Lo shows that serial correlation affects Sharpe-ratio interpretation and that naive time aggregation is valid only under special conditions.[1] The implementation therefore calls its annualized ratio **naive** and retains both return frequency and autocorrelation diagnostics. It never treats that ratio as an adjusted Sharpe, a confidence interval, or a validation result.

White’s Reality Check addresses repeated use of the same data across model comparisons, a setting in which reporting only the best observed result risks data snooping.[2] Bailey and coauthors likewise frame backtest overfitting as a material risk and describe a combinatorially symmetric cross-validation framework for quantifying it.[3] Accordingly, a Phase 11 comparison report represents an **explicit, complete candidate family against one named baseline**. A missing candidate, a changed baseline, a changed cutoff, or a changed bootstrap configuration cannot be represented as a valid report.

| Research risk | Phase 11 control | Explicit limitation |
|---|---|---|
| Serially dependent returns | Sample autocorrelations are retained by declared lag; bootstrap uses contiguous circular blocks. | This does not estimate the true data-generating process or validate a block length. |
| Naive annualization | `observations_per_year` and the resulting naive annualized Sharpe are retained. | It is not a serial-correlation-adjusted Sharpe or statistical test. |
| Candidate selection | Comparison family requires exact declared candidate coverage and one baseline. | It does not itself enforce ledger registration or a multiple-testing correction. |
| Hidden randomness | Bootstrap seed is a retained SHA-256 value; start positions derive deterministically from it. | Determinism is reproducibility, not statistical validity. |
| Selective stress reporting | Robustness report requires one base and every declared adverse scenario. | It does not decide which stress set is sufficient; Phase 12 must preregister it. |

## Causal Return Evidence

`ReturnObservation` retains a record ID, event time, availability time, finite return, and source digest. `ReturnSeries` requires at least two observations in strict chronological order with unique event times and record IDs. Every observation must have been available at or before the retained decision cutoff.

| Contract condition | Enforcement |
|---|---|
| Timestamp causality | Naive times are rejected; each observation’s event time cannot follow its availability time; availability after series cutoff is rejected. |
| Chronology | Event times must be strictly ascending and unique. |
| Provenance | Source digest must be a lowercase SHA-256 value. |
| Frequency | Observations per year is explicit and strictly positive. |
| Numerical safety | Every return must be a finite `Decimal`; non-finite values are rejected. |

> **Causal boundary:** The contract records the cutoff associated with the return evidence. It does not infer whether an upstream strategy used only information that was available before its trade decision; that remains the responsibility of the point-in-time data, forecast, allocation, and experiment-ledger layers.

## Serial-Dependence Diagnostics

`serial_dependence_diagnostics` returns the sample mean, sample volatility, naive annualized Sharpe (when volatility is nonzero), and autocorrelations through an explicit maximum lag. A zero-variance series retains a `zero_variance` status, no Sharpe value, and explicit `None` autocorrelations; the system never manufactures an infinite ratio.

The naive annualized Sharpe is computed as:

> `sample mean / sample volatility × √(observations per year)`

This formula is displayed solely as a reproducible descriptive calculation. It is not claimed to be valid under serial dependence, nonstationarity, skewness, changing leverage, nonlinear option payoffs, or costs.[1]

## Deterministic Circular Moving-Block Bootstrap

The supported `BlockBootstrapConfig` method is `circular_moving_block`. It requires a version, positive block length, positive replicate count, and SHA-256 seed. Candidate and baseline series must have the same decision cutoff, frequency, and **identical chronological event timestamps**. The method forms period-by-period candidate-minus-baseline excess returns, centers them by their observed mean, selects deterministic circular contiguous blocks, and records every block start for every replicate.

| Retained output | Purpose | What it is not |
|---|---|---|
| Observed mean excess return | Describes the aligned candidate-minus-baseline sample mean. | A performance conclusion. |
| Block start indices | Makes each deterministic resample reproducible and auditable. | Evidence that block length is economically optimal. |
| Centered sample mean per replicate | Retains each resampling output rather than only a tail count. | An IID bootstrap draw. |
| One-sided exceedance rate | Reconciles exactly to retained replicate means and observed excess. | A published p-value, a Reality Check result, or a multiple-testing-adjusted significance statement. |
| Exact candidate-family coverage | Prevents silently dropping candidates from a report. | A substitute for preregistration and ledger barriers. |

The circular construction wraps a block at the end of the finite sample. That feature preserves contiguous within-block ordering while yielding a fixed-length sample, but it remains a declared modeling choice requiring sensitivity analysis in a future preregistered campaign. The contract rejects a block length longer than the aligned sample, non-canonical replicate indices, missing replicates, out-of-range block starts, or an exceedance rate that fails to reconcile exactly to its receipts.

## Robustness-Scenario Retention

`RobustnessScenario` distinguishes one non-adverse `base` scenario from adverse cost-stress, parameter-perturbation, placebo, and regime scenarios. `robustness_diagnostic` requires a complete bijection between declared scenario IDs and point-in-time return series IDs. It retains every scenario outcome and verifies its mean directly against retained return observations.

| Requirement | Fail-closed behavior |
|---|---|
| Exactly one base scenario | Missing or multiple base scenarios are rejected. |
| Every non-base scenario adverse | A non-adverse stress scenario is rejected. |
| Scenario completeness | Missing, additional, or mismatched scenario series are rejected. |
| Shared cutoff | Any scenario series with a different cutoff is rejected. |
| Reconciled outcome | A stated scenario mean that differs from its return series is rejected. |

This is a completeness layer, not a robustness gate. Phase 12 must preregister actual scenario definitions, perturbation magnitudes, regime labels, acceptance criteria, and the candidate budget before campaign execution. Phase 15 will run the specified stresses. Neither phase can reinterpret the current diagnostic code as a demonstrated edge.

## Validation Performed

The deterministic test suite covers causal timing, chronology, unique receipt identities, finite values, zero variance, lag boundaries, bootstrap configuration/receipt reconciliation, fixed-seed reproducibility, candidate-family completeness, baseline identity, alignment/frequency mismatches, scenario completeness, adverse classification, cutoff consistency, and exact outcome reconciliation. The repository-wide quality gate must pass before this work is published.

## Evidence Boundary

No dataset was ingested, no candidate was registered or evaluated, no bootstrap result was computed on campaign data, no p-value or probability-of-backtest-overfitting estimate was claimed, and no strategy was promoted or rejected in Phase 11. The original v1 campaign’s failed result remains unchanged and the v1 evaluation holdout remains locked. Any future inference must flow through the immutable experiment ledger and a preregistered protocol.

## References

[1]: https://ideas.repec.org/a/taf/ufajxx/v58y2002i4p36-52.html "Lo — The Statistics of Sharpe Ratios"
[2]: https://www.ssc.wisc.edu/~bhansen/718/White2000.pdf "White — A Reality Check for Data Snooping"
[3]: https://escholarship.org/uc/item/4w1110bb "Bailey, Borwein, López de Prado, and Zhu — The Probability of Backtest Overfitting"
