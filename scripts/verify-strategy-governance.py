#!/usr/bin/env python3
"""Verify the frozen A+ strategy-research governance without external data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "research" / "governance" / "strategy_grade_policy_v1.json"
IDENTITIES_PATH = ROOT / "research" / "governance" / "baseline_identities.txt"
EVIDENCE_CHECKSUMS_PATH = ROOT / "research" / "governance" / "v1_evidence_checksums.txt"
HYPOTHESIS_CATALOG_PATH = ROOT / "research" / "governance" / "hypothesis_catalog_v1.json"
V1_PROTOCOL_PATH = ROOT / "evaluation" / "protocol_v1.json"
V1_EVIDENCE_ROOT = ROOT / "evaluation" / "results" / "v1-preholdout"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        key, value = raw_line.split("=", maxsplit=1)
        if key in values:
            raise ValueError(f"duplicate key in {path.name}: {key}")
        values[key] = value
    return values


def parse_checksum_manifest(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        expected, relative_name = raw_line.split(maxsplit=1)
        checksums[relative_name.strip()] = expected
    if not checksums:
        raise ValueError(f"empty checksum manifest: {path}")
    return checksums


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    policy: dict[str, Any] = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    identities = parse_key_values(IDENTITIES_PATH)
    evidence_checksums = parse_checksum_manifest(EVIDENCE_CHECKSUMS_PATH)
    catalog: dict[str, Any] = json.loads(HYPOTHESIS_CATALOG_PATH.read_text(encoding="utf-8"))
    baseline: dict[str, Any] = policy["baseline"]

    require(policy["policy_id"] == "qtpro-strategy-grade-v1", "unexpected policy ID")
    require(policy["policy_version"] == "1.0.0", "unexpected policy version")
    require(policy["score"]["a_plus_minimum"] >= 95.0, "A+ threshold cannot be below 95")
    require(sum(policy["score"]["weights"].values()) == 100, "strategy weights must total 100")
    require(policy["score"]["hard_gates_required"] is True, "A+ must require every hard gate")
    require(policy["score"]["caps"]["before_locked_holdout"] == "A-", "pre-holdout cap changed")
    require(
        policy["score"]["caps"]["before_authenticated_shadow_and_paper"] == "A",
        "pre-paper grade cap changed",
    )

    expected_identities = {
        "baseline_commit": baseline["main_commit"],
        "release_tag_commit": baseline["release_tag_commit"],
        "cloud_runtime_commit": baseline["cloud_runtime_commit"],
    }
    require(identities == expected_identities, "baseline identity file does not match policy")
    require(
        baseline["v1_protocol_sha256"] == digest(V1_PROTOCOL_PATH),
        "frozen v1 protocol digest does not match repository bytes",
    )
    for relative_name, expected in evidence_checksums.items():
        require(
            digest(ROOT / relative_name) == expected, f"frozen evidence mismatch: {relative_name}"
        )

    require(catalog["catalog_id"] == "qtpro-strategy-hypotheses-v1", "unexpected catalog ID")
    require(
        catalog["status"] == "literature_screened_not_preregistered",
        "hypothesis catalog cannot imply preregistration or promotion",
    )
    families: list[dict[str, Any]] = catalog["families"]
    expected_family_ids = {f"H{index:02d}" for index in range(1, 13)}
    observed_family_ids = {str(family["id"]) for family in families}
    require(len(families) == 12, "hypothesis family count must remain 12")
    require(observed_family_ids == expected_family_ids, "hypothesis family IDs changed")
    require(
        sum(int(family["candidate_ceiling"]) for family in families) == 56,
        "aggregate candidate ceiling changed",
    )
    require(
        all(1 <= int(family["candidate_ceiling"]) <= 6 for family in families),
        "family candidate ceiling exceeds the frozen range",
    )
    option_families = [family for family in families if str(family["id"]) >= "H08"]
    require(
        all(
            str(family["data_readiness"]).startswith("blocked_pending_point_in_time_options")
            for family in option_families
        ),
        "empirical options families must remain blocked pending point-in-time data",
    )
    catalog_rules: dict[str, Any] = catalog["rules"]
    required_true_catalog_rules = (
        "candidate_ceilings_are_upper_bounds",
        "all_attempts_must_enter_experiment_ledger",
        "hierarchical_multiple_testing_required",
        "provider_market_data_required_for_empirical_options_grade",
    )
    require(
        all(catalog_rules[name] is True for name in required_true_catalog_rules),
        "a required hypothesis catalog protection was disabled",
    )
    require(
        catalog_rules["indicative_options_quotes_for_grade"] is False,
        "indicative options quotes cannot support a strategy grade",
    )
    require(
        catalog_rules["live_execution_allowed"] is False,
        "live execution must remain unavailable in the hypothesis catalog",
    )

    require(baseline["v1_holdout_status"] == "locked_unopened", "v1 holdout status changed")
    forbidden_holdout_files = (
        "holdout_receipt.json",
        "holdout_results.csv",
        "holdout_selections.csv",
        "holdout_summary.json",
    )
    leaked = [name for name in forbidden_holdout_files if (V1_EVIDENCE_ROOT / name).exists()]
    require(not leaked, f"v1 holdout artifacts must remain absent: {', '.join(leaked)}")

    required_categories = set(policy["mandatory_gate_categories"])
    require(len(required_categories) >= 12, "mandatory gate categories were removed")
    allowed = set(policy["options_scope"]["initially_allowed"])
    prohibited = set(policy["options_scope"]["prohibited"])
    require(bool(allowed), "options research scope cannot be empty")
    require(
        not allowed.intersection(prohibited),
        "an options structure cannot be both allowed and prohibited",
    )
    require(
        policy["options_scope"]["evidence_requires_real_point_in_time_contract_data"] is True,
        "options evidence must require real point-in-time contract data",
    )
    require(
        "naked_short_option" in prohibited and "automated_0dte" in prohibited,
        "initial high-risk options prohibitions were weakened",
    )

    holdout: dict[str, Any] = policy["holdout"]
    require(all(holdout.values()), "every holdout protection must remain enabled")
    execution: dict[str, Any] = policy["execution"]
    require(
        execution["public_paper_command_allowed"] is False,
        "public paper execution must remain unavailable",
    )
    require(execution["live_execution_allowed"] is False, "live execution must remain unavailable")
    require(
        execution["shadow_required_before_paper"] is True, "shadow acceptance cannot be bypassed"
    )
    require(
        execution["authenticated_paper_required_for_a_plus"] is True,
        "A+ must require paper evidence",
    )

    print(
        json.dumps(
            {
                "status": "verified",
                "policy_id": policy["policy_id"],
                "baseline_commit": baseline["main_commit"],
                "a_plus_minimum": policy["score"]["a_plus_minimum"],
                "weight_total": sum(policy["score"]["weights"].values()),
                "mandatory_gate_categories": len(required_categories),
                "hypothesis_families": len(families),
                "candidate_ceiling_total": sum(
                    int(family["candidate_ceiling"]) for family in families
                ),
                "allowed_options_structures": sorted(allowed),
                "v1_holdout_status": baseline["v1_holdout_status"],
                "public_paper_command_allowed": False,
                "live_execution_allowed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
