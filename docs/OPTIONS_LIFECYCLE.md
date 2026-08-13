# Options Instruments, Lifecycle, Greeks, and Multi-Leg Accounting

**Status:** Implemented research-domain contracts. This layer does **not** price a tradable option chain, create a performance result, route an order, enable paper trading, or make live trading available.

Phase 8 adds explicit options instrument, valuation-input, lifecycle, and multi-leg accounting contracts. Its purpose is to prevent a future options campaign from obscuring a deliverable change, early assignment, residual spread leg, partial fill, or collateral obligation behind a net-premium number. The frozen v1 equity holdout is unchanged and unopened; all options hypotheses remain unvalidated until a later campaign uses licensed point-in-time contract data, quotes, rates, dividend inputs, multi-leg costs, and the immutable experiment ledger.

## Contract Identity and Deliverables

An `OptionContract` binds a provider-normalized option security identity to its underlying identity, OCC-style symbol, root, right, strike, expiry, last-trade timestamp, multiplier, exercise style, settlement type, currency, status, and explicit deliverables. The standard 100-share multiplier is not hard-coded as a universal rule. OCC states that standard equity options normally represent 100 shares, while corporate actions can produce adjusted contracts with a different deliverable.[1] The model therefore retains a `deliverable_version`, a list of deliverables, and an immutable `OptionContractAdjustment` receipt connected to an adjusted successor contract.

| Contract control | Enforcement | Rationale |
|---|---|---|
| Underlying identity | Must be an equity, ETF, or index; option security must use `AssetClass.OPTION`. | Avoids a string-only ticker relationship. |
| Expiry and listing time | Timezone-aware; last trade cannot follow expiry. | Stops impossible time ordering before a lifecycle event exists. |
| Strike, multiplier, and deliverables | Positive finite Decimals; physical settlement requires a security deliverable; cash settlement requires a cash deliverable. | Makes contractual obligations computable rather than assumed. |
| Contract adjustment | A receipt must name the original contract, an `ADJUSTED` successor contract, effective and availability timestamps, and an OCC-memo digest. | Keeps multiplier/deliverable changes reviewable and causal. |
| Settlement currency | Must match the option security currency. | Prevents an unannounced currency basis change. |

> **Adjustment boundary:** an adjustment receipt is retained as a lifecycle event. It is not a silent rewrite of historical fills, premiums, or contract identity. A successor strategy specification must explicitly use the adjusted contract before any future research attempt can proceed.

## Independent Greeks Inputs

`BlackScholesInputs` retains a calculation timestamp, observed option price, spot, risk-free rate, dividend yield, implied volatility, time to expiration, a model version, and four distinct hashed source receipts: option quote, underlying, rate, and dividend. Each source must have been available no later than the calculation timestamp. The code calculates Black–Scholes–Merton price, delta, gamma, vega, daily theta, and rho; it retains a price residual against the observed option price.

The implementation uses a European-exercise pricing formula even for a retained American-style contract, and emits `european_exercise_assumption` for any non-European contract. This limitation is deliberate: FINRA and OIC both emphasize that American-style holders may exercise before expiration and writers may be assigned while a position remains open.[2] [3] The calculated Greeks are therefore **diagnostic model evidence**, not vendor-Greeks substitution, executable pricing, early-assignment probability, or a claim that an American option has been fully valued.

## Defined-Risk Initial Structures

The initial structure set exactly matches the frozen policy. The model does not contain a generic short-option constructor; a candidate must declare one of the restricted structures and satisfy its collateral or long-leg condition before a position can be initialized.

| Allowed initial structure | Required invariant | Explicitly rejected exposure |
|---|---|---|
| Long call or long put | Exactly one long leg with no declared coverage/collateral. | Short option by relabeling a single-leg strategy. |
| Vertical debit or credit spread | Exactly one long and one short leg, same underlying/expiry/right/count, distinct strikes, direction consistent with debit/credit type. | Ratio spreads, mixed expiries, mixed rights, unmatched legs, and standalone collateral substitution. |
| Covered call | One short call and retained underlying shares at least equal to the current short-call deliverable. | Naked calls and reduction of coverage while a short call remains open. |
| Cash-secured put | One short put and cash collateral at least equal to strike × multiplier × contracts. | Naked puts and partial short fills above retained collateral. |

This restriction follows the project’s frozen policy and reflects the SEC’s warning that writers can face losses beyond the premium received; it is a risk-control boundary, not a claim that collateral makes a strategy safe or profitable.[4]

## Multi-Leg Fills and Lifecycle Events

An `OptionStrategyPosition` keeps every leg’s open contract count, the ordered observed fill receipts, premiums, fees, lifecycle cash flow, underlying shares, and event receipts. It starts in `NEW`, moves through `PARTIALLY_OPEN`, `OPEN`, `PARTIALLY_CLOSED`, and `CLOSED`, and fails before a partial vertical short fill would exceed its open long protection. No theoretical fill type exists in the model.

| Event | Preconditions | Recorded accounting effect |
|---|---|---|
| Fill | Action must match leg side and stay within predeclared quantity; live short coverage is checked after the fill. | Premium cash flow uses the contract multiplier; fee remains separate; fill ID is immutable. |
| Long exercise | An open long leg, explicit spot, valid physical or cash settlement. | Resolves contracts and records strike cash plus delivered/acquired underlying shares, or cash intrinsic value. |
| Short assignment | An open short leg and explicit spot. | Resolves contracts and records the writer’s strike cash and share delivery/acquisition. FINRA notes a short option can be assigned while open, including within a multi-leg strategy.[2] |
| Worthless expiry | Event is on/after contract expiry, explicit spot, and zero intrinsic value. | Resolves the leg with zero settlement cash; in-the-money contracts cannot silently expire worthless. |
| Contract adjustment | Adjustment receipt matches the affected leg contract, quantity, effective time, and availability time. | Retains the receipt without changing historical contract or fill records. |

OCC specifies that standard equity-option exercise or assignment results in acquisition or delivery of the underlying shares, and describes T+1 exercise settlement.[1] The present accounting model retains the contractual event and cash/share consequences but does not simulate settlement-fail mechanics, broker cut-offs, tax lots, financing, corporate-action processing after an adjustment, or an assignment probability model. Those are later execution, cost, and operational-acceptance requirements.

## Validation Performed

The dedicated unit suite covers standard and adjusted contract construction, deliverable validation, causal valuation inputs, call and put Greeks, American-style model limitation flags, all permitted initial structures, naked/unbounded shape rejection, vertical partial-fill sequencing, premium and fee reconciliation, covered-call delivery on assignment, cash-secured-put assignment, long exercise, worthless expiry, in-the-money expiry rejection, cash settlement, unknown-settlement rejection, and adjustment receipt idempotence/mismatch controls. The complete repository quality gate remains the acceptance criterion.

No statement in this document should be read as a recommendation to trade options. A complete options evidence campaign still requires point-in-time chain data and quotes, contract adjustments, rates/dividends/borrow, multileg spread/fee/impact models, capacity controls, tail and expected-shortfall gates, preregistration, walk-forward validation, and eventual paper-operation acceptance.

## References

[1]: https://www.theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications "OCC — Equity Options Product Specifications"
[2]: https://www.finra.org/investors/insights/trading-options-understanding-assignment "FINRA — Trading Options: Understanding Assignment"
[3]: https://www.optionseducation.org/optionsoverview/exercising-options "Options Industry Council — Exercising Options"
[4]: https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-63 "SEC Investor.gov — An Introduction to Options"
