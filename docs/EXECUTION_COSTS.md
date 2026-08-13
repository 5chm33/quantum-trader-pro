# Execution, Liquidity, Cost, and Capacity Research Contracts

**Status:** Implemented as immutable **research estimates only**. This module does not create orders, submit broker requests, route to a venue, modify the paper-execution controls, or make live execution available.

Phase 10 provides a conservative, reproducible way to translate one hypothetical equity quantity and one point-in-time quote/volume receipt into either a transparent full/partial estimate or an explicit no-trade result. It exists to prevent later strategy experiments from silently assuming frictionless fills. It does **not** prove that the model matches realized trading costs, does not validate capacity, and produces no strategy-return result. The v1 holdout remains locked and the recorded failed v1 campaign remains unchanged.

## Research Motivation and Scope

Almgren and Chriss frame portfolio execution around trading costs and risk, explicitly distinguishing temporary and permanent impact in a stylized model.[1] Direct estimation work subsequently treats market impact coefficients as empirical quantities to be estimated rather than universal constants.[2] This project therefore keeps its temporary-impact coefficient in a versioned configuration and will require future campaigns to preregister and stress it; it does not fit the coefficient from campaign performance.

Liquidity is not a single field. The Federal Reserve Bank of New York explains that a bid-ask spread quantifies the cost of a limited-size trade, while price impact complements it for larger trading, and volume alone is a weak liquidity proxy.[3] The SEC similarly notes that quantity available at the bid or ask may be limited despite a narrow quoted spread.[4] The Phase 10 model consequently uses **all three distinct inputs**: bid/ask for a half-spread cost, retained available volume for a participation ceiling, and a declared temporary-impact term. It will fail closed instead of interpreting any absent component as a free fill.

## Point-in-Time Inputs

`EquityLiquiditySnapshot` is a checksum-bound observation. It keeps the instrument, observation and availability times, bid, ask, nonnegative available volume, source record identifier/hash, and source version. `ResearchTradeRequest` separately keeps the instrument, side, strictly positive requested quantity, decision cutoff, and request identifier. The pricing function never accepts an executable order object.

| Input or control | Required property | Fail-closed result |
|---|---|---|
| Bid / ask | Finite, strictly positive, and `ask ≥ bid`. | Invalid inputs are rejected at construction. |
| Volume | Nonnegative retained available volume. | Zero volume produces `no_trade` with `zero_available_volume`. |
| Timing | Both observed and available by the decision cutoff, with observed age within the declared maximum. | Future-available/future-observed data produces `unavailable_at_cutoff`; excessive age produces `stale_market_data`. |
| Instrument identity | Request/snapshot identities must match after canonical normalization. | Mismatch is rejected. |
| Source evidence | Valid source record ID, lowercase SHA-256 digest, and version. | Invalid provenance is rejected. |
| Participation control | `0 < max_participation_rate ≤ 1`. | The floor of `available volume × rate` sets maximum quantity; any remainder remains explicit. |

> **No theoretical completion:** When a request exceeds the participation cap, the result is a partial estimate with an explicit unfilled quantity. It does not assume that the remainder fills later, at the same price, or at any price.

## Cost Formula and Reconciliation

For a nonzero estimate, the module calculates the midpoint `m = (bid + ask) / 2`, half-spread `h = (ask − bid) / 2`, participation `p = filled quantity / available volume`, and declared temporary impact in basis points `I(p) = impact_bps_at_full_participation × p`. The side-specific research price is:

> `estimated buy price = m + h + m × I(p) / 10,000`
>
> `estimated sell price = m − h − m × I(p) / 10,000`

The positive all-in cost is separately reconciled as:

> `total cost = quantity × h + quantity × commission/share + quantity × fee/share + quantity × m × I(p) / 10,000`

`CostBreakdown` retains each component and rejects any inconsistent total. The module then calculates `total cost bps = total cost / (quantity × midpoint) × 10,000`. If the total exceeds the predeclared cost budget, it creates an explicit `no_trade` result rather than reducing the cost or claiming a fill.

| Output field | Meaning | Important limit |
|---|---|---|
| `estimated_execution_price` | Counterfactual price after half-spread and temporary impact. | It is not an observed trade price or broker instruction. |
| `CostBreakdown` | Exact half-spread, commission, fee, and temporary-impact components. | It omits venue routing, order-book dynamics, latency, adverse selection, taxes, borrow, and option multi-leg effects. |
| `estimated_filled_quantity` | Quantity at or below declared participation. | It is neither a broker fill nor a probability of fill. |
| `unfilled_quantity` | Remainder of a hypothetical request. | It is never carried as a deferred fill. |
| `status` | `full`, `partial`, or `no_trade`. | `no_trade` contains no price, costs, or participation rate. |
| `no_trade_reason` | Causal-data, volume, or budget failure code. | It is not an execution rejection from a broker. |

## Capacity Diagnostic

`assess_equity_capacity` reports a conservative maximum quantity and midpoint notional under the same time, participation, and baseline-cost controls. It starts with the base cost (half-spread plus commission and fee) in basis points. If that already exceeds the budget, capacity is zero. Otherwise it solves the maximum affordable participation implied by the declared linear impact coefficient, applies the participation cap, and floors resulting quantity to whole shares.

A capacity result is therefore a **model-bound diagnostic**, not a statement of deployable capital, liquidity at another time, market depth, future trading volume, or strategy scalability. Quote size and reported volume can understate or otherwise imperfectly capture depth, and actual impact depends on trade size, timing, venue, and counterparties.[3] Every future campaign must treat the model’s assumptions as stress-test inputs and report sensitivity results rather than rely on one configuration.

## Boundaries and Future Work

The current implementation deliberately accepts **equity-like single-instrument quote snapshots only**. It does not price options, create multi-leg fills, calculate permanent impact, net cross-order impact, infer a limit order book, estimate liquidity from closing bars, or relax the existing options defined-risk boundaries. Options execution requires chain-level bid/ask/depth evidence and leg-aware lifecycle/cost accounting in a later dedicated enhancement.

Before a performance conclusion can be made, a preregistered campaign must use point-in-time inputs, include all permanent baselines, model turnover and the declared cost configurations, sweep adverse costs/participation assumptions, test liquidity/capacity exclusions, apply dependence-aware inference, and pass walk-forward gates. This module changes none of those evidentiary requirements and enables no paper/live operation.

## Validation Performed

The deterministic test suite covers quote and source validation, full and partial estimates, buy/sell direction, exact component reconciliation, participation rounding, future/stale/zero-volume/cost-budget no-trade states, capacity budget/participation bounds, zero-impact handling, result coherence, and identity mismatches. The repository-wide quality gate must still pass before publication.

## References

[1]: https://www.quantitativebrokers.com/s/Optimal-Execution-of-Portfolio-Transaction-_-AlmgrenChriss-1999.pdf "Almgren and Chriss — Optimal Execution of Portfolio Transactions"
[2]: https://www.quantbeckman.com/api/v1/file/1ccd229d-0074-468d-b4bd-421075cdd4dc.pdf "Almgren et al. — Direct Estimation of Equity Market Impact"
[3]: https://www.newyorkfed.org/research/epr/03v09n3/0309flem/0309flem.html "Fleming — Measuring Treasury Market Liquidity"
[4]: https://www.sec.gov/files/access-to-capital-and-market-liquidity-study-dera-2017.pdf "SEC DERA — Access to Capital and Market Liquidity Study"
