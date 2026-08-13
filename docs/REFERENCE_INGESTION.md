# Point-in-Time Reference Ingestion

> **Status:** This module validates and seals research inputs. It does not create a trading edge, place orders, or certify a dataset as point-in-time merely because a provider supplied historical values.

## Purpose

Quantum Trader Pro now includes provider-neutral, fail-closed ingestion components for selected **reference data** and local **equity/corporate-action snapshots**. Their role is to ensure that research only receives records with retained source provenance, explicit availability times, immutable identities, and a checksummed snapshot manifest.

This is deliberately separate from the simulation engine and all broker adapters. No market-data fetcher can invoke a paper or live execution command.

| Input | Implemented component | Availability rule | Research limitation |
|---|---|---|---|
| SEC fundamentals | `SecEdgarFundamentalIngestor` | A company fact is accepted only when its accession appears in the SEC submissions feed with a matched acceptance timestamp. | Acceptance time establishes filing availability, not economic event time or a signal-quality claim. |
| Treasury par curves | `TreasuryParCurveIngestor` | The caller must supply a per-vintage publication-time policy; missing selected-vintage availability fails closed. | Treasury archive rows do not themselves prove when an observer could have obtained each value. |
| Equity bars | `PointInTimeEquityCsvIngestor` | Every row requires `event_at`, `available_at`, `captured_at`, raw OHLCV, volume, and finality. | The source CSV must itself be survivorship-safe and corporate-action-aware. |
| Corporate actions | `PointInTimeCorporateActionCsvIngestor` | Announcement/availability and effective timestamps are distinct and mandatory. | The ingestor does not infer dividends, splits, delistings, or OCC adjustments. |
| Immutable snapshot | `ResearchSnapshotWriter` | Every normalized source must be available no later than the declared decision cutoff and is bound into a canonical manifest. | A sealed manifest proves retained bytes and declared rules, not vendor completeness or economic validity. |

## Data Contract

All normalized records implement the public [point-in-time data contracts](DATA_CONTRACTS.md). In particular, a record has three different timestamps:

| Field | Meaning | Prohibited substitution |
|---|---|---|
| `event_at` | When the market, filing, action, or rate observation occurred. | It is not automatically a tradable timestamp. |
| `available_at` | The earliest retained time at which the research process claims it could observe the record. | It cannot precede `event_at` or `published_at`. |
| `captured_at` | When this system retained the source response or local snapshot. | It cannot be used as a proxy for historical availability. |

The normalizer rejects naive timestamps, noncausal timestamp order, malformed identifiers, incomplete source provenance, nonfinite decimal values, inconsistent adjustment columns, nonmonotonic bar sequences, and incomplete corporate-action ratios.

## SEC Fundamentals

The SEC adapter retrieves only the official company-facts and submissions endpoints. It normalizes an allowed filing form only if the fact's accession number has a retained matching SEC acceptance timestamp. It rejects unpaired facts instead of assigning an estimated availability time.

```python
from quantum_trader.adapters.research_ingestion import (
    SecEdgarFundamentalIngestor,
    SecEdgarTransport,
)

transport = SecEdgarTransport(
    user_agent="YourResearchName analyst@example.com"
)
ingestor = SecEdgarFundamentalIngestor(transport=transport)
receipt = ingestor.fetch(cik="0000320193")
```

The `user_agent` must contain a contact address to comply with SEC access expectations. Raw SEC responses and locally normalized rows are intentionally **not** included in this repository.

## Treasury Curves

The Treasury adapter reads official daily archive CSVs but requires an independent availability policy:

```python
curves = ingestor.normalize(
    response=archive_response,
    availability_by_vintage={
        vintage_date: documented_publication_timestamp,
    },
    requested_dates=(vintage_date,),
)
```

A convenient clock-time assumption is not acceptable for an A+ research claim. The policy must point to retained provider publication evidence for each selected vintage. The repository includes adapter tests using an explicitly labeled scenario timestamp only to test mechanics; that timestamp is not strategy data.

## Equity Total Return and Corporate Actions

The local equity CSV path supports optional `adjusted_close` and `total_return_factor` fields, but they are **all-or-none** within a snapshot. Execution logic must continue using unadjusted executable prices. Adjusted values are reserved for benchmark and research-return semantics.

A total-return benchmark is valid only when the source also has point-in-time corporate-action availability. Dividends, splits, mergers, symbol changes, delistings, and options deliverable changes must be represented by the [corporate-action contract](DATA_CONTRACTS.md#corporate-actions). The ingestor will not backfill or infer missing events.

## Sealing a Research Snapshot

`ResearchSnapshotWriter` serializes normalized records into canonical JSONL sources and creates `manifest.json` with:

- raw, query, normalized, environment-lock, and schema-manifest checksums;
- exact code commit and decision cutoff;
- contract schema identities, source date ranges, record counts, and licensing flags;
- explicit sealed/opened/retired holdout boundaries; and
- optional immutable experiment-ledger head identity.

The output directory must be new and empty. Source files and the manifest are written with restrictive permissions. The writer refuses a source record whose `available_at` follows the snapshot decision cutoff, duplicate source identities, absent sources, invalid record envelopes, or a second seal operation.

A sealed snapshot is designed to become an immutable input to the [experiment ledger](EXPERIMENT_LEDGER.md). It does not authorize an evaluation or open a holdout.

## Provider and Licensing Boundary

The repository stores schemas, code, checksums, synthetic fixtures, and retained aggregate evidence. It does not store licensed OPRA options data, raw provider responses, account data, SEC response payloads, local equity data, or research snapshots. Those artifacts belong in local or secured storage and are excluded by `.gitignore`.

Before an equity, ETF, or options campaign can claim point-in-time validity, the selected provider must supply and retain enough evidence for:

1. exact source and schema version;
2. query or subscription identity;
3. raw payload checksum;
4. record-level or documented availability rules;
5. historical universe membership and corporate actions;
6. adjustment methodology and total-return semantics; and
7. license scope and redistribution restrictions.

## Current Acceptance Boundary

The adapters have been exercised against official SEC and Treasury sources using read-only requests and redacted receipts. This proves adapter mechanics and contract enforcement only. It does **not** add those observations to a strategy dataset, change the frozen v1 evaluation, open any holdout, validate an options strategy, or authorize paper/live execution.

See also: [Data Contracts](DATA_CONTRACTS.md), [Strategy Governance](STRATEGY_GOVERNANCE.md), [Experiment Ledger](EXPERIMENT_LEDGER.md), and [Research Approaches](RESEARCH_APPROACHES.md).
