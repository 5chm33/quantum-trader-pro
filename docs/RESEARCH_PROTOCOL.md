# Preregistered Research Protocol

## Purpose and Status

`evaluation/protocol_v1.json` freezes the first A+ research-evaluation design **before** the branch retrieves the defined 2015–2026 multi-asset dataset or observes a walk-forward result. The protocol is intended to reduce researcher degrees of freedom, not to manufacture a passing result. Bailey and López de Prado describe how multiple testing and non-normal returns can inflate Sharpe ratios,[1] while Bailey et al. show that ordinary holdouts may be unreliable in investment simulations and propose explicit backtest-overfitting diagnostics.[2]

> **Interpretation boundary:** Passing this protocol would support the claim that a simple strategy survived a declared historical robustness process. It would not prove future profitability, justify autonomous live trading, or authenticate the original project’s historical live-profit claim.

## Current Result

The completed pre-holdout evaluation retained **2,604 trials** across six assets and 84 untouched base-cost folds. It **failed** the frozen gate: median base excess return was −2.27%, 15.48% of folds beat the adjusted-close benchmark, the worst test drawdown was −34.33%, and six cost scenarios triggered a risk halt. The complete evidence is published under [`evaluation/results/v1-preholdout/`](../evaluation/results/v1-preholdout/).

The final 252 observations for each asset remain unopened. Because the pre-holdout evidence failed, no holdout receipt or result exists, and the repository does not claim research acceptance.

## Frozen Design

| Dimension | Preregistered rule |
|---|---|
| Observation interval | Daily |
| Date range | August 2015 through July 2026 |
| Assets | SPY, QQQ, IWM, EFA, TLT, and GLD |
| Benchmark | Complete adjusted-close buy-and-hold total-return proxy for each asset |
| Rolling windows | 504 training bars, 126 validation bars, 126 untouched test bars, stepped by 126 bars |
| Final lockbox | Last 252 observations, excluded from every earlier fold |
| Candidate family | Fast windows 20/50/100, slow windows 100/200/250, invested fractions 50%/75%/95%, with fast strictly below slow |
| Cost scenarios | Base, 2×, and 5× declared slippage and fees |
| Start-date sensitivity | 0, 21, and 63-bar offsets |
| Gap-buffer sensitivity | 500, 1,000, and 2,000 basis points |
| Trial retention | Every attempted specification, including failures and rejected candidates |

The asset set is fixed before retrieval to prevent post-result symbol deletion. It spans U.S. large-cap, growth, small-cap, developed ex-U.S. equities, long-duration U.S. Treasuries, and gold. It is a robustness panel, not a claim that each instrument is equally investable for every operator.

## Selection and Evaluation Order

The training block exists to establish sufficient history and strategy warm-up. Candidate ranking uses the following validation block only. The selected candidate is then replayed on the next untouched test block under base, 2×, and 5× costs. The test result cannot change the candidate for that fold.

| Order | Rule |
|---:|---|
| 1 | Enumerate the complete candidate set deterministically. |
| 2 | Run and retain every candidate on the validation block using only preceding training history for warm-up. |
| 3 | Exclude candidates with a risk halt, pending final order, or drawdown below the declared floor. |
| 4 | Rank eligible candidates by validation excess return versus the adjusted-close proxy. |
| 5 | Resolve ties by lower absolute drawdown, lower turnover, and deterministic candidate ID. |
| 6 | Freeze the winner, then evaluate the untouched test block and cost scenarios. |
| 7 | Advance by 126 observations and repeat without revising the protocol. |

Palomar’s synthesis of backtesting research warns that repeatedly modifying a model after viewing test results contaminates the test set and recommends defining the sample ex ante, tracking every trial, including costs, and avoiding iterative tweaking.[3] This harness therefore emits a trial ledger rather than only the winning row.

## Locked Holdout

The final 252 observations for each asset are excluded from candidate selection, rolling test aggregation, chart drafting, and acceptance tuning. Running the holdout requires an explicit command and writes `holdout_receipt.json` containing the protocol digest, data digests, branch commit, command timestamp, selected specification source, and result hashes. A second holdout attempt against the same protocol and output root fails closed.

The holdout is not magically “pure”: researchers already know broad market history. It is a procedural lockbox that limits direct optimization and records when the final block was opened.

## Promotion Gates

The machine-readable gates are deliberately capable of failing. The aggregate pre-holdout result requires complete assets, at least eight folds per asset, nonnegative median base and 2× test excess return, at least 55% positive base asset-fold tests, median 5× excess no worse than −5%, no risk halts or pending orders, and no drawdown below −30%. The final holdout separately requires nonnegative median excess and at least half of assets positive.

A failure is retained as a scientifically useful result. The repository must not lower a gate after observing an outcome under this protocol; a revised protocol requires a new version, rationale, and unexamined future data.

## Reproduction

Provider data are fetched locally and are not redistributed in the repository. From a development installation:

```bash
python scripts/fetch-evaluation-data.py \
  --protocol evaluation/protocol_v1.json \
  --output .local/evaluation-data

quantum-trader evaluate \
  --protocol evaluation/protocol_v1.json \
  --data-dir .local/evaluation-data \
  --output .local/v1-preholdout
```

The fetch command requires network access; evaluation itself is offline. The generated source manifest records the protocol and CSV SHA-256 digests. Provider revisions can change a future data digest, which constitutes a new dataset version rather than a silent replacement of the retained evidence.

The lockbox command is intentionally **not** shown as a routine next step. It requires `--confirm-protocol-id qtpro-walk-forward-v1`, creates a durable receipt before evaluation begins, and must be used only after satisfactory pre-holdout evidence under a frozen protocol. That condition was not met.

## Artifacts

The evaluation writes a protocol snapshot and digest, source manifest and checksums, complete candidate trial ledger, fold selections, out-of-sample test ledger, cost-sensitivity table, robustness table, summary JSON, readable report, and deterministic charts. The lockbox command adds its receipt and holdout-only results only when it is deliberately opened.

## References

[1]: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 "Bailey and López de Prado — The Deflated Sharpe Ratio"
[2]: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253 "Bailey et al. — The Probability of Backtest Overfitting"
[3]: https://portfoliooptimizationbook.com/book/8.3-dangers-backtesting.html "Palomar — The Dangers of Backtesting"
