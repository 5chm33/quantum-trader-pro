# Point-in-Time Forecast Signals and Permanent Baselines

**Status:** Implemented research contracts. These forecasts are **not trade instructions**, have not entered a preregistered performance campaign, and do not establish that any signal is profitable or investable.

The Phase 7 signal layer converts point-in-time market records into immutable, checksum-bound forecasts. It is designed to make causal errors, silent warm-up behavior, changing universes, and benchmark substitution visible before any candidate reaches the experiment ledger. It does not open the frozen v1 holdout, alter the preserved failed v1 result, activate paper trading, or make live execution available.

## Forecast Contract

Each `ForecastSignal` has an instrument identifier, a timezone-aware decision cutoff, a stable family identity, a version, a warm-up state, a forecast value only when warm, and a retained sequence of raw inputs. Every retained input records the source record identifier, input field, Decimal value, availability timestamp, and source-content SHA-256 digest. Construction fails if an input was unavailable at the decision cutoff, timestamps are naive, values are non-finite, a warm forecast lacks a value, or an unready forecast lacks a reason code.

| Contract element | Fail-closed requirement | Why it matters |
|---|---|---|
| `decision_cutoff_at` | Timezone-aware and normalized to UTC. | Establishes what the forecast could have known. |
| `raw_inputs` | Each input must be available no later than the cutoff and carries a source hash. | Preserves causal lineage and supports later artifact reconciliation. |
| `warm_up_complete` | A `False` state requires `forecast_value=None` and an explicit absence reason. | Prevents accidental zero-filling or trading an undefined statistic. |
| `forecast_value` | Decimal and finite whenever present. | Avoids binary-float drift and non-finite propagation. |
| `signal_version` | Stable, validated identifier. | Prevents specification drift from being hidden behind a family name. |
| `universe_id` and hash | Required together for cross-sectional forecasts. | Binds rankings to a frozen, reviewable eligible universe. |

The currently defined absence reasons are `no_data_at_cutoff`, `insufficient_history`, `insufficient_eligible_members`, and `source_forecast_absent`. An absence is data about a forecast’s eligibility, not a permission to impute a value or silently remove an instrument.

## Implemented Forecast Families

The code currently exposes three carefully separated modules. Their presence is an implementation milestone, not evidence of expected return.

| Family | Implementation | Retained controls | Research interpretation |
|---|---|---|---|
| H01 — time-series trend | A sign or continuous trailing total-return forecast from causally eligible bars. | Explicit lookback; at least `lookback + 1` bars; no future-available bar is eligible. | The design follows the time-series-momentum distinction described by Moskowitz, Ooi, and Pedersen, but no local replication conclusion has been made.[1] |
| H02 — cross-sectional momentum | Descending, tie-aware ranks of trailing returns over a complete `FrozenUniverse`. | The input mapping must contain every and only declared member; unavailable members remain explicit; the entire sleeve fails closed if eligible membership falls below its declared threshold. | Ranking a surviving subset would introduce a universe-selection channel, so data availability is retained rather than treated as a deletion rule. |
| H04 — volatility targeting | A transform of a source forecast using annualized sample volatility from causally eligible returns. | Target volatility, estimation window, minimum observations, annualization convention, minimum exposure, and maximum leverage are all explicit. | This is a bounded risk transform, not an alpha claim. Moreira and Muir document historical volatility-management evidence, but it must be tested separately from both trend and passive exposure.[2] |

> **Decision rule:** a volatility transform has a separate family identity and returns an explicitly unscaled source forecast with an explicit scaling-status code when observations are insufficient or volatility is zero. It never substitutes a hidden leverage assumption.

## Frozen Universes and Attribution

A `FrozenUniverse` holds a canonical sorted set of instruments, the timestamp at which the membership was available, and a content hash. Cross-sectional inputs must exactly match that membership; an extra ticker, a missing ticker, duplicate membership, or noncanonical order fails construction. This guard is intended to prevent accidental survivorship bias and selective loss of difficult-to-trade assets before later point-in-time membership and delisting data are available.

`SignalPortfolio` is a canonical ordered collection of forecasts at one decision cutoff. It retains one `FamilyAttribution` per represented family, with counts that reconcile to the signals and Decimal blend weights that sum exactly to one. The object does not optimize weights or emit orders. A later experiment must preregister any blend, its cost model, its fold plan, and its benchmark set in the immutable ledger.

## Permanent Baselines

Every later equity candidate comparison must retain all three baseline portfolios below. The methods return immutable `BaselinePortfolio` objects rather than a verbal label, preserving cutoff, frozen universe, readiness state, allocations, and, for trend, the exact source-signal versions.

| Permanent baseline | Construction | Purpose |
|---|---|---|
| `equal_weight_buy_and_hold` | Equal Decimal weight across every declared universe member. | Tests whether a candidate improves on a same-universe passive exposure rather than on cash or a cherry-picked benchmark. |
| `trend_only_unscaled` | Equal-gross signed exposure from H01 sign forecasts; unavailable H01 inputs make the baseline explicitly unready. | Separates the directional trend hypothesis from volatility scaling or a multi-sleeve blend. |
| `cash_zero_exposure` | Zero exposure for every declared universe member. | Reveals absolute exposure, turnover, and risk assumptions; it is not a substitute for the passive comparator. |

The frozen hypothesis catalog additionally requires volatility-scaled and leverage-matched passive comparators for any H01/H04 campaign. Those future comparators belong with portfolio construction and campaign preregistration because they require a declared portfolio-level volatility estimator and execution/cost convention; this Phase 7 contract does not pretend that an instrument-level scaling transformation is a completed benchmark campaign.[3]

## Validation Performed

The focused unit suite tests naive timestamps, inconsistent warm-up states, inputs unavailable at cutoff, insufficient history, no-lookahead behavior for delayed bars, deterministic replay, frozen-universe completeness, cross-sectional ranking, scaling caps, insufficient-history scaling, zero-volatility behavior, source-forecast absence, signal-family attribution, and exact baseline allocations. The complete repository quality gate remains the release criterion.

No historical performance calculation, parameter selection, data-snapshot acquisition, holdout read, broker action, or live-execution action is performed by this module. All such work remains governed by the immutable experiment ledger and the frozen strategy-grade policy.

## References

[1]: https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf "Moskowitz, Ooi, and Pedersen — Time Series Momentum"
[2]: https://www.nber.org/papers/w22208 "Moreira and Muir — Volatility Managed Portfolios"
[3]: STRATEGY_HYPOTHESES.md "Quantum Trader Pro — Strategy Hypothesis Evidence Matrix"
