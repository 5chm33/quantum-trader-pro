# Simulation and Evaluation Methodology

## Objective

Quantum Trader Pro is designed to make an incorrect trading claim difficult to hide. A run must preserve ordered input provenance, causal execution, reconciled accounting, explicit risk decisions, declared costs, benchmark availability, an end-of-test convention, and deterministic artifacts. None of those properties establishes future profitability; they establish that the historical experiment is inspectable.

| Evidence layer | Current status |
|---|---|
| v0.1.0 retained SPY run | Reproducible engineering baseline with a price-only benchmark; not out-of-sample alpha evidence |
| A+ methodology branch | Adjusted-close benchmark support, conservative buy sizing, post-fill checks, flat-to-flat attribution, and explicit end-state handling implemented and tested |
| Walk-forward evaluation | Specified for the next phase; no result may be promoted before locked-window acceptance |
| Paper brokerage | Not yet implemented; simulation remains the only executable mode |
| Live capital | Disabled and outside the current acceptance boundary |

## Input Data Contract

A replay CSV must contain `datetime`, `open`, `high`, `low`, `close`, and `volume`. Timestamps are normalized to UTC, must be strictly increasing, and are checked against a declared maximum gap. OHLC values must be finite, positive, and internally consistent. The exact input bytes are identified by SHA-256 in the source name.

An optional `adjusted_close` column is accepted only as a complete all-or-none series. Yahoo defines adjusted close as the closing price adjusted for applicable splits and dividend distributions.[1] The engine therefore uses raw OHLC fields for signals and next-open fills while using `adjusted_close` only as a buy-and-hold total-return proxy. If the column is absent, the report records `unavailable_missing_adjusted_close`; it does not infer dividends or promote raw price return to a total-return benchmark.

| Series | Permitted use |
|---|---|
| Raw close | Strategy observation, portfolio marking, and secondary price-return diagnostic |
| Raw next open | Causal simulated execution reference |
| Adjusted close | Headline buy-and-hold total-return proxy when complete |
| Fabricated or forward-filled benchmark | Prohibited |

## Strategy

The explanatory baseline maintains a rolling close-price window. Before the slow window is full, its target allocation is zero. After warm-up, it calculates arithmetic means over the fast and slow windows. If the fast mean is strictly greater than the slow mean, the target fraction is the configured invested fraction; otherwise the target is zero.

The retained v0.1.0 run used fast window 50, slow window 200, and invested fraction 95%. Those parameters were not selected through an isolated train/validation/test procedure. They remain an engineering fixture, not evidence that 50/200 is optimal. The walk-forward phase freezes selection and holdout rules before producing a replacement evaluation card.

## Order Construction and Conservative Risk Sizing

The portfolio converts a target fraction into a whole-share delta using reconciled marked equity and the current close. Before a buy can be approved, the risk manager calculates a conservative executable-price bound:

> **Conservative buy price** = reference close × (1 + (slippage bps + execution-buffer bps) / 10,000)

Fixed order fees and per-share fees are reserved before quantity approval. The resulting quantity must satisfy the maximum order commitment, maximum position fraction, and minimum cash reserve simultaneously. The default execution-price buffer is 1,000 basis points; it is a sizing assumption, not a promise that a market cannot gap farther.

After a buy fills, the system applies the real fill to accounting first and then rechecks order commitment, position exposure, and cash reserve at the actual fill price. A breach halts new exposure and records `risk_halt`. An executed fill is never discarded merely to preserve a risk metric. Sells remain risk-reducing and are not blocked because an appreciated position exceeds its original target fraction.

Drawdown, non-positive equity, and realized-loss circuit breakers are observed on every reconciled equity point. Once halted, new buys are denied while a target-to-cash override and owned-quantity-limited sell remain eligible.

## Execution Model

An intent created from event *t* cannot fill at *t*. The simulated broker queues an approved order and fills it only on the next event whose timestamp is later than the intent. A buy adds configured basis-point slippage to that event’s open; a sell subtracts it. Fees combine fixed per-order and per-share amounts.

The simulator currently assumes complete fills. It does not model spread separately, market impact, latency, queue position, price improvement, auctions, halts, borrow, margin, taxes, or regulatory fees. Alpaca likewise warns that paper trading omits market impact, information leakage, latency slippage, queue position, price improvement, regulatory fees, and dividends, and that simulators can differ in fill, liquidity, return, and data assumptions.[2] Paper evidence will therefore remain distinct from backtest evidence, and neither will be labeled authenticated live performance.

## End-of-Test Policy

At the final market event, every still-pending order is canceled and recorded as `order_canceled_end_of_test`. The engine does not fabricate a later fill or a closing auction. Any remaining position is marked to the final observed close and disclosed through `open_position_at_end`. The policy identifier is `cancel_pending_mark_positions_to_final_close`.

This convention separates three states that must not be conflated: a completed flat-to-flat trade, an unfilled canceled order, and an open marked position. A future optional liquidation convention would require an explicit operator selection and a defensible execution price; it cannot be silently substituted into an existing run.

## Accounting

Cash decreases by buy notional plus fees and increases by sell notional minus fees. Position average cost is the quantity-weighted executed purchase price. Realized P&L is recognized on sales against average cost. Fees are accumulated separately, unrealized P&L marks remaining shares to the current close, and every equity point must satisfy `equity = cash + market value`.

## Round-Trip Trade Attribution

Reporting groups all fills from flat to flat for each symbol. A round trip may contain multiple entries, partial exits, or rebalances. It closes only when the owned quantity returns to zero. Net trade P&L equals total sell notional minus total buy notional minus all entry and exit fees.

| Metric | Definition |
|---|---|
| Round-trip trade count | Number of completed flat-to-flat episodes |
| Trade win rate | Positive-net-P&L round trips divided by completed round trips |
| Expectancy | Mean net P&L per completed round trip |
| Profit factor | Gross winning trade P&L divided by absolute gross losing trade P&L; undefined without losses |
| Average win/loss | Mean positive or negative round-trip net P&L |
| Holding time | Time from first entry fill to the fill that returns quantity to zero |
| Open round trips | Started but not flat at the final observation |

The legacy exit-fill diagnostic remains nested in the machine-readable report with an explicit warning that partial exit fills are not independent trades. It is not presented as the strategy’s trade win rate.

## Portfolio Metrics

| Metric | Definition |
|---|---|
| Strategy total return | Final marked equity divided by initial equity minus one |
| Annualized return | Geometric annualization over elapsed time when the sample is at least 30 days |
| Maximum drawdown | Minimum of current equity divided by prior running peak minus one |
| Sharpe diagnostic | Mean event-to-event equity return divided by sample standard deviation, using a 0% risk-free rate and median-spacing annualization |
| Total-return proxy | Last adjusted close divided by first adjusted close minus one, only when supplied for every row |
| Price return | Last raw close divided by first raw close minus one |
| Average exposure | Mean market value divided by equity across observations |
| Time in market | Fraction of observations with nonzero market value |
| Annualized turnover | Gross fill notional divided by average equity and elapsed years |

The Sharpe value is a reproducible diagnostic, not a universally comparable statistic. It includes cash and warm-up observations, uses event-to-event returns, assumes a zero risk-free rate, and derives annualization from observation spacing.

## Retained v0.1.0 Result

The public baseline retains its original five-year SPY result because removing an unfavorable comparison would weaken the evidence record. It used 1,255 daily observations from August 12, 2021 through August 12, 2026 and a price-only benchmark with dividends excluded.

| Metric | v0.1.0 value |
|---|---:|
| Strategy total return | 60.21% |
| Buy-and-hold price return | 73.66% |
| Excess versus price benchmark | -13.45 percentage points |
| Annualized strategy return | 9.89% |
| Maximum drawdown | -18.07% |
| Sharpe diagnostic | 0.88 |
| Simulated fills | 58 |

![SPY validation](assets/spy_validation.png)

This card is historical baseline evidence, not the A+ acceptance result. It underperformed an already-generous price-only benchmark and did not use a locked holdout, walk-forward parameter selection, cost grid, parameter perturbation, or multi-asset protocol. Phase six replaces it only after those rules are preregistered and executed.

## Reproducibility

A run’s source digest, canonical configuration, deterministic identifiers, injected replay clock, ordered SQLite ledger, payload hashes, and finite artifacts support byte-for-byte reruns. The current output set is `simulation_report.json`, `simulation_report.md`, `equity_curve.csv`, `fills.csv`, `round_trip_trades.csv`, and `events.sqlite3`.

The repository does not ship downloaded market data as performance evidence. A reviewer must obtain appropriately licensed data, preserve its checksum, and disclose any provider revision before claiming reproduction.

## Research Limitations and Next Gate

The v0.1.0 result covers one ETF and one regime sequence. It has no isolated tuning and test periods, does not control for multiple trials, and uses a simplified full-fill execution model. The next acceptance gate requires a preregistered rolling training/validation/test procedure, one final locked period, adjusted-close benchmarks, predefined assets and regimes, cost sensitivity, parameter perturbation, and complete retention of attempted specifications.

A positive historical return does not establish a durable edge. A strategy can pass every engineering and research gate and still lose money in future markets.

## References

[1]: https://help.yahoo.com/kb/SLN28256.html "Yahoo Help — What is the adjusted close?"
[2]: https://docs.alpaca.markets/us/docs/paper-trading "Alpaca — Paper Trading"
