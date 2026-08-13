# Strategy Hypothesis Evidence Matrix

**Status:** Literature-screened hypothesis catalog; no candidate has been promoted or tested against a new holdout.

The campaign begins with economic hypotheses, not indicators selected because they look profitable. Each family needs a mechanism, a falsifiable prediction, a permanent baseline, point-in-time data, realistic implementation costs, and a fixed candidate ceiling. The ceilings below are **upper bounds for a later preregistration**; they are not permission to run every combination and select the best-looking result.

## Evidence Screen

| ID | Family | Role | Evidence Assessment | Data Feasibility | Proposed Candidate Ceiling |
|---|---|---|---|---|---:|
| H01 | Time-series trend | Core forecast | Strong long-run literature, but replication evidence shows volatility scaling and leverage can explain much of reported alpha; a scaled buy-and-hold comparator is mandatory.[1] [2] | High for liquid ETFs | 6 |
| H02 | Cross-sectional momentum | Core forecast | Broad evidence across assets; crash, sector, beta, and turnover exposure must be neutralized or reported rather than hidden.[3] | High for a frozen ETF universe | 6 |
| H03 | Liquidity-shock reversal | Conditional forecast | Mixed and highly cost-sensitive. Recent evidence distinguishes low-turnover liquidity reversals from high-turnover, news-driven continuation, so the classifier must separate liquidity shocks from information events.[4] | Medium; requires spreads, volume, and event controls | 4 |
| H04 | Volatility targeting | Portfolio overlay | Strong evidence as a risk-management overlay, not proof of alpha. Benefits must be separated from embedded trend, leverage, and timing effects.[5] | High | 4 |
| H05 | Defensive quality/value/profitability/low-volatility | Core forecast | Broad factor evidence, but point-in-time fundamentals, factor crowding, and long regime underperformance are material risks.[3] | Medium; requires licensed point-in-time fundamentals | 4 |
| H06 | Post-earnings-announcement drift | Event forecast | Decades of evidence and plausible underreaction mechanism, but explanations remain incomplete and recent market efficiency may compress the effect.[6] | Medium to low; requires exact event times, estimates, actuals, and delisting-safe universes | 4 |
| H07 | Diversified forecast portfolio | Meta-portfolio | Diversification is credible only when each component contributes out of sample. It cannot rescue individually failed signals through in-sample weighting. | High after component evidence exists | 4 |
| H08 | Defined-risk volatility-risk-premium harvesting | Options forecast | A priced insurance premium is economically plausible, but returns are non-normal and left-tail compensation can masquerade as alpha. Multi-leg spread costs and stress liquidity are decisive.[7] | Low until licensed point-in-time options quotes are available | 6 |
| H09 | Volatility term structure and skew | Options relative-value forecast | Published evidence links term-structure slope to future option returns, but implementation evidence after multi-leg spreads, assignment, and prolonged regimes remains mixed.[8] | Low until point-in-time chains and rates/dividends are available | 6 |
| H10 | Trend-conditioned calls/debit spreads | Options convex forecast | The underlying trend mechanism is credible; evidence that options improve net performance is weaker because implied-volatility premium and theta can dominate. | Low until options quotes are available | 4 |
| H11 | Conditional protective puts/put spreads | Portfolio insurance | Tail hedges can protect bad states, but demand makes them expensive when perceived tail risk is high. Grade this on drawdown and utility after drag, not standalone return.[9] | Low until options quotes are available | 4 |
| H12 | Covered calls and cash-secured puts | Permanent options baseline | Useful as fully collateralized baselines for volatility-premium exposure, assignment, and tax-lot-free research accounting. They are not presumed alpha and retain equity downside or capped upside. | Low until options quotes are available | 4 |

The proposed ceilings total **56 potential specifications**, but later inference must treat them as twelve related families rather than 56 independent discoveries. The experiment ledger will retain every attempt, and the preregistered campaign must use hierarchical multiple-testing controls before any promotion claim.

## Falsifiable Predictions and Permanent Baselines

| ID | Falsifiable Prediction | Permanent Baseline |
|---|---|---|
| H01 | A volatility-scaled trend forecast improves net risk-adjusted performance across rolling test folds after leverage-matched and volatility-scaled passive comparators. | Same-universe equal-weight and volatility-scaled buy-and-hold portfolios. |
| H02 | A beta- and sector-controlled winner-minus-loser ETF portfolio produces positive net test-fold excess return without relying on a single sector or crisis interval. | Same-universe equal-weight portfolio and unneutralized momentum. |
| H03 | Identified non-news liquidity shocks reverse after spread, impact, and delay costs, while news-driven shocks do not. | No-trade, unconditional reversal, and unconditional continuation rules. |
| H04 | Volatility targeting reduces test-fold drawdown or improves utility after turnover costs without a hidden increase in average leverage. | Static exposure and leverage-matched static exposure. |
| H05 | A frozen defensive factor blend improves downside risk and net risk-adjusted return across regimes without retrospective constituent or filing information. | Broad market-cap index and equal-weight eligible universe. |
| H06 | Point-in-time earnings surprises predict same-direction residual returns after the first executable post-announcement price, costs, and risk controls. | Event-matched eligible-universe return and a no-surprise event portfolio. |
| H07 | A fixed forecast blend improves test performance and stability versus its strongest single component, not merely its average component. | Equal-weight components and the strongest predeclared single component. |
| H08 | Defined-risk spreads earn compensation after both-leg execution costs while surviving predeclared tail-loss, expected-shortfall, and stress-liquidity gates. | Fully collateralized passive underlying and fully collateralized put-writing baseline. |
| H09 | A frozen term-structure or skew condition improves defined-risk spread outcomes versus an unconditional same-structure roll after all lifecycle costs. | Unconditional same-delta, same-expiry rolling spread. |
| H10 | A frozen trend condition improves long-call or debit-spread net outcomes versus unconditional rolling options and the underlying trend strategy. | Underlying trend strategy and unconditional option roll. |
| H11 | Conditional insurance reduces drawdown or expected shortfall per unit of long-run premium drag versus unconditional insurance and no hedge. | No hedge and unconditional same-budget put-spread overlay. |
| H12 | Fully collateralized covered-call and put-writing returns reconcile exactly through dividends, assignment, exercise, expiry, and underlying lots. | Buy-and-hold underlying with the same cash and dividend convention. |

## Research Implications

### Volatility scaling is a controlled variable

The time-series momentum literature is substantial, but the replication evidence is a warning: scaling can increase both trend and passive portfolio alphas, and more recent subperiods can underperform.[1] The campaign therefore treats trend direction and volatility scaling as separate modules. Every report must show unscaled, volatility-scaled, and leverage-matched baselines.

### Reversal must be separated from information continuation

Short-horizon reversal is not a generic “price fell, therefore buy” rule. Evidence indicates that low-turnover or liquidity-driven moves can reverse, while high-turnover, news-driven moves can continue.[4] H03 must therefore use point-in-time news/event exclusions and reject any implementation whose gross edge is smaller than a conservative spread-and-impact envelope.

### Options premium is compensation for risk, not free alpha

Volatility and tail-risk premiums can exist because investors pay for protection, but their return distributions are nonlinear and left-tailed.[7] [9] H08 and H12 must report expected shortfall, worst lifecycle loss, gap stress, assignment outcomes, collateral use, and multi-leg transaction costs. Sharpe ratio, win rate, or premium collected cannot be a promotion gate by itself.

### Options structures must be compared with the risk they replace

Long calls and put overlays are insurance or convexity purchases; covered calls and collateralized puts reshape equity exposure. Each options hypothesis therefore has at least two baselines: the underlying exposure and an unconditional version of the same option structure. Options support cannot receive a strategy grade from accounting tests alone.

## Preliminary Research Roles

The literature screen does not select a winner. It assigns roles that guide the next data-contract and experiment-ledger phases:

| Role | Hypotheses | Purpose |
|---|---|---|
| **Permanent baselines** | H04 static/leverage-matched variants; H12 collateralized option-income variants | Prevent sophisticated candidates from being compared only with weak benchmarks. |
| **Distinct equity forecasts** | H01, H02, H03, H05, H06 | Test separate trend, relative-strength, liquidity, defensive, and event mechanisms. |
| **Meta-portfolio** | H07 | Combine only components that independently survive rolling validation. |
| **Defined-risk options forecasts** | H08, H09, H10 | Test insurance selling, volatility relative value, and convex trend participation without naked short options. |
| **Portfolio insurance** | H11 | Measure downside protection and drag rather than standalone alpha. |

The next phase will define provider-neutral point-in-time schemas for every field named here. No options result will be computed from indicative quotes, reconstructed chains, or vendor Greeks without retained inputs and an independently reproducible calculation.

## References

[1]: https://www.sciencedirect.com/science/article/abs/pii/S1386418116301379 "Time Series Momentum and Volatility Scaling"
[2]: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026 "A Century of Evidence on Trend-Following Investing"
[3]: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1363476 "Value and Momentum Everywhere"
[4]: https://www.sciencedirect.com/science/article/abs/pii/S0378426621000261 "Short-Term Reversals, Short-Term Momentum, and News-Driven Trading Activity"
[5]: https://www.nber.org/papers/w22208 "Volatility Managed Portfolios"
[6]: https://www.sciencedirect.com/science/article/pii/S2214635020303750 "A Review of the Post-Earnings-Announcement Drift"
[7]: https://academic.oup.com/rfs/article-abstract/22/11/4463/1565787 "Expected Stock Returns and Variance Risk Premia"
[8]: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1944298 "Equity Volatility Term Structures and the Cross-Section of Option Returns"
[9]: https://www.federalreserve.gov/pubs/feds/2013/201354/index.html "Volatility of Volatility and Tail Risk Premiums"
