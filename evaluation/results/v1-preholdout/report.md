# Preregistered Walk-Forward Evaluation

> This report is historical research evidence, not a forecast, live-performance record, or capital-deployment authorization.

## Run Identity

| Field | Value |
|---|---|
| Protocol | `qtpro-walk-forward-v1` |
| Protocol SHA-256 | `becf6ad29b0649c7f97c76fb149d41eeb6bac92760f1ff616e4772a596d1acb5` |
| Candidate trials | 2016 |
| Out-of-sample cost trials | 252 |
| Robustness trials | 336 |
| Pre-holdout promotion gate | **FAIL** |

## Asset Results

| Asset | Folds | Median base excess | Positive fold share | Median return | Median drawdown |
|---|---:|---:|---:|---:|---:|
| SPY | 14 | -1.81% | 7.14% | 3.51% | -6.60% |
| QQQ | 14 | -2.27% | 7.14% | 7.78% | -9.40% |
| IWM | 14 | -3.82% | 7.14% | 0.00% | -8.63% |
| EFA | 14 | -2.72% | 14.29% | 0.31% | -5.46% |
| TLT | 14 | -1.62% | 42.86% | -0.75% | -4.60% |
| GLD | 14 | -2.02% | 14.29% | 0.80% | -5.63% |

## Gate Checks

| Check | Result |
|---|---|
| `all_assets_complete` | PASS |
| `minimum_folds_per_asset` | PASS |
| `median_base_test_excess_return_minimum` | FAIL |
| `positive_base_test_excess_share_minimum` | FAIL |
| `median_cost_2x_test_excess_return_minimum` | FAIL |
| `median_cost_5x_test_excess_return_minimum` | PASS |
| `maximum_test_drawdown_floor` | FAIL |
| `risk_halts_allowed` | FAIL |
| `pending_orders_allowed` | PASS |
| `negative_cash_allowed` | PASS |

## Evidence Boundary

The final 252-observation holdout remains unopened. This pre-holdout result cannot be promoted as final A+ research acceptance until the one-time lockbox command records its receipt and separate gates. A failed gate remains part of the public evidence record and must not be repaired by changing this protocol after seeing the result.
