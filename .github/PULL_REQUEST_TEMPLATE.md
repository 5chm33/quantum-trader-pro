## Summary

Describe the problem and the focused change.

## Safety Impact

Explain whether this affects execution policy, market data, order intent, risk review, simulated fills, accounting, persistence, filesystem access, secrets, or deployment.

## Methodology Impact

State whether results, fill timing, costs, benchmark semantics, or report formulas change. If not, write “None.”

## Validation

| Check | Result |
|---|---|
| `ruff format --check src tests` | |
| `ruff check src tests` | |
| `mypy src` | |
| `pytest --cov=quantum_trader --cov-report=term-missing` | |
| `bandit -q -r src` | |
| `python -m build` | |

## Required Confirmation

- [ ] The simulation-only boundary remains intact.
- [ ] No credential, account identifier, production endpoint, or private data is included.
- [ ] New behavior has regression tests.
- [ ] Synthetic data is labeled and is not used for market-performance claims.
- [ ] Documentation and methodology were updated where required.
