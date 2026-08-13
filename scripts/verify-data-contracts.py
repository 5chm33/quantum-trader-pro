#!/usr/bin/env python3
"""Verify provider-neutral point-in-time research data contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "research" / "schemas"
MANIFEST_PATH = SCHEMA_DIR / "schema_manifest_v1.json"
DRAFT = "https://json-schema.org/draft/2020-12/schema"
REPOSITORY_PREFIX = "https://github.com/5chm33/quantum-trader-pro/research/schemas/"
EXPECTED_SCHEMA_FILES = (
    "borrow_snapshot_v1.schema.json",
    "common_v1.schema.json",
    "corporate_action_v1.schema.json",
    "data_snapshot_manifest_v1.schema.json",
    "dividend_input_v1.schema.json",
    "earnings_estimate_snapshot_v1.schema.json",
    "earnings_event_v1.schema.json",
    "equity_bar_v1.schema.json",
    "fundamental_fact_v1.schema.json",
    "market_session_v1.schema.json",
    "option_greeks_v1.schema.json",
    "option_instrument_v1.schema.json",
    "option_quote_v1.schema.json",
    "option_trade_v1.schema.json",
    "rate_curve_v1.schema.json",
    "universe_membership_v1.schema.json",
    "volatility_surface_v1.schema.json",
    "volatility_term_structure_v1.schema.json",
)
POINT_IN_TIME_EXEMPT = {
    "common_v1.schema.json",
    "data_snapshot_manifest_v1.schema.json",
}
LOCAL_REF_PATTERN = re.compile(r"^(?P<file>[a-z0-9_]+_v[0-9]+\.schema\.json)(?P<fragment>#/.*)?$")


class ContractVerificationError(ValueError):
    """Raised when a contract invariant is violated."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractVerificationError(f"invalid JSON file: {path.relative_to(ROOT)}") from exc
    if not isinstance(value, dict):
        raise ContractVerificationError(f"top level must be an object: {path.relative_to(ROOT)}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref" and isinstance(child, str):
                refs.append(child)
            else:
                refs.extend(_walk_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_walk_refs(child))
    return refs


def _resolve_fragment(document: dict[str, Any], fragment: str | None) -> None:
    if fragment is None:
        return
    if not fragment.startswith("#/"):
        raise ContractVerificationError(f"unsupported JSON pointer fragment: {fragment}")
    current: Any = document
    for raw_part in fragment[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ContractVerificationError(f"unresolved JSON pointer fragment: {fragment}")
        current = current[part]


def _verify_local_refs(
    path: Path, document: dict[str, Any], documents: dict[str, dict[str, Any]]
) -> None:
    for reference in _walk_refs(document):
        if reference.startswith("#/"):
            _resolve_fragment(document, reference)
            continue
        match = LOCAL_REF_PATTERN.fullmatch(reference)
        if match is None:
            raise ContractVerificationError(
                f"unsupported or remote $ref in {path.name}: {reference}"
            )
        filename = match.group("file")
        target = documents.get(filename)
        if target is None:
            raise ContractVerificationError(f"missing local $ref target in {path.name}: {filename}")
        _resolve_fragment(target, match.group("fragment"))


def _verify_contract_shape(filename: str, document: dict[str, Any]) -> None:
    if document.get("$schema") != DRAFT:
        raise ContractVerificationError(f"wrong JSON Schema draft: {filename}")
    if document.get("$id") != f"{REPOSITORY_PREFIX}{filename}":
        raise ContractVerificationError(f"noncanonical $id: {filename}")
    title = document.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ContractVerificationError(f"missing title: {filename}")
    if filename == "common_v1.schema.json":
        definitions = document.get("$defs")
        if not isinstance(definitions, dict):
            raise ContractVerificationError("common schema must define $defs")
        required_defs = {
            "availability",
            "date",
            "decimal",
            "nonnegative_decimal",
            "positive_decimal",
            "provenance",
            "record_identity",
            "security_identity",
            "sha256",
            "utc_timestamp",
        }
        if set(definitions) != required_defs:
            raise ContractVerificationError("common schema definition set changed")
        return
    schema_const = document.get("properties", {}).get("schema_id", {}).get("const")
    expected_const = f"qtpro.{filename.removesuffix('_v1.schema.json')}.v1"
    if schema_const != expected_const:
        raise ContractVerificationError(f"schema_id mismatch: {filename}")
    if filename in POINT_IN_TIME_EXEMPT:
        return
    required = document.get("required")
    if not isinstance(required, list):
        raise ContractVerificationError(f"required list missing: {filename}")
    mandatory = {"schema_id", "identity", "availability", "provenance"}
    if not mandatory.issubset(set(required)):
        raise ContractVerificationError(f"point-in-time envelope missing: {filename}")
    properties = document.get("properties")
    if not isinstance(properties, dict) or document.get("additionalProperties") is not False:
        raise ContractVerificationError(f"contract must reject unknown fields: {filename}")


def _manifest(documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for filename in EXPECTED_SCHEMA_FILES:
        document = documents[filename]
        entries.append(
            {
                "filename": filename,
                "schema_id": document["$id"],
                "title": document["title"],
                "sha256": _sha256(SCHEMA_DIR / filename),
            }
        )
    return {
        "manifest_id": "qtpro.point_in_time_data_contracts.v1",
        "json_schema_draft": DRAFT,
        "contract_count": len(entries),
        "contracts": entries,
    }


def _verify_manifest(expected: dict[str, Any]) -> None:
    actual = _load_json(MANIFEST_PATH)
    if actual != expected:
        raise ContractVerificationError(
            "schema manifest is stale; run scripts/verify-data-contracts.py --write-manifest"
        )


def verify(*, write_manifest: bool) -> dict[str, Any]:
    actual_files = tuple(sorted(path.name for path in SCHEMA_DIR.glob("*_v1.schema.json")))
    if actual_files != EXPECTED_SCHEMA_FILES:
        raise ContractVerificationError(
            f"schema file set changed: expected {EXPECTED_SCHEMA_FILES}, found {actual_files}"
        )
    documents = {filename: _load_json(SCHEMA_DIR / filename) for filename in actual_files}
    schema_ids: set[str] = set()
    titles: set[str] = set()
    for filename, document in documents.items():
        _verify_contract_shape(filename, document)
        _verify_local_refs(SCHEMA_DIR / filename, document, documents)
        schema_id = str(document["$id"])
        title = str(document["title"])
        if schema_id in schema_ids or title in titles:
            raise ContractVerificationError(f"duplicate schema identity: {filename}")
        schema_ids.add(schema_id)
        titles.add(title)
    expected_manifest = _manifest(documents)
    if write_manifest:
        MANIFEST_PATH.write_text(
            json.dumps(expected_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    _verify_manifest(expected_manifest)
    return {
        "status": "verified",
        "manifest_id": expected_manifest["manifest_id"],
        "contract_count": expected_manifest["contract_count"],
        "manifest_sha256": _sha256(MANIFEST_PATH),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="rewrite the deterministic schema hash manifest before verification",
    )
    arguments = parser.parse_args()
    try:
        result = verify(write_manifest=arguments.write_manifest)
    except ContractVerificationError as exc:
        print(f"data-contract verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
