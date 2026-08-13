# Daily-Equity Provider Admission

> **Classification: admission-only control.** This layer inspects a provider’s declared fields, timing semantics, coverage, correction policy, and dataset-retention permission before a campaign exists. It cannot fetch market data, create a snapshot, register a candidate, access a lockbox, write the experiment ledger, or create paper/live orders.

The admission control separates two questions that must not be conflated:

| Question | Allowed output |
|---|---|
| Is the provider and its declared daily-equity dataset sufficient for the project’s stated campaign requirements? | A deterministic, machine-readable **admitted** or **rejected** receipt. |
| Does a candidate perform well on that provider’s data? | **No output.** Candidate registration and any return computation are outside admission. |

## Required Input Dictionary

The reviewer accepts an inspected-field JSON document and fails closed unless each required component is present. A field’s presence alone is insufficient: each field receipt must retain its source path and time semantics, and each component must carry a dated coverage receipt, missing-record count, and correction policy.

| Component | Required fields | Additional required evidence |
|---|---|---|
| Daily price | `instrument_id`, `event_at`, `available_at`, `session_id`, `close`, `adjusted_close`, `unadjusted_close`, `volume` | Explicit adjustment convention, trading-session identifier, historical coverage, and a missing-bar policy. |
| Corporate actions | `action_type`, `effective_at`, `available_at` | Dated coverage and a correction policy. |
| Realistic cash rate | `rate_date`, `available_at`, `rate` | Dated coverage and a correction policy aligned to the campaign’s decision dates. |
| Universe | No opaque field list substitutes for an eligibility rule. | Fixed-universe or point-in-time eligibility rule, plus dated coverage. |
| Liquidity/cost proxy | At least one of `bid`, `ask`, `quoted_spread`, `liquidity_proxy`, or `participation_capacity` when the declared cost model requires one. | The intended cost model must be declared separately in the campaign protocol. |

The inspection must also affirm the right to retain and rerun the frozen research dataset. It must set `lockbox_query_executed`, `candidate_registered`, and `snapshot_created` to `false`. Any true value produces a rejected receipt; an admission receipt may never record campaign state.

## Read-Only Workflow

The command has no provider client and no market-data endpoint. A provider-specific adapter or an operator must first inspect actual returned headers, field conventions, coverage, and documentation, then create the inspection document. The reviewer only validates the recorded inspection.

```bash
uv run --extra dev python scripts/review_daily_equity_provider.py \
  --inspection research/examples/daily_equity_provider_inspection.example.json \
  --output /private/provider-admission-receipt.json
```

The command prints canonical JSON to standard output and, when requested, writes a mode-`0600` receipt file. The committed example is **synthetic** and demonstrates the schema only; it is not market data, a provider validation, a snapshot, or campaign evidence.

| Receipt property | Meaning |
|---|---|
| `status: admitted` | All declared admission fields and boundaries were present in the inspected document. This still does not register a candidate or create a snapshot. |
| `status: rejected` | The receipt retains canonical failure codes such as missing adjusted/unadjusted price fields, action timing, volume, calendar, cash alignment, universe rule, retention right, quote/proxy, missing-bar policy, coverage, or an inadmissible campaign-state attempt. |
| `receipt_sha256` | SHA-256 of the canonical receipt JSON for later protocol binding. |
| `fields` and `coverage` | The provider-specific machine-readable data dictionary and coverage receipts, canonically sorted for deterministic comparison. |

## Admission Boundary and Next Steps

An admitted provider receipt is a necessary **data-capability** control, not an evidence-grade strategy result. Only after an admitted receipt exists may the project freeze the actual provider query, rights classification, adjustment policy, raw/normalized hashes, realistic cash/T-bill series, universe rule, cost model, candidate budget, baselines, walk-forward plan, and separate unretrieved lockbox in a fresh campaign protocol.[1] [2]

Options-quality data is not required at this stage. It becomes mandatory before any option affects a registered signal, portfolio allocation, execution, cost, lifecycle, benchmark, or performance claim.[3]

This document is research infrastructure only, not personalized financial advice.

## References

[1]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/PHASE13_DATA_READINESS.md "Quantum Trader Pro — Daily-Equity and ETF Data-Readiness Decision"
[2]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/PREREGISTRATION_PROTOCOL.md "Quantum Trader Pro — Preregistration, Walk-Forward, Regime, and Lockbox Protocol"
[3]: https://github.com/5chm33/quantum-trader-pro/blob/research/a-plus-strategy-v1/docs/PHASE14_OPTIONS_DATA_READINESS.md "Quantum Trader Pro — Options Data-Readiness Decision"
