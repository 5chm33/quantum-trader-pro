"""Run the frozen provisional daily-equity H01/H04 falsification diagnostic.

This runner is deliberately restricted to a private, non-promotion adjusted-price snapshot.
It records a development-stage comparison in the immutable experiment ledger, but it never
moves a candidate beyond development, seals no holdout, and creates no order or broker call.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from statistics import stdev
from typing import Any

from quantum_trader.adapters.sqlite_experiment_ledger import SQLiteExperimentLedger
from quantum_trader.domain.experiments import (
    ArtifactRecord,
    AttemptRegistration,
    AttemptStage,
    CampaignRegistration,
    CandidateRegistration,
    PreregistrationFreeze,
    ResearchState,
    sha256_json,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


class ProvisionalRunError(ValueError):
    """Raised when the frozen provisional protocol or snapshot cannot be evaluated safely."""


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One adjusted-close observation retained in the frozen prototype snapshot."""

    timestamp: datetime
    adjusted_close: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioTrack:
    """One deterministic provisional portfolio return and turnover track."""

    name: str
    returns: tuple[Decimal, ...]
    turnovers: tuple[Decimal, ...]
    timestamps: tuple[datetime, ...]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-commit", required=True)
    arguments = parser.parse_args()

    protocol_path = arguments.protocol.expanduser().resolve()
    snapshot_dir = arguments.snapshot.expanduser().resolve()
    output_dir = arguments.output.expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ProvisionalRunError("output directory must be empty")
    if not _is_commit(arguments.code_commit):
        raise ProvisionalRunError("code_commit must be a 40-character lowercase git SHA")
    output_dir.mkdir(parents=True, exist_ok=True)

    protocol = _load_json(protocol_path, "protocol")
    _validate_protocol(protocol)
    snapshot = _load_json(snapshot_dir / "manifest.json", "snapshot manifest")
    _validate_snapshot(protocol=protocol, snapshot=snapshot, snapshot_dir=snapshot_dir)
    bars_by_symbol = _load_bars(protocol=protocol, snapshot=snapshot, snapshot_dir=snapshot_dir)
    tracks = _evaluate(protocol=protocol, bars_by_symbol=bars_by_symbol)
    result = _result(protocol=protocol, snapshot=snapshot, tracks=tracks)
    result_path = output_dir / "summary.json"
    result_path.write_text(_canonical_json(result) + "\n", encoding="utf-8")
    report_path = output_dir / "report.md"
    report_path.write_text(_report(result=result) + "\n", encoding="utf-8")
    _record_ledger(
        output_dir=output_dir,
        protocol=protocol,
        snapshot=snapshot,
        code_commit=arguments.code_commit,
        result_path=result_path,
        report_path=report_path,
    )
    return 0


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvisionalRunError(f"{label} cannot be read as JSON") from exc
    if not isinstance(raw, dict):
        raise ProvisionalRunError(f"{label} must be a JSON object")
    return raw


def _validate_protocol(protocol: dict[str, Any]) -> None:
    if protocol.get("classification") != "provisional_nonpromotion_falsification_only":
        raise ProvisionalRunError("protocol is not provisional non-promotion")
    candidate = protocol.get("candidate")
    evaluation = protocol.get("evaluation")
    if not isinstance(candidate, dict) or not isinstance(evaluation, dict):
        raise ProvisionalRunError("protocol candidate or evaluation section is missing")
    if candidate.get("family_ids") != ["H01", "H04"]:
        raise ProvisionalRunError("protocol must remain limited to H01/H04")
    if candidate.get("lookback_bars") != 252 or candidate.get("volatility_window_bars") != 63:
        raise ProvisionalRunError("protocol forecast windows differ from the frozen configuration")
    if candidate.get("rebalance_frequency_bars") != 21:
        raise ProvisionalRunError(
            "protocol rebalance frequency differs from the frozen configuration"
        )
    if evaluation.get("fold_count") != 4 or evaluation.get("bars_per_fold") != 126:
        raise ProvisionalRunError("protocol fold plan differs from the frozen configuration")
    forbidden = protocol.get("forbidden_actions")
    if (
        not isinstance(forbidden, list)
        or "candidate state transition beyond development" not in forbidden
    ):
        raise ProvisionalRunError("protocol must explicitly prohibit candidate promotion")


def _validate_snapshot(
    *, protocol: dict[str, Any], snapshot: dict[str, Any], snapshot_dir: Path
) -> None:
    if snapshot.get("classification") != "provisional_nonpromotion_falsification_only":
        raise ProvisionalRunError("snapshot is not provisional non-promotion")
    if snapshot.get("protocol_id") != protocol.get("protocol_id"):
        raise ProvisionalRunError("snapshot protocol identity differs")
    content_hash = snapshot.get("content_sha256")
    without_hash = {key: value for key, value in snapshot.items() if key != "content_sha256"}
    if not isinstance(content_hash, str) or content_hash != sha256_json(without_hash):
        raise ProvisionalRunError("snapshot content hash is invalid")
    scope = snapshot.get("admission_scope")
    if not isinstance(scope, dict) or scope.get("not_a_holdout") is not True:
        raise ProvisionalRunError("snapshot must explicitly state that it is not a holdout")
    if scope.get("excluded_from") != protocol["data"]["excluded_from"]:
        raise ProvisionalRunError("snapshot exclusion boundary differs from protocol")
    if not (snapshot_dir / "data").is_dir():
        raise ProvisionalRunError("snapshot data directory is missing")


def _load_bars(
    *, protocol: dict[str, Any], snapshot: dict[str, Any], snapshot_dir: Path
) -> dict[str, tuple[DailyBar, ...]]:
    assets = snapshot.get("assets")
    if not isinstance(assets, list):
        raise ProvisionalRunError("snapshot assets are missing")
    receipts = {str(item["symbol"]): item for item in assets if isinstance(item, dict)}
    symbols = tuple(str(value) for value in protocol["data"]["universe"])
    if tuple(sorted(receipts)) != symbols:
        raise ProvisionalRunError("snapshot universe differs from protocol")
    excluded_from = _parse_time(str(protocol["data"]["excluded_from"]))
    bars_by_symbol: dict[str, tuple[DailyBar, ...]] = {}
    for symbol in symbols:
        receipt = receipts[symbol]
        filename = receipt.get("normalized_filename")
        if not isinstance(filename, str):
            raise ProvisionalRunError(f"{symbol}: normalized filename is missing")
        path = snapshot_dir / "data" / filename
        if _digest_file(path) != receipt.get("normalized_sha256"):
            raise ProvisionalRunError(f"{symbol}: normalized snapshot hash is invalid")
        bars_by_symbol[symbol] = _read_bars(path=path, symbol=symbol, excluded_from=excluded_from)
        if len(bars_by_symbol[symbol]) != receipt.get("row_count"):
            raise ProvisionalRunError(f"{symbol}: snapshot row count is invalid")
    timestamps = tuple(bar.timestamp for bar in bars_by_symbol[symbols[0]])
    if any(
        tuple(bar.timestamp for bar in bars_by_symbol[symbol]) != timestamps
        for symbol in symbols[1:]
    ):
        raise ProvisionalRunError("all provisional assets must have matching daily timestamps")
    return bars_by_symbol


def _read_bars(*, path: Path, symbol: str, excluded_from: datetime) -> tuple[DailyBar, ...]:
    try:
        handle = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise ProvisionalRunError(f"{symbol}: snapshot CSV is missing") from exc
    bars: list[DailyBar] = []
    with handle:
        reader = csv.DictReader(handle)
        if (
            reader.fieldnames is None
            or "datetime" not in reader.fieldnames
            or "adjusted_close" not in reader.fieldnames
        ):
            raise ProvisionalRunError(f"{symbol}: snapshot CSV has required columns missing")
        previous: datetime | None = None
        for row in reader:
            timestamp = _parse_time(str(row["datetime"]))
            if timestamp >= excluded_from:
                raise ProvisionalRunError(f"{symbol}: snapshot contains excluded rows")
            if previous is not None and timestamp <= previous:
                raise ProvisionalRunError(
                    f"{symbol}: snapshot timestamps are not strictly ascending"
                )
            try:
                adjusted_close = Decimal(str(row["adjusted_close"]))
            except Exception as exc:
                raise ProvisionalRunError(f"{symbol}: adjusted_close is invalid") from exc
            if not adjusted_close.is_finite() or adjusted_close <= _ZERO:
                raise ProvisionalRunError(f"{symbol}: adjusted_close must be finite and positive")
            bars.append(DailyBar(timestamp=timestamp, adjusted_close=adjusted_close))
            previous = timestamp
    if len(bars) < 757:
        raise ProvisionalRunError(f"{symbol}: insufficient rows for the frozen prototype folds")
    return tuple(bars)


def _evaluate(
    *, protocol: dict[str, Any], bars_by_symbol: dict[str, tuple[DailyBar, ...]]
) -> tuple[PortfolioTrack, ...]:
    candidate = protocol["candidate"]
    evaluation = protocol["evaluation"]
    symbols = tuple(str(value) for value in protocol["data"]["universe"])
    lookback = int(candidate["lookback_bars"])
    volatility_window = int(candidate["volatility_window_bars"])
    minimum_observations = int(candidate["minimum_volatility_observations"])
    target_volatility = Decimal(str(candidate["target_annualized_volatility"]))
    multiplier_minimum = Decimal(str(candidate["minimum_exposure_multiplier"]))
    multiplier_maximum = Decimal(str(candidate["maximum_exposure_multiplier"]))
    rebalance_frequency = int(candidate["rebalance_frequency_bars"])
    first_index = int(evaluation["first_evaluation_bar_index"])
    observation_count = int(evaluation["fold_count"]) * int(evaluation["bars_per_fold"])
    last_index = first_index + observation_count - 1
    if last_index + 1 >= len(bars_by_symbol[symbols[0]]):
        raise ProvisionalRunError("snapshot does not contain all frozen fold returns")

    tracks = {
        "candidate_h01_h04": _empty_track("candidate_h01_h04"),
        "baseline_trend_only": _empty_track("baseline_trend_only"),
        "baseline_equal_weight_buy_and_hold": _empty_track("baseline_equal_weight_buy_and_hold"),
        "baseline_cash": _empty_track("baseline_cash"),
    }
    candidate_weights = {symbol: _ZERO for symbol in symbols}
    trend_weights = {symbol: _ZERO for symbol in symbols}
    equal_weight_values = {symbol: _ONE / Decimal(len(symbols)) for symbol in symbols}
    for index in range(first_index, last_index + 1):
        if (index - first_index) % rebalance_frequency == 0:
            candidate_weights = _candidate_weights(
                symbols=symbols,
                bars_by_symbol=bars_by_symbol,
                index=index,
                lookback=lookback,
                volatility_window=volatility_window,
                minimum_observations=minimum_observations,
                target_volatility=target_volatility,
                multiplier_minimum=multiplier_minimum,
                multiplier_maximum=multiplier_maximum,
            )
            trend_weights = _trend_weights(
                symbols=symbols,
                bars_by_symbol=bars_by_symbol,
                index=index,
                lookback=lookback,
            )
        one_day_returns = {
            symbol: _return(bars_by_symbol[symbol][index], bars_by_symbol[symbol][index + 1])
            for symbol in symbols
        }
        timestamp = bars_by_symbol[symbols[0]][index + 1].timestamp
        _append_track(
            tracks["candidate_h01_h04"],
            daily_return=_weighted_return(candidate_weights, one_day_returns),
            turnover=_turnover_if_rebalanced(
                index=index,
                first_index=first_index,
                rebalance_frequency=rebalance_frequency,
                old_weights=tracks["candidate_h01_h04"].get("last_weights"),
                new_weights=candidate_weights,
            ),
            timestamp=timestamp,
            weights=candidate_weights,
        )
        _append_track(
            tracks["baseline_trend_only"],
            daily_return=_weighted_return(trend_weights, one_day_returns),
            turnover=_turnover_if_rebalanced(
                index=index,
                first_index=first_index,
                rebalance_frequency=rebalance_frequency,
                old_weights=tracks["baseline_trend_only"].get("last_weights"),
                new_weights=trend_weights,
            ),
            timestamp=timestamp,
            weights=trend_weights,
        )
        equal_weight_return = _weighted_return(equal_weight_values, one_day_returns)
        _append_track(
            tracks["baseline_equal_weight_buy_and_hold"],
            daily_return=equal_weight_return,
            turnover=_ZERO,
            timestamp=timestamp,
            weights=equal_weight_values,
        )
        denominator = _ONE + equal_weight_return
        equal_weight_values = {
            symbol: equal_weight_values[symbol] * (_ONE + one_day_returns[symbol]) / denominator
            for symbol in symbols
        }
        _append_track(
            tracks["baseline_cash"],
            daily_return=_ZERO,
            turnover=_ZERO,
            timestamp=timestamp,
            weights={symbol: _ZERO for symbol in symbols},
        )
    return tuple(_freeze_track(track) for track in tracks.values())


def _empty_track(name: str) -> dict[str, Any]:
    return {"name": name, "returns": [], "turnovers": [], "timestamps": [], "last_weights": None}


def _candidate_weights(
    *,
    symbols: tuple[str, ...],
    bars_by_symbol: dict[str, tuple[DailyBar, ...]],
    index: int,
    lookback: int,
    volatility_window: int,
    minimum_observations: int,
    target_volatility: Decimal,
    multiplier_minimum: Decimal,
    multiplier_maximum: Decimal,
) -> dict[str, Decimal]:
    weights: dict[str, Decimal] = {}
    for symbol in symbols:
        bars = bars_by_symbol[symbol]
        trend = _sign(_return(bars[index - lookback], bars[index]))
        returns = tuple(
            _return(bars[position - 1], bars[position])
            for position in range(index - volatility_window + 1, index + 1)
        )
        if len(returns) < minimum_observations:
            multiplier = _ONE
        else:
            annualized_volatility = _annualized_volatility(returns)
            multiplier = (
                _ONE
                if annualized_volatility == _ZERO
                else target_volatility / annualized_volatility
            )
            multiplier = min(multiplier_maximum, max(multiplier_minimum, multiplier))
        weights[symbol] = trend * multiplier / Decimal(len(symbols))
    return weights


def _trend_weights(
    *,
    symbols: tuple[str, ...],
    bars_by_symbol: dict[str, tuple[DailyBar, ...]],
    index: int,
    lookback: int,
) -> dict[str, Decimal]:
    return {
        symbol: _sign(
            _return(bars_by_symbol[symbol][index - lookback], bars_by_symbol[symbol][index])
        )
        / Decimal(len(symbols))
        for symbol in symbols
    }


def _return(start: DailyBar, end: DailyBar) -> Decimal:
    return end.adjusted_close / start.adjusted_close - _ONE


def _sign(value: Decimal) -> Decimal:
    if value > _ZERO:
        return _ONE
    if value < _ZERO:
        return Decimal("-1")
    return _ZERO


def _annualized_volatility(returns: tuple[Decimal, ...]) -> Decimal:
    if len(returns) < 2:
        return _ZERO
    with localcontext() as context:
        context.prec = 40
        values = [float(value) for value in returns]
        return Decimal(str(stdev(values))) * Decimal(252).sqrt()


def _weighted_return(weights: dict[str, Decimal], returns: dict[str, Decimal]) -> Decimal:
    return sum((weights[symbol] * returns[symbol] for symbol in weights), _ZERO)


def _turnover_if_rebalanced(
    *,
    index: int,
    first_index: int,
    rebalance_frequency: int,
    old_weights: dict[str, Decimal] | None,
    new_weights: dict[str, Decimal],
) -> Decimal:
    if (index - first_index) % rebalance_frequency != 0 or old_weights is None:
        return _ZERO
    return sum((abs(new_weights[symbol] - old_weights[symbol]) for symbol in new_weights), _ZERO)


def _append_track(
    track: dict[str, Any],
    *,
    daily_return: Decimal,
    turnover: Decimal,
    timestamp: datetime,
    weights: dict[str, Decimal],
) -> None:
    track["returns"].append(daily_return)
    track["turnovers"].append(turnover)
    track["timestamps"].append(timestamp)
    track["last_weights"] = dict(weights)


def _freeze_track(track: dict[str, Any]) -> PortfolioTrack:
    return PortfolioTrack(
        name=str(track["name"]),
        returns=tuple(track["returns"]),
        turnovers=tuple(track["turnovers"]),
        timestamps=tuple(track["timestamps"]),
    )


def _result(
    *, protocol: dict[str, Any], snapshot: dict[str, Any], tracks: tuple[PortfolioTrack, ...]
) -> dict[str, Any]:
    costs = tuple(
        Decimal(value)
        for value in protocol["return_and_cost_convention"]["cost_scenarios_bps_per_one_way_turn"]
    )
    fold_count = int(protocol["evaluation"]["fold_count"])
    bars_per_fold = int(protocol["evaluation"]["bars_per_fold"])
    return {
        "classification": "provisional_nonpromotion_falsification_only",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": _sha256_json(protocol),
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_content_sha256": snapshot["content_sha256"],
        "candidate_state": "development_only_no_promotion_attempted",
        "forbidden_outcomes": [
            "strategy_grade",
            "candidate_promotion",
            "holdout_access",
            "options_validation",
            "paper_trading",
            "live_trading",
        ],
        "evaluation": {
            "return_count": len(tracks[0].returns),
            "fold_count": fold_count,
            "bars_per_fold": bars_per_fold,
            "unused_tail_is_not_holdout": True,
        },
        "portfolios": [
            _portfolio_summary(track=track, costs=costs, bars_per_fold=bars_per_fold)
            for track in tracks
        ],
    }


def _portfolio_summary(
    *, track: PortfolioTrack, costs: tuple[Decimal, ...], bars_per_fold: int
) -> dict[str, Any]:
    return {
        "name": track.name,
        "base_metrics": _metrics(track.returns, track.turnovers),
        "cost_sensitivities": {
            f"{cost_bps}_bps_one_way_turn": _metrics(
                tuple(
                    value - cost_bps / Decimal(10_000) * turnover
                    for value, turnover in zip(track.returns, track.turnovers, strict=True)
                ),
                track.turnovers,
            )
            for cost_bps in costs
        },
        "fold_metrics": [
            _metrics(
                track.returns[offset : offset + bars_per_fold],
                track.turnovers[offset : offset + bars_per_fold],
            )
            for offset in range(0, len(track.returns), bars_per_fold)
        ],
    }


def _metrics(returns: tuple[Decimal, ...], turnovers: tuple[Decimal, ...]) -> dict[str, str]:
    if not returns:
        raise ProvisionalRunError("metrics require returns")
    growth = _ONE
    maximum_value = _ONE
    maximum_drawdown = _ZERO
    for value in returns:
        growth *= _ONE + value
        maximum_value = max(maximum_value, growth)
        maximum_drawdown = min(maximum_drawdown, growth / maximum_value - _ONE)
    annualized_return = growth ** (Decimal(252) / Decimal(len(returns))) - _ONE
    annualized_volatility = _annualized_volatility(returns)
    return {
        "cumulative_return": _decimal_text(growth - _ONE),
        "annualized_return": _decimal_text(annualized_return),
        "annualized_volatility": _decimal_text(annualized_volatility),
        "maximum_drawdown": _decimal_text(maximum_drawdown),
        "annualized_turnover": _decimal_text(
            sum(turnovers, _ZERO) * Decimal(252) / Decimal(len(turnovers))
        ),
    }


def _record_ledger(
    *,
    output_dir: Path,
    protocol: dict[str, Any],
    snapshot: dict[str, Any],
    code_commit: str,
    result_path: Path,
    report_path: Path,
) -> None:
    now = datetime.now(UTC)
    campaign_id = "qtpro.provisional.daily-equity.v1"
    candidate_id = "qtpro.h01h04.provisional.001"
    protocol_hash = _sha256_json(protocol)
    specification = protocol["candidate"]
    ledger = SQLiteExperimentLedger((output_dir / "experiment_ledger.sqlite").resolve())
    ledger.register_campaign(
        CampaignRegistration(
            campaign_id=campaign_id,
            governance_policy_sha256=_digest_file(
                Path("research/governance/strategy_grade_policy_v1.json").resolve()
            ),
            hypothesis_catalog_sha256=_digest_file(
                Path("research/governance/hypothesis_catalog_v1.json").resolve()
            ),
            data_contract_manifest_sha256=_digest_file(
                Path("research/schemas/schema_manifest_v1.json").resolve()
            ),
            baseline_commit=code_commit,
            registered_at=now,
        ),
        actor="provisional_daily_equity_runner",
    )
    ledger.register_candidate(
        CandidateRegistration(
            candidate_id=candidate_id,
            campaign_id=campaign_id,
            family_id="H01",
            candidate_index=1,
            candidate_ceiling=6,
            specification_sha256=_sha256_json(specification),
            code_commit=code_commit,
            registered_at=now,
        ),
        actor="provisional_daily_equity_runner",
    )
    ledger.transition_candidate(
        candidate_id,
        actor="provisional_daily_equity_runner",
        target=ResearchState.DEVELOPMENT,
        occurred_at=now,
        gate_evidence_sha256=protocol_hash,
    )
    ledger.freeze_preregistration(
        PreregistrationFreeze(
            candidate_id=candidate_id,
            protocol_id=str(protocol["protocol_id"]),
            protocol_sha256=protocol_hash,
            data_snapshot_id=str(snapshot["snapshot_id"]),
            data_snapshot_manifest_sha256=str(snapshot["content_sha256"]),
            partition_plan_sha256=_sha256_json(protocol["evaluation"]),
            benchmark_set_sha256=_sha256_json(protocol["permanent_baselines"]),
            cost_model_set_sha256=_sha256_json(protocol["return_and_cost_convention"]),
            candidate_budget_sha256=_sha256_json({"family_id": "H01", "ceiling": 6}),
            frozen_at=now,
        ),
        actor="provisional_daily_equity_runner",
    )
    comparison_group_id = "qtpro.provisional.daily-equity.v1.comparison.001"
    portfolios = (
        "candidate_h01_h04",
        "baseline_equal_weight_buy_and_hold",
        "baseline_trend_only",
        "baseline_cash",
    )
    for portfolio in portfolios:
        attempt_id = f"qtpro.provisional.{portfolio}.001"
        ledger.register_attempt(
            AttemptRegistration(
                attempt_id=attempt_id,
                candidate_id=candidate_id,
                comparison_group_id=comparison_group_id,
                stage=AttemptStage.DEVELOPMENT,
                protocol_id=str(protocol["protocol_id"]),
                data_snapshot_id=str(snapshot["snapshot_id"]),
                partition_id="four-fixed-126-bar-folds",
                code_commit=code_commit,
                configuration_sha256=_sha256_json(
                    {"portfolio": portfolio, "protocol": protocol["candidate"]}
                ),
                benchmark_set_sha256=_sha256_json(protocol["permanent_baselines"]),
                cost_model_sha256=_sha256_json(protocol["return_and_cost_convention"]),
                inference_plan_sha256=_sha256_json({"mode": "descriptive_falsification_only"}),
                registered_at=now,
            ),
            actor="provisional_daily_equity_runner",
        )
        ledger.start_attempt(attempt_id, actor="provisional_daily_equity_runner", started_at=now)
        artifact_path = result_path if portfolio == "candidate_h01_h04" else report_path
        artifact = ArtifactRecord(
            artifact_id=f"qtpro.provisional.artifact.{portfolio}.001",
            attempt_id=attempt_id,
            name=artifact_path.name,
            sha256=_digest_file(artifact_path),
            byte_count=artifact_path.stat().st_size,
            media_type="application/json" if artifact_path.suffix == ".json" else "text/markdown",
            role="provisional_descriptive_result",
            license_class="private",
            retained_at=now,
        )
        ledger.complete_attempt(
            attempt_id,
            actor="provisional_daily_equity_runner",
            completed_at=now,
            result_summary_sha256=_digest_file(result_path),
            artifacts=(artifact,),
        )
    ledger.open_comparison(
        comparison_group_id,
        actor="provisional_daily_equity_runner",
        opened_at=now,
    )
    ledger.verify_integrity()


def _report(*, result: dict[str, Any]) -> str:
    rows = []
    for portfolio in result["portfolios"]:
        metrics = portfolio["base_metrics"]
        rows.append(
            "| {name} | {cumulative_return} | {annualized_return} | "
            "{annualized_volatility} | {maximum_drawdown} | {annualized_turnover} |".format(
                name=portfolio["name"], **metrics
            )
        )
    return "\n".join(
        [
            "# Provisional Daily-Equity Falsification Result",
            "",
            (
                "> **Non-promotion diagnostic only.** This private adjusted-price prototype "
                "cannot establish alpha, promote a candidate, open a holdout, validate options, "
                "or authorize paper/live trading."
            ),
            "",
            f"Snapshot: `{result['snapshot_id']}` (`{result['snapshot_content_sha256']}`).",
            "",
            (
                "| Portfolio | Cumulative return | Annualized return | Annualized volatility | "
                "Maximum drawdown | Annualized turnover |"
            ),
            "|---|---:|---:|---:|---:|---:|",
            *rows,
            "",
            (
                "The retained JSON includes zero-, five-, and fifteen-basis-point one-way-turn "
                "cost sensitivities plus four fixed descriptive folds. These are falsification "
                "diagnostics, not significance tests or selection evidence."
            ),
        ]
    )


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProvisionalRunError("timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProvisionalRunError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0000000001")), "f")


def _is_commit(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


if __name__ == "__main__":
    raise SystemExit(main())
