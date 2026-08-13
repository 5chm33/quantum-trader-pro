"""Add predeclared non-tuning diagnostics to the provisional daily-equity result.

This script is intentionally read-only with respect to the parent candidate and ledger. It
verifies the parent summary hash, uses the same frozen snapshot and evaluation window, and
writes only descriptive benchmark, attribution, and asset-level diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from statistics import fmean
from typing import Any

from run_provisional_daily_equity import (
    DailyBar,
    _annualized_volatility,
    _candidate_weights,
    _decimal_text,
    _digest_file,
    _evaluate,
    _load_bars,
    _load_json,
    _metrics,
    _return,
    _validate_snapshot,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


class DiagnosticRunError(ValueError):
    """Raised when a frozen parent result cannot be extended safely."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic-protocol", type=Path, required=True)
    parser.add_argument("--parent-protocol", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--parent-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-commit", required=True)
    arguments = parser.parse_args()

    output_dir = arguments.output.expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise DiagnosticRunError("output directory must be empty")
    if not _is_commit(arguments.code_commit):
        raise DiagnosticRunError("code_commit must be a 40-character lowercase git SHA")
    output_dir.mkdir(parents=True, exist_ok=True)

    protocol_path = arguments.diagnostic_protocol.expanduser().resolve()
    parent_protocol = _load_json(
        arguments.parent_protocol.expanduser().resolve(), "parent protocol"
    )
    diagnostic_protocol = _load_json(protocol_path, "diagnostic protocol")
    _validate_diagnostic_protocol(
        diagnostic_protocol=diagnostic_protocol, parent_protocol=parent_protocol
    )
    snapshot_dir = arguments.snapshot.expanduser().resolve()
    snapshot = _load_json(snapshot_dir / "manifest.json", "snapshot manifest")
    _validate_snapshot(
        protocol=parent_protocol,
        snapshot=snapshot,
        snapshot_dir=snapshot_dir,
    )
    parent_summary_path = arguments.parent_summary.expanduser().resolve()
    parent_summary = _load_json(parent_summary_path, "parent summary")
    _validate_parent_result(
        diagnostic_protocol=diagnostic_protocol,
        parent_summary=parent_summary,
        parent_summary_path=parent_summary_path,
        snapshot=snapshot,
    )
    bars_by_symbol = _load_bars(
        protocol=parent_protocol,
        snapshot=snapshot,
        snapshot_dir=snapshot_dir,
    )
    result = _diagnostics(
        diagnostic_protocol=diagnostic_protocol,
        parent_protocol=parent_protocol,
        bars_by_symbol=bars_by_symbol,
        parent_summary=parent_summary,
        code_commit=arguments.code_commit,
    )
    output_path = output_dir / "diagnostics.json"
    output_path.write_text(_canonical_json(result) + "\n", encoding="utf-8")
    report_path = output_dir / "diagnostics.md"
    report_path.write_text(_report(result=result) + "\n", encoding="utf-8")
    receipt = {
        "classification": "provisional_nonpromotion_diagnostics_only",
        "diagnostic_protocol_sha256": _digest_file(protocol_path),
        "parent_summary_sha256": _digest_file(parent_summary_path),
        "diagnostics_sha256": _digest_file(output_path),
        "diagnostics_report_sha256": _digest_file(report_path),
        "code_commit": arguments.code_commit,
        "ledger_write_performed": False,
        "holdout_action_performed": False,
        "candidate_state_transition_performed": False,
    }
    (output_dir / "receipt.json").write_text(
        _canonical_json(receipt) + "\n",
        encoding="utf-8",
    )
    return 0


def _validate_diagnostic_protocol(
    *, diagnostic_protocol: dict[str, Any], parent_protocol: dict[str, Any]
) -> None:
    if diagnostic_protocol.get("classification") != "provisional_nonpromotion_diagnostics_only":
        raise DiagnosticRunError("diagnostic protocol must be non-promotion")
    parent = diagnostic_protocol.get("parent_result")
    if not isinstance(parent, dict):
        raise DiagnosticRunError("diagnostic protocol parent_result is missing")
    if parent.get("protocol_id") != parent_protocol.get("protocol_id"):
        raise DiagnosticRunError("diagnostic protocol parent identity differs")
    if parent.get("protocol_sha256") != _sha256_json(parent_protocol):
        raise DiagnosticRunError("diagnostic protocol parent hash differs")
    forbidden = diagnostic_protocol.get("forbidden_actions")
    required = {
        "modify parent candidate parameters, universe, folds, costs, or return timing",
        "change candidate state beyond development",
        "create, retrieve, seal, approve, or open a holdout",
        "evaluate options or create paper or live orders",
    }
    if not isinstance(forbidden, list) or not required.issubset(set(forbidden)):
        raise DiagnosticRunError("diagnostic protocol is missing required forbidden actions")


def _validate_parent_result(
    *,
    diagnostic_protocol: dict[str, Any],
    parent_summary: dict[str, Any],
    parent_summary_path: Path,
    snapshot: dict[str, Any],
) -> None:
    parent = diagnostic_protocol["parent_result"]
    if _digest_file(parent_summary_path) != parent.get("summary_sha256"):
        raise DiagnosticRunError("parent summary hash differs from diagnostic protocol")
    if parent_summary.get("classification") != "provisional_nonpromotion_falsification_only":
        raise DiagnosticRunError("parent summary is not a provisional falsification result")
    if parent_summary.get("snapshot_id") != parent.get("snapshot_id"):
        raise DiagnosticRunError("parent summary snapshot identity differs")
    if parent_summary.get("snapshot_content_sha256") != snapshot.get("content_sha256"):
        raise DiagnosticRunError("parent summary snapshot hash differs")
    if parent_summary.get("candidate_state") != "development_only_no_promotion_attempted":
        raise DiagnosticRunError("parent result does not have the expected development-only state")


def _diagnostics(
    *,
    diagnostic_protocol: dict[str, Any],
    parent_protocol: dict[str, Any],
    bars_by_symbol: dict[str, tuple[DailyBar, ...]],
    parent_summary: dict[str, Any],
    code_commit: str,
) -> dict[str, Any]:
    evaluation = parent_protocol["evaluation"]
    first_index = int(evaluation["first_evaluation_bar_index"])
    return_count = int(evaluation["fold_count"]) * int(evaluation["bars_per_fold"])
    last_index = first_index + return_count - 1
    portfolio_tracks = _parent_tracks(
        parent_protocol=parent_protocol,
        bars_by_symbol=bars_by_symbol,
        parent_summary=parent_summary,
    )
    candidate_returns = portfolio_tracks["candidate_h01_h04"]
    trend_returns = portfolio_tracks["baseline_trend_only"]
    equal_returns = portfolio_tracks["baseline_equal_weight_buy_and_hold"]
    spy_returns = tuple(
        _return(bars_by_symbol["SPY"][index], bars_by_symbol["SPY"][index + 1])
        for index in range(first_index, last_index + 1)
    )
    cash_plus_spy = tuple(Decimal("0.60") * value for value in spy_returns)
    volatility_matched_equal = _volatility_matched_equal_weight(
        parent_protocol=parent_protocol,
        bars_by_symbol=bars_by_symbol,
        first_index=first_index,
        last_index=last_index,
    )
    asset_level = _asset_level(
        parent_protocol=parent_protocol,
        bars_by_symbol=bars_by_symbol,
        first_index=first_index,
        last_index=last_index,
    )
    return {
        "classification": "provisional_nonpromotion_diagnostics_only",
        "diagnostic_protocol_id": diagnostic_protocol["protocol_id"],
        "diagnostic_protocol_sha256": _sha256_json(diagnostic_protocol),
        "parent_result": diagnostic_protocol["parent_result"],
        "code_commit": code_commit,
        "read_only_controls": {
            "ledger_write_performed": False,
            "candidate_state_transition_performed": False,
            "holdout_action_performed": False,
            "options_evaluation_performed": False,
            "paper_or_live_orders_created": False,
        },
        "benchmark_alignment": {
            "spy_total_return_proxy": _metrics(spy_returns, _zero_turnover(spy_returns)),
            "cash_plus_spy_60_40_daily_rebalanced": _metrics(
                cash_plus_spy,
                _zero_turnover(cash_plus_spy),
            ),
            "volatility_matched_equal_weight": _metrics(
                volatility_matched_equal,
                _zero_turnover(volatility_matched_equal),
            ),
        },
        "return_attribution": _attribution(
            candidate_returns=candidate_returns,
            trend_returns=trend_returns,
            equal_returns=equal_returns,
            spy_returns=spy_returns,
            parent_summary=parent_summary,
        ),
        "asset_level": asset_level,
        "data_adjustment_boundary": diagnostic_protocol["data_adjustment_boundary"],
    }


def _parent_tracks(
    *,
    parent_protocol: dict[str, Any],
    bars_by_symbol: dict[str, tuple[DailyBar, ...]],
    parent_summary: dict[str, Any],
) -> dict[str, tuple[Decimal, ...]]:
    required = {
        "candidate_h01_h04",
        "baseline_trend_only",
        "baseline_equal_weight_buy_and_hold",
    }
    retained = parent_summary.get("portfolios")
    if not isinstance(retained, list):
        raise DiagnosticRunError("parent summary portfolios are missing")
    retained_metrics = {
        str(item["name"]): item.get("base_metrics")
        for item in retained
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    reconstructed = {
        track.name: track
        for track in _evaluate(protocol=parent_protocol, bars_by_symbol=bars_by_symbol)
    }
    if not required.issubset(reconstructed) or not required.issubset(retained_metrics):
        raise DiagnosticRunError("parent summary lacks a required retained portfolio")
    tracks: dict[str, tuple[Decimal, ...]] = {}
    for name in sorted(required):
        track = reconstructed[name]
        metrics = retained_metrics[name]
        if not isinstance(metrics, dict) or _metrics(track.returns, track.turnovers) != metrics:
            raise DiagnosticRunError(
                "reconstructed parent track does not match the retained result"
            )
        tracks[name] = track.returns
    lengths = {len(track) for track in tracks.values()}
    if lengths != {504}:
        raise DiagnosticRunError("reconstructed parent return tracks have an unexpected length")
    return tracks


def _volatility_matched_equal_weight(
    *,
    parent_protocol: dict[str, Any],
    bars_by_symbol: dict[str, tuple[DailyBar, ...]],
    first_index: int,
    last_index: int,
) -> tuple[Decimal, ...]:
    specification = parent_protocol["candidate"]
    window = int(specification["volatility_window_bars"])
    minimum_observations = int(specification["minimum_volatility_observations"])
    target = Decimal(str(specification["target_annualized_volatility"]))
    lower = Decimal(str(specification["minimum_exposure_multiplier"]))
    upper = Decimal(str(specification["maximum_exposure_multiplier"]))
    symbols = tuple(str(value) for value in parent_protocol["data"]["universe"])
    equal_weight_returns = _equal_weight_rebalanced_returns(
        symbols=symbols,
        bars_by_symbol=bars_by_symbol,
        last_index=last_index,
    )
    output: list[Decimal] = []
    for index in range(first_index, last_index + 1):
        history = tuple(
            equal_weight_returns[position] for position in range(index - window + 1, index + 1)
        )
        if len(history) < minimum_observations:
            multiplier = _ONE
        else:
            realized = _annualized_volatility(history)
            multiplier = _ONE if realized == _ZERO else target / realized
            multiplier = min(upper, max(lower, multiplier))
        output.append(multiplier * equal_weight_returns[index + 1])
    return tuple(output)


def _equal_weight_rebalanced_returns(
    *,
    symbols: tuple[str, ...],
    bars_by_symbol: dict[str, tuple[DailyBar, ...]],
    last_index: int,
) -> tuple[Decimal, ...]:
    return tuple(
        sum(
            (
                _return(bars_by_symbol[symbol][index - 1], bars_by_symbol[symbol][index])
                / Decimal(len(symbols))
                for symbol in symbols
            ),
            _ZERO,
        )
        for index in range(1, last_index + 2)
    )


def _asset_level(
    *,
    parent_protocol: dict[str, Any],
    bars_by_symbol: dict[str, tuple[DailyBar, ...]],
    first_index: int,
    last_index: int,
) -> list[dict[str, Any]]:
    candidate = parent_protocol["candidate"]
    results = []
    for symbol in tuple(str(value) for value in parent_protocol["data"]["universe"]):
        candidate_returns: list[Decimal] = []
        buy_hold_returns: list[Decimal] = []
        for index in range(first_index, last_index + 1):
            weight = _candidate_weights(
                symbols=(symbol,),
                bars_by_symbol=bars_by_symbol,
                index=index,
                lookback=int(candidate["lookback_bars"]),
                volatility_window=int(candidate["volatility_window_bars"]),
                minimum_observations=int(candidate["minimum_volatility_observations"]),
                target_volatility=Decimal(str(candidate["target_annualized_volatility"])),
                multiplier_minimum=Decimal(str(candidate["minimum_exposure_multiplier"])),
                multiplier_maximum=Decimal(str(candidate["maximum_exposure_multiplier"])),
            )[symbol]
            one_day_return = _return(
                bars_by_symbol[symbol][index], bars_by_symbol[symbol][index + 1]
            )
            candidate_returns.append(weight * one_day_return)
            buy_hold_returns.append(one_day_return)
        results.append(
            {
                "symbol": symbol,
                "h01_h04_single_asset_sleeve": _metrics(
                    tuple(candidate_returns),
                    _zero_turnover(tuple(candidate_returns)),
                ),
                "buy_and_hold_adjusted_price_proxy": _metrics(
                    tuple(buy_hold_returns),
                    _zero_turnover(tuple(buy_hold_returns)),
                ),
            }
        )
    return results


def _attribution(
    *,
    candidate_returns: tuple[Decimal, ...],
    trend_returns: tuple[Decimal, ...],
    equal_returns: tuple[Decimal, ...],
    spy_returns: tuple[Decimal, ...],
    parent_summary: dict[str, Any],
) -> dict[str, Any]:
    candidate_metrics = _metrics(candidate_returns, _zero_turnover(candidate_returns))
    trend_metrics = _metrics(trend_returns, _zero_turnover(trend_returns))
    equal_metrics = _metrics(equal_returns, _zero_turnover(equal_returns))
    spy_metrics = _metrics(spy_returns, _zero_turnover(spy_returns))
    costs = _cost_cumulative(parent_summary=parent_summary, portfolio_name="candidate_h01_h04")
    return {
        "method": (
            "Non-additive descriptive return bridge; every cumulative difference is reported "
            "against its immediately preceding reference."
        ),
        "cumulative_return_bridge": {
            "market_reference_spy_total_return_proxy": spy_metrics["cumulative_return"],
            "equal_weight_allocation_relative_to_market": _difference(
                equal_metrics["cumulative_return"], spy_metrics["cumulative_return"]
            ),
            "trend_timing_relative_to_equal_weight": _difference(
                trend_metrics["cumulative_return"], equal_metrics["cumulative_return"]
            ),
            "volatility_scaling_relative_to_trend": _difference(
                candidate_metrics["cumulative_return"], trend_metrics["cumulative_return"]
            ),
            "candidate_relative_to_equal_weight": _difference(
                candidate_metrics["cumulative_return"], equal_metrics["cumulative_return"]
            ),
        },
        "market_beta_proxy": {
            "candidate_h01_h04_to_spy": _decimal_text(_beta(candidate_returns, spy_returns)),
            "trend_only_to_spy": _decimal_text(_beta(trend_returns, spy_returns)),
            "equal_weight_buy_and_hold_to_spy": _decimal_text(_beta(equal_returns, spy_returns)),
        },
        "turnover_cost_sensitivity": costs,
        "limitations": [
            (
                "No formal factor model, causal attribution, historical quotes, impact, borrow, "
                "financing, tax, or cash-rate input is available."
            ),
            (
                "The parent adjusted-close return construction may contain unobservable "
                "corporate-action timing leakage and cannot establish executable decision prices."
            ),
        ],
    }


def _cost_cumulative(*, parent_summary: dict[str, Any], portfolio_name: str) -> dict[str, str]:
    portfolios = parent_summary.get("portfolios")
    if not isinstance(portfolios, list):
        raise DiagnosticRunError("parent summary portfolios are missing for cost sensitivity")
    for portfolio in portfolios:
        if isinstance(portfolio, dict) and portfolio.get("name") == portfolio_name:
            sensitivities = portfolio.get("cost_sensitivities")
            if not isinstance(sensitivities, dict):
                raise DiagnosticRunError("parent summary cost sensitivities are missing")
            return {
                str(key): str(value["cumulative_return"])
                for key, value in sensitivities.items()
                if isinstance(value, dict) and isinstance(value.get("cumulative_return"), str)
            }
    raise DiagnosticRunError("parent candidate cost sensitivity is missing")


def _difference(left: str, right: str) -> str:
    return _decimal_text(Decimal(left) - Decimal(right))


def _beta(portfolio_returns: tuple[Decimal, ...], market_returns: tuple[Decimal, ...]) -> Decimal:
    if len(portfolio_returns) != len(market_returns) or len(portfolio_returns) < 2:
        raise DiagnosticRunError("beta requires aligned return tracks")
    portfolio_values = tuple(float(value) for value in portfolio_returns)
    market_values = tuple(float(value) for value in market_returns)
    portfolio_mean = fmean(portfolio_values)
    market_mean = fmean(market_values)
    market_variance = sum((value - market_mean) ** 2 for value in market_values)
    if market_variance == 0.0:
        raise DiagnosticRunError("beta market variance is zero")
    covariance = sum(
        (portfolio - portfolio_mean) * (market - market_mean)
        for portfolio, market in zip(portfolio_values, market_values, strict=True)
    )
    return Decimal(str(covariance / market_variance))


def _zero_turnover(returns: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    return tuple(_ZERO for _ in returns)


def _report(*, result: dict[str, Any]) -> str:
    alignment = result["benchmark_alignment"]
    attribution = result["return_attribution"]
    rows = [
        _metrics_row("SPY total-return proxy", alignment["spy_total_return_proxy"]),
        _metrics_row(
            "60% SPY / 40% zero-return cash", alignment["cash_plus_spy_60_40_daily_rebalanced"]
        ),
        _metrics_row(
            "Volatility-matched equal weight", alignment["volatility_matched_equal_weight"]
        ),
    ]
    asset_rows = [
        "| {symbol} | {candidate} | {buy_hold} |".format(
            symbol=item["symbol"],
            candidate=item["h01_h04_single_asset_sleeve"]["cumulative_return"],
            buy_hold=item["buy_and_hold_adjusted_price_proxy"]["cumulative_return"],
        )
        for item in result["asset_level"]
    ]
    bridge = attribution["cumulative_return_bridge"]
    return "\n".join(
        [
            "# Provisional Daily-Equity Independent Diagnostics",
            "",
            (
                "> **Non-promotion diagnostics only.** These calculations do not modify the "
                "parent candidate or ledger, register a candidate, open a holdout, evaluate "
                "options, or create paper/live orders."
            ),
            "",
            "## Benchmark Alignment",
            "",
            (
                "| Benchmark | Cumulative return | Annualized return | Annualized volatility | "
                "Maximum drawdown |"
            ),
            "|---|---:|---:|---:|---:|",
            *rows,
            "",
            "## Descriptive Return Bridge",
            "",
            "| Component | Cumulative return or difference |",
            "|---|---:|",
            f"| SPY market reference | {bridge['market_reference_spy_total_return_proxy']} |",
            (
                "| Equal-weight allocation relative to SPY | "
                f"{bridge['equal_weight_allocation_relative_to_market']} |"
            ),
            (
                "| Trend timing relative to equal weight | "
                f"{bridge['trend_timing_relative_to_equal_weight']} |"
            ),
            (
                "| Volatility scaling relative to trend | "
                f"{bridge['volatility_scaling_relative_to_trend']} |"
            ),
            (
                "| Candidate relative to equal weight | "
                f"{bridge['candidate_relative_to_equal_weight']} |"
            ),
            "",
            "## Asset-Level Diagnostic",
            "",
            (
                "| ETF | H01/H04 single-asset sleeve cumulative return | "
                "Buy-and-hold proxy cumulative return |"
            ),
            "|---|---:|---:|",
            *asset_rows,
            "",
            (
                "The adjusted-price decision safety remains **unverified**. Retrospective adjusted "
                "closes may incorporate distribution or split information whose availability time "
                "is not retained; no executable decision-price claim is made."
            ),
        ]
    )


def _metrics_row(name: str, metrics: dict[str, str]) -> str:
    return (
        "| {name} | {cumulative_return} | {annualized_return} | "
        "{annualized_volatility} | {maximum_drawdown} |"
    ).format(name=name, **metrics)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def _sha256_json(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _is_commit(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


if __name__ == "__main__":
    raise SystemExit(main())
