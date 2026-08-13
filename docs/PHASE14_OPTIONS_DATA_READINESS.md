# Phase 14 Options Data-Readiness Decision

**Decision for options campaigns:** **BLOCKED — no options falsification attempt is registered or run.**

**Scope boundary:** This is not a prerequisite for the current daily-equity-only evidence path. Historical OPRA-quality data becomes mandatory only when an option affects a registered signal, portfolio allocation, execution, cost model, lifecycle rule, benchmark, or performance claim.

The repository has implemented defined-risk structures, Greeks input contracts, partial-fill accounting, and explicit exercise, assignment, expiry, and adjustment receipts. Those controls make an options campaign safer to evaluate; they do not create the historical market data required to evaluate one. Phase 14 therefore stops before candidate registration rather than backfilling prices, assuming theoretical fills, or treating current chains as historical evidence.

## Required Admission Evidence

| Requirement | Required retained evidence | Phase 14 status |
|---|---|---|
| Contract identity and lifecycle | Historical point-in-time contract definitions, multiplier/deliverable changes, adjustments, exercise/assignment status, and expirations. | **Unavailable.** No qualified historical option security master is connected. |
| Executable price evidence | Historical NBBO/bid/ask, quotes, trades, sizes, timestamps, and venue/feed identity at every decision/fill point. | **Unavailable.** The connected service exposes historical bars/trades by supplied contract symbol, but the available tool inventory has no historical option-quote/NBBO route. |
| Model inputs | Underlying, rate/dividend, strike, expiration, model convention, and timestamps sufficient to calculate and retain Greeks/IV transparently. | **Unavailable.** No point-in-time joint snapshot has been admitted. |
| Underlying actions | Time-stamped dividends, splits, mergers, symbol changes, and contract adjustment mapping. | **Unavailable.** A required reconciled contract-adjustment history is not frozen. |
| Liquidity/capacity | Spread, quote size, volume, participation, partial-fill, and multi-leg timing data matching the declared execution model. | **Unavailable.** Option bars alone cannot establish executable spreads or multi-leg fill feasibility. |
| Preregistered protocol and new lockbox | Candidate budget, selected structure family, snapshot, costs, inference plan, and a distinct unretrieved holdout query bound in the ledger. | **Not created.** It cannot be truthfully bound without the preceding inputs. |

## Connected-Service Assessment

The available connected service was inspected through read-only tool metadata only; no account configuration was changed, no order was placed, and no option market-data request was made. Its exposed historical option-bars operation requires known contract symbols and returns OHLCV aggregates. Its historical trade operation likewise requires supplied contract symbols. The currently exposed contract search is oriented to active/inactive contracts and does not expose an `asof` parameter for historical chain reconstruction. No historical option-quote operation is exposed in the connected tool inventory.

That combination is insufficient for an A+ options campaign. It cannot reconstruct a contemporaneous selectable chain, prove bid/ask-based entry and exit assumptions, track contract adjustments across a historical universe, or determine whether a multi-leg structure was executable as modeled.

> **Prohibited substitute:** No theoretical mid-price, end-of-day close, current-chain lookup, or silently inferred contract mapping may substitute for the missing historical quote and lifecycle evidence. The frozen strategy policy expressly prohibits silent theoretical-fill substitution and requires real point-in-time contract data.[1]

## Data Capability Research

A qualified provider must cover more than bars. Public provider descriptions show the kind of data that would be necessary, subject to licensing and a separately reviewed integration:

| Provider capability described publicly | Relevance | Why it is not yet campaign evidence |
|---|---|---|
| Databento describes historical OPRA coverage since 2013 with consolidated last sale and national BBO, point-in-time instrument definitions, and OHLCV/reference schemas.[2] | Potentially supports historical contract, NBBO, and definition retention. | It is not connected or licensed in this project, and it reports that it does not publish pre-calculated IV/Greeks; the project would need a frozen model-input plan. |
| Massive describes historical US options data, contract/reference data, corporate actions, trades, and quotes at plan-dependent coverage levels.[3] | Potentially supports data acquisition after coverage and licensing review. | It is not connected or admitted. Any historical range, entitlement, corporate-action mapping, and redistribution condition must be verified. |
| Cboe’s public historical page offers volume statistics and directs detailed historical data to DataShop.[4] | Useful context for public aggregate volume, not for execution simulation. | Aggregate volume lacks per-contract NBBO, selectable chain history, lifecycle detail, and multi-leg fill evidence. |

## Unblock Criteria

Before any defined-risk option candidate is registered, a chosen provider integration must be reviewed and demonstrate all fields below in a provider-neutral snapshot. The snapshot must be hash-bound, rights-classified, and constructed without retrieving the new-campaign lockbox bytes.

| Component | Minimum acceptance condition |
|---|---|
| Option security master | Point-in-time listing/expiration/strike/right/style/multiplier/deliverables and adjustment lineage for every contract. |
| Quotes and trades | Historical NBBO or venue-specific bid/ask, sizes, trades, timestamps, and feed/venue identity at declared sampling times. |
| Underlying and actions | Split/dividend/corporate-action records plus option-adjustment mapping available by decision time. |
| Greeks and IV inputs | Retained model specification and all contemporaneous inputs; vendor values, if used, must retain model/convention provenance. |
| Strategy structures | Defined-risk or fully collateralized structures only, with each leg’s synchronized fill/no-fill rules and partial-fill handling. |
| Cost and capacity | Explicit spread, fees, impact, participation, borrow/collateral, and capacity rules calibrated only from the admitted data. |
| Governance | Candidate budget, walk-forward partitions, regimes, permanent baselines, inference plan, and a separate sealed holdout bound to the immutable ledger. |

## Evidence Boundary

This is a **data-readiness rejection**, not a rejection of covered calls, cash-secured puts, vertical spreads, volatility-risk-premium hypotheses, or other options families. It does not calculate returns, construct an option position, open a holdout, activate paper trading, or authorize live execution. The v1 equity result remains unchanged and the v1 holdout remains locked.

## References

[1]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/research/governance/strategy_grade_policy_v1.json "Quantum Trader Pro — Frozen Strategy-Grade Policy"
[2]: https://databento.com/options "Databento — Options Data"
[3]: https://massive.com/options "Massive — Options Data"
[4]: https://www.cboe.com/us/options/market_statistics/historical_data/ "Cboe — Historical Options Data"
