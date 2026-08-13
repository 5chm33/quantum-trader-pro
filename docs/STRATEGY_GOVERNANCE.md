# Strategy Research Governance

**Status:** Frozen governance for the `research/a-plus-strategy-v1` campaign

**Baseline:** `25c7ba800360d5a235312496dc52c8df2941eec4`
**Objective:** Seek an evidence-based A+ strategy grade without optimizing to a known test result or enabling real-money execution.

> **A+ engineering does not imply A+ trading performance.** Strategy grades are earned only through preregistered, cost-aware, out-of-sample evidence. Complexity, feature count, options support, or a high in-sample return cannot substitute for the hard gates below.

## Frozen Starting Point

The v0.2.0 engineering platform remains the immutable baseline. Its existing moving-average campaign failed promotion, and its final 252 observations per asset remain locked.[1] No new strategy may inspect, tune against, or indirectly infer those observations. New research will use distinct development periods and a separately preregistered lockbox.

| Baseline evidence | Frozen identity |
|---|---|
| Public main | `25c7ba800360d5a235312496dc52c8df2941eec4` |
| v0.2.0 tag | `1f6d993136573a66514485b7a67bfcba05b04288` |
| Accepted cloud runtime | `b60e311c53ff0111a2b2ade22d8d96c51e61042f` |
| v1 protocol SHA-256 | `becf6ad29b0649c7f97c76fb149d41eeb6bac92760f1ff616e4772a596d1acb5` |
| v1 result | Failed pre-holdout promotion |
| v1 holdout | Locked and unopened |

## Research States

A candidate advances through explicit states. State changes are append-only experiment records, not edits to prior results.

| State | Meaning | Permitted next action |
|---|---|---|
| `hypothesis` | Economic mechanism and falsifiable prediction documented | Implement features and permanent baselines |
| `development` | Candidate uses train/validation data only | Run bounded candidate budget |
| `test_eligible` | Candidate selection frozen before untouched test folds | Execute preregistered test folds once |
| `holdout_eligible` | Every pre-holdout hard gate passes | Request explicit approval to open one lockbox |
| `shadow_eligible` | Locked holdout passes without rule changes | Run live-data simulated orders |
| `paper_eligible` | Shadow acceptance passes | Run authenticated paper trading under default pause |
| `strategy_a_plus` | Quantitative, robustness, capacity, options, and paper gates all pass | Publish evidence; live remains a separate decision |
| `rejected` | Any mandatory gate fails | Retain evidence; start a new preregistered hypothesis |

The state machine is now implemented by the hardened, append-only [`EXPERIMENT_LEDGER.md`](EXPERIMENT_LEDGER.md). It binds campaigns, bounded candidates, frozen preregistrations, complete terminal attempt sets, retained artifacts, comparison barriers, candidate-bound lockboxes, one-use approvals, and retrieved snapshot identities. It is an integrity mechanism for future research; **no new candidate result, strategy grade, paper session, or live order has been created by that implementation**.

## A+ Grade Policy

A weighted score of at least **95/100** is necessary but not sufficient. Every hard gate must also pass. Before an untouched holdout, the maximum provisional strategy grade is **A-**. Before authenticated shadow and paper evidence, the maximum final strategy grade is **A**. A software implementation can retain its A+ engineering grade even when every strategy candidate is rejected.

| Dimension | Weight | A+ evidence standard |
|---|---:|---|
| Economic hypothesis and falsifiability | 10% | Mechanism, prediction, failure condition, and permanent baseline defined before testing |
| Point-in-time data integrity | 10% | Versioned total-return, reference, options-chain, corporate-action, and calendar inputs with provenance |
| Net out-of-sample performance | 20% | Positive risk-matched excess return after preregistered costs across untouched folds and lockbox |
| Statistical credibility | 10% | Dependence-aware uncertainty, multiple-testing adjustment, and concentration diagnostics |
| Drawdown and tail behavior | 10% | Preregistered drawdown, expected-shortfall, gap, and stress limits pass |
| Asset and regime stability | 10% | No single asset, regime, start date, or small trade subset explains the result |
| Costs, liquidity, turnover, and capacity | 10% | Net edge survives spread, impact, participation, and capital scaling scenarios |
| Options lifecycle and risk | 10% | Greeks, contract adjustments, early exercise, assignment, expiry, multi-leg fills, and bounded loss reconcile |
| Shadow and paper acceptance | 10% | Live-data shadow plus authenticated paper sessions reconcile without duplicate, stale, or unexplained state |

## Mandatory Hard Gates

The exact numeric thresholds will be frozen in the campaign protocol **before** its protected test and lockbox data are used. The following categories cannot be removed or weakened after observing results:

| Gate | Non-negotiable rule |
|---|---|
| Candidate budget | Every hypothesis, parameter set, universe, cost model, and result receives an immutable experiment ID in the append-only experiment ledger |
| Data boundary | Real point-in-time data is required for evidence; generated data is restricted to unit and failure tests |
| Benchmarking | Total-return and risk-matched permanent baselines appear in every report |
| Selection | Training and validation select candidates; untouched test folds and lockbox never select parameters |
| Multiple testing | Attempt count and selection process are retained and reflected in statistical inference |
| Costs | Spread, fees, impact, participation, borrow where applicable, and options multi-leg execution are explicit |
| Capacity | Net performance is reported across increasing capital and participation constraints |
| Concentration | Asset, regime, factor, and trade-contribution concentration are disclosed |
| Robustness | Start dates, parameter neighborhoods, costs, delayed execution, missing data, and placebos are tested |
| Risk | Drawdown, expected shortfall, gap loss, leverage, liquidity, Greeks, assignment, and expiry limits are enforced |
| Reproducibility | Independent reruns reproduce campaign artifacts or differences are explained and versioned |
| Promotion | Any failed mandatory gate rejects the candidate; no narrative override is allowed |

## Options Research Boundary

Options support is a separate evidence track, not a method for manufacturing leverage around a weak underlying signal. Performance analysis requires real historical contract data with point-in-time bid, ask, volume, open interest, contract specifications, underlying prices, corporate actions, rates, dividends, and timestamps. Models may calculate Greeks and theoretical values, but theoretical marks cannot be substituted silently for executable prices.

The first campaign is restricted to **defined-risk or fully collateralized structures**: long calls and puts, vertical debit or credit spreads, covered calls, and cash-secured puts. Naked short options, unbounded-loss structures, 0DTE automation, unsupported early-exercise assumptions, and live multi-leg execution remain prohibited. Assignment, exercise, expiry, multiplier changes, deliverable changes, and multi-leg partial fills must reconcile before an options result can be promoted.

## Holdout and Anti-Contamination Rules

The existing v1 lockbox remains sealed. The new campaign will define a separate lockbox after data coverage and protocol feasibility are known. Its bytes or provider query range may not be retrieved during development. The implemented experiment ledger permits exactly one candidate-bound sealed lockbox, records its provider-query and boundary hashes before retrieval, requires all pre-holdout gates plus an immutable one-use approval and explicit user approval in the conversation, and binds the later attempt to the retrieved snapshot manifest. A failed holdout is published and may not be reused as development data for the same campaign.

Research agents and parallel workers may receive only the development partitions assigned to them. They may not inspect another candidate's protected results before completing their own output. Candidate comparisons happen only after every assigned result is committed to the experiment ledger.

## Execution and Capital Boundary

All current commands remain simulation-only. Strategy research cannot add a public paper or live command. A promoted candidate must complete shadow and authenticated paper acceptance through the existing default-paused, one-use approval, reconciliation, stale-state, and kill-switch architecture before any later capital discussion.[2] Real-money activation remains a distinct project requiring a new risk review and explicit transaction-level confirmation.

## Change Control

Governance changes require a new version and an explanation of whether the change was made before or after observing any affected result. Threshold reductions, universe deletions, cost reductions, benchmark substitutions, or holdout changes made after observation invalidate the campaign. Failed evidence is retained rather than rewritten.

## References

[1]: ../evaluation/results/v1-preholdout/README.md "Preregistered v1 Pre-Holdout Evidence"
[2]: LIVE_READINESS.md "Live-Readiness Contract"
