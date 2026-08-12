# Simulation and Evaluation Methodology

## Objective

The retained validation demonstrates that Quantum Trader Pro can process real ordered market observations, produce causally delayed simulated fills, reconcile a portfolio, enforce risk controls, compare a strategy with a clearly labeled benchmark, and reproduce identical artifacts. It does **not** establish that the chosen moving-average parameters are optimal or likely to outperform in the future.

## Data

The retained evaluation uses 1,255 daily SPY OHLCV observations from August 12, 2021 through August 12, 2026. The observations were retrieved through the Yahoo Finance chart-data interface. Yahoo’s public SPY historical page exposes Open, High, Low, Close, Adjusted Close, and Volume and states that close is adjusted for splits while adjusted close also reflects dividends and capital-gain distributions.[1]

The engine uses the unadjusted OHLCV quote arrays because next-open execution requires an observed open price. Therefore, the benchmark is a **price return with dividends excluded**, not a total-return index. Nasdaq independently identifies the instrument as the State Street SPDR S&P 500 ETF Trust and exposes a historical-data interface.[2]

| Field | Value |
|---|---|
| Symbol | SPY |
| Frequency | Daily |
| Observations | 1,255 |
| Start | 2021-08-12T13:30:00+00:00 |
| End | 2026-08-12T13:30:00+00:00 |
| Normalized CSV SHA-256 | `879a1a5a2404eddfb9241a5e76525cfb04798801c8aff7415f3db31ff8dc88aa` |
| Missing OHLCV rows retained | 0 |
| Sort order | Strictly increasing timestamp |

## Strategy

The strategy maintains a rolling close-price window. Before the slow window is full, its target allocation is zero. After warm-up, it calculates an arithmetic mean over the fast and slow windows. If the fast mean is strictly greater than the slow mean, the target fraction is the configured invested fraction; otherwise the target is zero.

The retained run uses fast window 50, slow window 200, and invested fraction 95%. These parameters are a conventional explanatory baseline, not a result of an isolated train/validation/test selection procedure. The same observations both trigger decisions and measure the retained result, so the run should be described as a transparent historical simulation rather than an out-of-sample estimate.

## Order Construction and Risk

The portfolio converts a target fraction into a whole-share delta using current marked equity and the current close as the reference price. The risk manager then applies maximum position fraction, maximum order notional, minimum cash reserve, maximum drawdown, maximum realized loss, and duplicate-intent controls.

The retained evaluation uses a 95% maximum position fraction, $1,000,000 maximum order notional, 1% minimum cash reserve, 50% maximum drawdown halt, and $1,000,000 maximum realized-loss halt. These permissive halt values are intended to observe the strategy rather than optimize risk-adjusted performance.

## Execution Model

An intent created from event *t* cannot fill at *t*. The simulated broker queues an approved order and fills it only on the next event whose timestamp is later than the intent. The fill reference is that event’s open.

For a buy, the model adds configured basis-point slippage; for a sell, it subtracts slippage. The retained run uses 2 basis points and a $0.005 per-share fee with no fixed order fee. The model assumes complete fills and does not model bid/ask spread separately, market impact, partial fills, queue position, auctions, halts, borrow, margin, or taxes.

## Accounting

Cash decreases by buy notional plus fees and increases by sell notional minus fees. Position average cost is the quantity-weighted executed purchase price. Realized P&L is recognized on sales against average cost. Fees are accumulated separately, unrealized P&L marks remaining shares to the current close, and total equity must equal cash plus market value at each event.

The report calculates turnover from fill notional and separately reports modeled slippage and fees. `winning_exit_rate` is an exit-fill statistic, not a round-trip trade win rate, because target rebalancing can create multiple partial sales.

## Metrics

| Metric | Definition |
|---|---|
| Total return | Final equity divided by initial equity minus one |
| Annualized return | Geometric annualization over the observed elapsed time when the sample is at least 30 days |
| Maximum drawdown | Minimum of current equity divided by prior running peak minus one |
| Sharpe ratio | Mean event return divided by sample standard deviation, annualized from the median event interval, with 0% risk-free rate |
| Buy-and-hold price return | Last unadjusted close divided by first unadjusted close minus one |
| Excess price return | Strategy total return minus buy-and-hold price return |
| Modeled slippage | Sum of per-fill absolute difference between event open and executed price times quantity |

The Sharpe ratio uses event-to-event equity returns and a zero risk-free rate. It is reported for transparency but should not be compared mechanically with results using monthly returns, a non-zero cash rate, or a different annualization convention.

## Retained Result

| Metric | Value |
|---|---:|
| Strategy total return | 60.21% |
| Buy-and-hold price return | 73.66% |
| Excess versus price benchmark | -13.45 percentage points |
| Annualized strategy return | 9.89% |
| Maximum drawdown | -18.07% |
| Sharpe ratio | 0.88 |
| Simulated fills | 58 |
| Total modeled fees | $3.55 |
| Total modeled slippage | $74.02 |
| Pending orders at end | 0 |
| Rejected fills | 0 |
| Risk halt | No |

![SPY validation](assets/spy_validation.png)

The strategy’s positive simulated return is retained alongside its benchmark underperformance. This avoids presenting only favorable evidence and keeps the repository focused on engineering quality rather than marketing a strategy.

## Reproducibility

The normalized CSV, exact configuration, deterministic IDs, injected replay clock, canonical payload serialization, and finite output set make the run reproducible. Two independent executions produced byte-identical artifacts.

| Artifact | SHA-256 |
|---|---|
| `simulation_report.json` | `5727c22ffc47c96345965d4e1595ac6909eba2d56b3cc77cef2c71f92eb4b50d` |
| `simulation_report.md` | `3f60aaf7af4845aed4ceadf0b65b67cb80d831bf8ec85a657b08b113db1bc53d` |
| `equity_curve.csv` | `cba95b43699b9b9402d0fe3e3798f0093799859d528f78e64231be57c3624929` |
| `fills.csv` | `327e9e0554088d16f9424e06b461dae67c69dc1aa463b10ac5d6d4925ab31c19` |
| `events.sqlite3` | `6e9f29b3a1617f338d16511c3a66f148610fed3b83cd442e392bcfda2272fbe0` |

The repository retains the report and visualization but not the downloaded raw market dataset. A user must supply appropriately licensed data and compare its checksum before claiming an exact reproduction.

## Research Limitations

This validation has no isolated training, tuning, validation, and test periods. It covers one liquid U.S. ETF and one market regime sequence. It omits dividends, corporate-action cash flows, taxes, spread dynamics, and market impact. The benchmark is price only. The source may be revised by its provider, which would change the checksum and result.

A strategy-focused study should define parameters before the evaluation period, add walk-forward splits, compare multiple regimes and assets, include an adjusted total-return benchmark, test cost sensitivity, use a complete exchange calendar, and retain all attempted specifications to reduce selection bias.

## References

[1]: https://finance.yahoo.com/quote/SPY/history/ "Yahoo Finance — SPY Historical Data"
[2]: https://www.nasdaq.com/market-activity/etf/spy/historical "Nasdaq — SPY Historical Data"
