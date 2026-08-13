# Preregistration, Walk-Forward, Regime, and Lockbox Protocol

**Status:** Implemented as immutable **protocol contracts only**. Phase 12 does not ingest campaign data, register a real candidate, run a backtest, retrieve any new or existing holdout bytes, publish a result, promote a strategy, or activate shadow, paper, or live execution.

The purpose of this layer is to make the next research campaign answerable before it begins. It freezes a bounded candidate budget, embargoed chronological walk-forward folds, predeclared regime windows, permanent baselines, cost/inference/benchmark/snapshot hashes, and a separate locked holdout receipt. The protocol is designed to bind to the existing immutable experiment ledger rather than replace it.

## Research Rationale

Repeatedly searching a historical dataset and reporting the best result can make a research process vulnerable to data snooping; White’s Reality Check is a foundational response to this multiple-comparison setting.[1] Backtest-overfitting work similarly emphasizes the danger that selected investment strategies can reflect selection effects rather than durable properties, motivating explicit out-of-sample structure and retained selection records.[2] The project therefore turns the candidate ceiling, partitions, comparators, and lockbox identity into hash-bound objects **before** protected attempts can be frozen.

The protocol does not claim that any one validation architecture is universally sufficient. Walk-forward windows, embargo lengths, regime definitions, candidate budgets, cost stress levels, and statistical acceptance thresholds are all research design choices that must be explicit, justified, and retained. They may not be changed silently in response to performance.

| Phase 12 component | Immutable control | What it does **not** do |
|---|---|---|
| Candidate budget | Canonical HNN family ceilings are hash-bound and checked against ledger candidate registrations. | It does not discover, score, or select a candidate. |
| Walk-forward plan | At least two chronological folds with train/validation/test windows and positive embargoes. | It does not retrieve data or calculate fold performance. |
| Regime plan | At least two non-overlapping, ex-ante labeled windows linked to a classifier-specification digest. | It does not infer a regime from later returns or define success. |
| Permanent baselines | Cash, equal-weight, and trend-only baselines are mandatory in canonical order. | It does not compare any returns. |
| New lockbox | A separate ledger `HoldoutSeal`, with bytes explicitly unretrieved and user approval required. | It does not approve, open, or access the lockbox. |
| Ledger freeze | Produces a `PreregistrationFreeze` compatible with the existing append-only ledger. | It does not append an event or authorize an attempt. |

## Candidate Budget

`CandidateBudgetPlan` contains a canonically ordered set of `CandidateFamilyBudget` entries. Every entry is a hypothesis-family identifier (`HNN`) and a positive ceiling. Before creating a ledger-compatible freeze, the plan verifies that the candidate is in the same campaign, is in a declared family, and has the exact declared ceiling.

| Invariant | Enforcement |
|---|---|
| Bounded exploration | Every family ceiling is a positive integer. |
| Exact family coverage | Candidate family must appear in the immutable plan. |
| No ceiling substitution | Candidate’s ledger ceiling must equal the plan’s family ceiling. |
| Canonical evidence | Family IDs must be unique and ascending before hashing. |
| Code identity | Candidate code commit must equal the protocol’s committed code identity. |

> **No adaptive expansion:** A new candidate slot, a larger ceiling, or a different code commit requires a new protocol identity and a new ledger-visible research decision. It cannot be retrofitted into a frozen plan.

## Embargoed Walk-Forward Plan

Each `WalkForwardFold` has six timezone-aware timestamps: train start/end, validation start/end, and test start/end, plus a strictly positive embargo. Train must finish at least one embargo before validation starts, and validation must finish at least one embargo before test starts. A plan requires at least two uniquely and canonically ordered folds; the prior test end may not overlap the next fold’s train start.

| Window | Intended use | Boundary |
|---|---|---|
| Train | Fit or construct only from prior information. | No validation, test, or lockbox data may be used. |
| Validation | Evaluate prespecified alternatives within the candidate budget. | It cannot be relabeled as a final test. |
| Test | Retain protected walk-forward evidence after selection rules are frozen. | It is not the separate new-campaign lockbox. |
| Embargo | A stated chronological separation around validation and test starts. | It is a time boundary, not proof that all leakage channels are absent. |

The implementation stores timestamps and hashes only. It does not define the bar frequency or a generic number of embargo bars; later campaign materials must specify that from the point-in-time data snapshot and information-lag policy. The project’s finance workflow separately requires an information censor gap and refuses a leaky historical evaluation when that gap cannot be verified.[3]

## Ex-Ante Regime Reporting

`RegimePlan` requires a classifier version, classifier-specification SHA-256 digest, and two or more non-overlapping `RegimeWindow` objects. Each plan permits each `RegimeLabel` only once, preventing repeated use of a label to isolate favorable fragments. Supported descriptive labels are calm, stressed, high/low volatility, and rising/falling rates.

A regime label is a reporting partition, not a return prediction or a portfolio-allocation command. The classifier specification digest is retained precisely so an eventual campaign can show how the label was determined without allowing a retrospective regime rule to be silently changed after seeing candidate returns.

## New Campaign Lockbox

The v1 evaluation holdout remains locked and untouched. A `NewCampaignLockbox` wraps the existing `HoldoutSeal` contract for a **separate** campaign. Its seal must have `bytes_retrieved = false`, and it requires explicit user approval. The plan retains only boundary and provider-query digests; it does not contain data bytes, data locations, access credentials, or a method to open a holdout.

| Lockbox condition | Fail-closed behavior |
|---|---|
| Separate campaign identity | Lockbox campaign must equal the preregistration campaign. |
| Unretrieved bytes | A seal that indicates retrieved bytes is rejected. |
| Explicit user approval required | A lockbox with this requirement disabled is rejected. |
| Existing holdout preservation | The protocol retains a policy digest; no v1 holdout reference is mutable or opened. |
| Candidate-bound receipt | Existing `HoldoutSeal` identity includes the selected candidate ID. |

This contract does **not** substitute for the ledger’s one-time `HoldoutApproval` flow. The existing ledger still requires a candidate-bound, expiring approval record before an opening can occur. Explicit approval will be requested only in Phase 17, if and only if promotion is earned under the frozen protocol.

## Ledger Binding

`CampaignPreregistrationPlan.freeze_candidate` validates campaign, code, and budget identity and then creates an existing `PreregistrationFreeze`. The output binds the protocol hash, snapshot manifest, combined walk-forward/regime partition hash, benchmark/cost hashes, and candidate-budget hash.

> **No execution authority:** A successful freeze is evidence that a design has been defined. It is not an attempt registration, comparison opening, test run, holdout approval, broker permission, shadow eligibility, or paper-trading authorization.

## Validation Performed

The deterministic test suite covers canonical protocol digests, candidate-budget binding, ledger-compatible freeze creation, positive embargoes, nonempty/ordered/non-overlapping folds, ordered/non-overlapping uniquely labeled regimes, sealed separate lockboxes, mandatory permanent baselines, campaign/code mismatch rejection, and identifiers/versions/hashes/commits/timestamps. The repository-wide quality gate must still pass before publication.

## Evidence Boundary

No Phase 12 campaign has yet been preregistered in the ledger, because there is no approved real point-in-time campaign snapshot, full benchmark/cost/inference configuration, or user-authorized new holdout receipt to bind. The work is preparatory infrastructure. It cannot be cited as trading performance, statistical significance, portfolio capacity, options validation, or live/paper readiness.

## References

[1]: https://www.ssc.wisc.edu/~bhansen/718/White2000.pdf "White — A Reality Check for Data Snooping"
[2]: https://escholarship.org/uc/item/4w1110bb "Bailey, Borwein, López de Prado, and Zhu — The Probability of Backtest Overfitting"
[3]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/RESEARCH_PROTOCOL.md "Quantum Trader Pro — Existing Research Protocol"
