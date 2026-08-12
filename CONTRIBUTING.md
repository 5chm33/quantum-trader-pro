# Contributing to Quantum Trader Pro

Quantum Trader Pro welcomes focused improvements to simulation correctness, data validation, accounting, risk controls, reporting, documentation, and test coverage. It does not accept live-trading shortcuts or changes that weaken the evidence boundary.

## The Golden Rule: Preserve the Safety Boundary

A pull request must not add a paper or live mode, broker credential, production endpoint, hidden network call, direct strategy-to-broker reference, synthetic fallback presented as real data, same-bar look-ahead fill, or silent accounting recovery. A proposal that needs external execution should begin as an architecture and threat-model discussion.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
quantum-trader preflight
```

The runtime supports Python 3.11 and newer. Development should occur in an isolated environment; do not rely on packages that happen to exist globally.

## Workflow

Create a focused branch, add or update tests before changing behavior, keep domain logic independent from adapters, and document any methodology change. Generated simulation artifacts should not be committed unless they are intentional evidence assets under `docs/assets/` with provenance and a reproducibility note.

| Change type | Required evidence |
|---|---|
| Domain model or portfolio | Unit tests for every new invariant and accounting branch |
| Risk policy | Allow, deny, quantity-reduction, duplicate, halt, and risk-reducing exit tests |
| Strategy | Deterministic warm-up, entry, exit, and repeated-run tests |
| Market-data adapter | Schema, timezone, ordering, gap, invalid-value, and provenance tests |
| Broker adapter | Intent/decision matching, idempotency, causality, costs, and reconciliation tests |
| Persistence | Ordering, canonical serialization, integrity, closure, and replay tests |
| Reporting | Formula fixtures, edge cases, benchmark language, and byte-identical rerun evidence |
| Deployment | Preflight, least privilege, duplicate-instance protection, and no-network verification |

## Quality Gate

Run the complete gate before opening a pull request:

```bash
ruff format --check src tests
ruff check src tests
mypy src
pytest --cov=quantum_trader --cov-report=term-missing
bandit -q -r src
python -m build
```

Coverage must remain at or above 90%. Tests must not be deleted, skipped, weakened, or changed solely to make a defect appear to pass. A bug fix should include a regression test that fails under the previous behavior.

## Code Style

Use explicit types, timezone-aware datetimes, `Decimal` for money and prices, immutable value objects where practical, and small components with a single responsibility. Prefer a clear failure to a guessed fallback. Do not catch broad exceptions unless the branch records a safe halt and the exception type rather than secret-bearing content.

Identifiers derived from business fields must remain deterministic. Serialization must remain canonical. New nondeterministic inputs such as wall-clock time, random numbers, unordered sets, network state, or mutable globals require explicit injection and tests.

## Research and Performance Claims

A performance change must identify the data source, exact period, input digest, fees, slippage, fill model, benchmark, parameter-selection process, and whether the sample is in-sample, validation, or out-of-sample. Dividend-adjusted total return must not be inferred from an unadjusted close series.

Synthetic fixtures may be used for correctness tests but must be labeled and must not support market-performance claims. Live-profitability claims require authenticated external brokerage evidence and cash-flow reconciliation.

## Commit and Pull-Request Format

Use an imperative summary such as `Fix emergency exit after drawdown halt`. The pull-request description should explain the problem, the safety impact, the chosen design, the tests added, and any remaining limitation. Keep unrelated refactors out of behavioral fixes.

## Security Reports

Do not disclose a suspected credential leak or exploitable vulnerability in a public issue. Follow [`SECURITY.md`](SECURITY.md) and use GitHub’s private security-advisory workflow.
