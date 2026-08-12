# Simulation Report

> This is an offline deterministic simulation, not live performance or financial advice.

| Metric | Value |
|---|---:|
| Run ID | run_37c79bca8b8d5211823b810cabf72f66 |
| Source | csv:spy_daily.csv:sha256:879a1a5a2404eddfb9241a5e76525cfb04798801c8aff7415f3db31ff8dc88aa |
| Period | 2021-08-12T13:30:00+00:00 to 2026-08-12T13:30:00+00:00 |
| Observations | 1,255 |
| Initial equity | 100000 |
| Final equity | 160206.36087987500 |
| Total return | 60.21% |
| Buy-and-hold price return (dividends excluded) | 73.66% |
| Maximum drawdown | -18.07% |
| Sharpe ratio | 0.877 |
| Fills | 58 |
| Total fees | 3.550000 |
| Modeled slippage | 74.024199 |
| Risk halted | False |

## Methodology

Orders are generated from explicit historical observations, pass through fail-closed risk checks, and fill at the next eligible event open with the declared fee and slippage model. The benchmark is the unadjusted buy-and-hold price return over the identical source period; dividends are excluded. Every decision, order, fill, and equity point is retained in the SQLite event ledger.

**Metrics checksum:** `11101d7447b01b920f9b6f8866690e46f7616973728f2e0a851db856b7c3f247`
