"""Declarative pack loading, schema errors, and native evidence results."""

import json
import tempfile
from pathlib import Path

from billguard.model import CheckResult, Status
from billguard.packs import PackValidationError, load_pack
from harness import eq, test, true


VALID = {
    "schema_version": 1,
    "id": "fixture-progress",
    "name": "Fixture progress checks",
    "version": 1,
    "rules": [{
        "check_id": "P01",
        "title": "Claim arithmetic agrees",
        "operator": "difference_equals",
        "fields": ["claimed_to_date_cents", "previously_certified_cents",
                   "this_claim_cents"],
        "severity": "hold",
        "fp_risk": "none",
        "evidence": {
            "pass": "Claim arithmetic agrees at {this_claim_cents} cents.",
            "fail": "Claim arithmetic does not agree with {this_claim_cents} cents.",
            "unknown": "Cannot check claim arithmetic; missing {missing}.",
        },
    }],
}


def _write(data) -> Path:
    folder = Path(tempfile.mkdtemp())
    path = folder / "pack.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@test
def valid_pack_loads_and_emits_native_check_results():
    pack = load_pack(_write(VALID))
    eq((pack.pack_id, pack.name, pack.version),
       ("fixture-progress", "Fixture progress checks", 1))
    result = pack.evaluate({"claimed_to_date_cents": 12500,
                            "previously_certified_cents": 5000,
                            "this_claim_cents": 7500})[0]
    true(isinstance(result, CheckResult))
    eq(result.status, Status.PASS)
    true(result.evidence)
    eq(set(result.to_dict()), {"check_id", "title", "status", "severity",
                               "fp_risk", "evidence", "detail"})


@test
def invalid_pack_is_rejected_with_the_field_and_reason_named():
    invalid = {**VALID, "rules": [{**VALID["rules"][0], "fields": ["only_one"]}]}
    try:
        load_pack(_write(invalid))
    except PackValidationError as exc:
        true("pack.rules[0].fields" in str(exc))
        true("requires 3 field name(s)" in str(exc))
    else:
        raise AssertionError("invalid pack was accepted")


@test
def missing_pack_input_is_unknown_and_never_a_pass():
    result = load_pack(_write(VALID)).evaluate({
        "claimed_to_date_cents": 12500,
        "this_claim_cents": 7500,
    })[0]
    eq(result.status, Status.UNKNOWN)
    true("previously_certified_cents" in result.evidence)
    eq(result.detail["missing_fields"], ["previously_certified_cents"])


@test
def duplicate_ids_are_rejected_to_keep_findings_unambiguous():
    invalid = {**VALID, "rules": VALID["rules"] * 2}
    try:
        load_pack(_write(invalid))
    except PackValidationError as exc:
        true("check_id values must be unique" in str(exc))
    else:
        raise AssertionError("duplicate check ids were accepted")
