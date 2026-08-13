# Equity and Options Research Approaches

**Status:** Decision record for the A+ strategy campaign

The A+ campaign needs two distinct capabilities: an auditable research process and, only after a candidate survives untouched tests, an eventual shadow/paper operating path. No provider or host can turn an unvalidated signal into an edge. The immediate work can remain provider-neutral while data schemas, the experiment ledger, forecasts, options lifecycle, portfolio risk, and acceptance gates are built.

## Historical Research and Data

| Approach | Tradeoffs | Cost | Setup Complexity |
|---|---|---:|---|
| **Local licensed OPRA pipeline** | Highest control over point-in-time quotes, instrument definitions, timestamps, chain construction, and retained evidence. Databento states that OPRA coverage includes consolidated last sale, exchange BBO, NBBO, point-in-time definitions, and history since 2013. It does not supply pre-calculated Greeks, so the repository must retain the exact rate, dividend, volatility, and pricing-model inputs used to derive them.[1] | Historical OPRA remains pay-as-you-go; the official Standard live plan is **$199/month**. Actual historical campaign cost depends on symbols, dates, schema, and resolution and must be priced with the provider’s cost-estimation endpoint before download.[2] | **High.** Requires licensing, storage planning, normalization, corporate-action/reference joins, options lifecycle logic, derived analytics, and independent replay infrastructure. |
| **Managed options research platform** | Fastest route to long-history minute options research. QuantConnect documents AlgoSeek US equity options from January 2012 across roughly 4,000 symbols, with trades, quotes, open interest, contract universes, and dependencies for underlying equities, security master, splits, dividends, and symbol changes. The platform also exposes option-chain history and derived Greeks/IV. The tradeoff is dependence on platform data semantics, compute limits, and a second implementation unless results are exported into this repository’s evidence format.[3] | QuantConnect states that its free plan includes options data and unlimited backtesting; paid research, compute, collaboration, storage, API access, and live nodes are configurable additions. Exact cost depends on the selected resources and must be confirmed in the account before purchase.[4] | **Medium.** Less raw-data engineering, but requires LEAN integration, evidence export, model-parity tests, and protection against platform-specific assumptions. |
| **Lighter equity-first campaign with deferred empirical options grading** | Uses the existing total-return equity/ETF pipeline to test diversified forecasts, portfolio construction, inference, costs, and capacity now. Options instruments, Greeks, assignment, expiry, and multi-leg accounting can be built and unit-tested, but **no options strategy grade** is issued until real point-in-time options data is obtained. Alpaca’s historical options API starts only in February 2024; its free indicative feed is derived rather than actual OPRA, so it is useful for integration and recent shadow checks, not a long-regime A+ options claim.[5] | **Lowest initial cost.** Existing equity data and deterministic fixtures can support development; later options-data cost is deferred. | **Low to medium.** Fastest non-blocking start, but empirical options validation remains explicitly incomplete. |

No data approach is silently selected by this document. The repository will first implement provider-neutral contracts and immutable experiment records. Before any paid download, the campaign will record the provider, symbols, period, schemas, expected bytes, quoted cost, license boundary, and whether the data can be retained or only checksummed.

## Eventual Shadow and Paper Operation

| Approach | Tradeoffs | Cost | Setup Complexity |
|---|---|---:|---|
| **Existing hardened cloud computer** | Preserves the repository’s exact adapters, durable journals, reconciliation, one-use approvals, and failure-injection evidence. It provides the strongest continuity between research artifacts and later shadow/paper behavior, but the owner is responsible for OS updates, service monitoring, backups, broker/data credentials, and incident response. | Existing cloud-computer cost plus provider data and brokerage fees. No new hosting purchase is required for development. | **Medium to high.** The service framework exists, but authenticated shadow, options state reconciliation, alerts, and service-manager recovery still require acceptance. |
| **Managed research/trading platform** | Can reduce market-data and brokerage integration work and may provide managed nodes, notifications, and brokerage adapters. It introduces platform dependency and requires model-parity evidence so fills, assignments, exercise, fees, and position state match the public research implementation.[4] | Free research entry point exists; live nodes, data, and compute may require paid resources. Exact account pricing must be confirmed before enabling. | **Medium.** Less infrastructure ownership but meaningful migration, parity, and audit work. |
| **Hybrid: managed data/research plus repository-controlled cloud operation** | Uses a managed platform for historical options access while preserving the public repository as the canonical experiment ledger, model implementation, and eventual operator boundary. This offers broad data plus strong ownership, but every promoted result must pass cross-engine parity tests and therefore has the greatest integration surface. | Data/platform costs plus the existing cloud computer. | **High.** Two engines, two data representations, evidence export, and exact parity checks are required. |

## Decision Boundary

The campaign can proceed immediately with the provider-neutral work shared by all three approaches: immutable experiments, point-in-time schemas, permanent baselines, diversified forecasts, options lifecycle/accounting, risk allocation, endogenous costs, capacity, and dependence-aware inference. An empirical options campaign must not begin until one of the three data approaches is explicitly chosen and its license, coverage, cost, and retention rules are recorded.

The eventual operating approach remains independent from the research-data choice. A strategy may reach pre-holdout promotion without an operating provider, but it cannot receive the final A+ strategy grade before authenticated shadow and paper evidence. Real-money execution remains outside this campaign.

## References

[1]: https://databento.com/options "Databento Options Data"
[2]: https://databento.com/blog/introducing-new-opra-pricing-plans "Databento OPRA Pricing Plans"
[3]: https://www.quantconnect.com/docs/v2/writing-algorithms/datasets/algoseek/us-equity-options "QuantConnect AlgoSeek US Equity Options Dataset"
[4]: https://www.quantconnect.com/pricing/ "QuantConnect Pricing and Features"
[5]: https://docs.alpaca.markets/us/docs/historical-option-data "Alpaca Historical Option Data"
