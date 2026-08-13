# Constraint-First Portfolio Construction and Factor-Aware Allocation

**Status:** Implemented research-domain construction contracts. This module creates **research target exposures only**. It does not estimate tradable prices, select parameters from performance, submit orders, enable paper trading, or authorize live execution.

Phase 9 converts the retained Phase 7 forecasts into a deterministic, audit-ready allocation object. Its design starts with declared risk constraints—not an optimizer searching historical returns—and keeps enough attribution to distinguish a forecast from its eventual constrained exposure. The locked v1 equity holdout remains sealed and the existing failed campaign is unchanged. No return, Sharpe ratio, alpha, or diversification claim is created by this code.

## Why Factor Exposure Is Explicit

A portfolio can appear diversified by constituent count while remaining concentrated in a small number of underlying sources of risk. Roncalli and Weisang illustrate that equal risk contribution across investment assets need not produce balanced risk contributions across primary risks or factors.[1] The construction model therefore reports linear factor exposure separately from instrument weight, retains the input loading receipts, and treats missing or stale loading data as a failure to construct rather than as zero factor exposure.

The reference literature also cautions against treating one construction method as persistently superior to the equal-weight baseline. Bessler, Taushanov, and Wolff use an out-of-sample comparison of multiple allocation methods and retain 1/N as a benchmark; their discussion also emphasizes the practical relevance of turnover and transaction costs.[2] This project preserves equal-weight, unscaled-trend, and cash baselines from Phase 7. It does **not** claim that factor-aware construction has improved them.

## Inputs and Causal Boundary

`construct_factor_aware_portfolio` accepts an immutable `SignalPortfolio`, an `AllocationConfig`, and zero or more `FactorLoading` receipts. Forecasts must already be warm and available at the common decision cutoff. A factor-aware configuration additionally requires exactly one loading for every `(frozen-universe instrument, constrained factor)` pair.

| Input | Required retained evidence | Fail-closed condition |
|---|---|---|
| Forecast | Family, checksum-bound raw inputs, decision cutoff, forecast value, warm-up status, family blend weight. | Any incomplete signal returns an all-cash, unready portfolio with `incomplete_forecasts`. |
| Frozen universe | Universe identifier, hash, canonical instrument order, availability timestamp. | Construction never adds or removes members to obtain a more favorable allocation. |
| Factor loading | Instrument, factor, as-of time, availability time, finite value, source record ID/hash, and model version. | Missing pair returns `missing_factor_loading`; a future-dated or too-old receipt returns `stale_factor_loading`. |
| Configuration | Version, per-instrument cap, gross cap, net cap, factor-loading maximum age, optional family caps, optional factor caps. | Invalid, duplicated, unordered, or infeasible controls are rejected before allocation. |

> **No silent zeroes:** a missing or stale factor loading is not interpreted as a zero loading. The result is all cash, explicitly marked unready, and names the incomplete input keys.

## Deterministic Constraint Sequence

The allocation sequence is fixed and visible. It makes no covariance, expected-return, or optimized-risk estimate; dependence-aware inference and richer risk estimation are reserved for later phases.

| Step | Calculation | Attribution retained |
|---|---|---|
| 1. Blend forecasts | `forecast value × preregistered family blend weight` for every family and instrument. | Original forecast contribution and provisional instrument exposure. |
| 2. Family gross caps | Scale only a family when its cross-universe absolute exposure exceeds its declared cap. | Constrained family contribution and `family_gross_cap` label on affected targets. |
| 3. Instrument cap | Clip each instrument to the declared absolute cap. | `instrument_cap` label only where clipping occurred. |
| 4. Gross and net caps | Proportionally scale the current vector if gross or absolute net exceeds its declared cap; a zero net cap returns all-zero targets. | Portfolio-level `gross_cap` or `net_cap` label. |
| 5. Factor caps | Calculate linear factor exposures from retained loadings; proportionally scale the whole current vector to the tightest breached factor cap. | Pre- and post-cap factor exposures, all loading receipts, and `factor_cap` label. |
| 6. Cash residual | Set `cash_residual = 1 − gross exposure`; a negative value denotes a research leverage requirement rather than available broker buying power. | Exact gross/net/cash reconciliation. |

This is a deliberately conservative construction layer. Factor exposure is a linear diagnostic and constraint; it is **not** a model of factor risk contribution, covariance, liquidity, margin, scenario loss, or execution capacity. Those require the later dependence, execution, and cost phases.

## Defined Contract Invariants

The model enforces canonical ordering and arithmetic reconciliation at every level. `TargetAllocation` retains each family contribution, its provisional aggregate, its constrained final target, and only the local constraints that bound that instrument. `PortfolioFactorExposure` retains the provisional and final linear exposure, declared cap, and all selected point-in-time loadings. `ConstructedPortfolio` verifies its gross, net, and cash residual exactly against the final targets.

| Invariant | Enforced behavior |
|---|---|
| No implicit factor universe | Factor limits must be unique and canonically ordered; every declared factor needs all frozen-universe loadings. |
| No retrospective factor records | `as_of_at` and `available_at` must be no later than the decision cutoff and within the declared maximum age. |
| No invisible constraint | Every cap that binds appears in target or portfolio attribution. |
| No unknown coverage | Unrecognized extra loadings are ignored as non-required evidence; they cannot alter a declared factor’s value. Duplicate required `(instrument, factor)` receipts are rejected. |
| No artificial readiness | Unready output must be all cash, contain no factor exposure report, retain an explicit absence reason, and name the missing or stale inputs. |
| No parameter fitting | Family blend weights and every cap arrive in a versioned configuration; the function has no historical performance input. |

## Validation Performed

The unit suite verifies deterministic replay, family attribution, family/instrument/gross/net/factor caps, zero-net behavior, future-dated and stale receipts, missing loadings, duplicate receipts, incomplete forecasts, configuration ordering/uniqueness, source-digest validation, and result arithmetic/all-cash invariants. The full repository quality gate remains required for acceptance.

The source literature motivates monitoring underlying risk exposures, but it does not validate this project’s allocation rule. A future research campaign must preregister the configuration and candidate budget, use point-in-time total-return data, include the permanent baselines, model turnover/costs/liquidity/capacity, apply dependence-aware inference, and pass walk-forward and holdout gates before any performance conclusion is warranted.

## References

[1]: https://mpra.ub.uni-muenchen.de/44017/1/risk-factor-parity.pdf "Roncalli and Weisang — Risk Parity Portfolios with Risk Factors"
[2]: https://pmc.ncbi.nlm.nih.gov/articles/PMC8164058/ "Bessler, Taushanov, and Wolff — Factor Investing and Asset Allocation Strategies"
