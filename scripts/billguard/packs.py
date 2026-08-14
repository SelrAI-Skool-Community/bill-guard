"""Validated, declarative niche check packs.

Pack files are data, never Python: this keeps loading deterministic and avoids
turning a verification rule into an arbitrary-code execution surface.
"""

from __future__ import annotations

import json
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .model import CheckResult, FPRisk, Severity, fail, ok, unknown


class PackValidationError(ValueError):
    """A pack cannot be loaded; the message names the invalid location."""


@dataclass(frozen=True)
class PackRule:
    check_id: str
    title: str
    operator: str
    fields: tuple[str, ...]
    severity: Severity
    fp_risk: FPRisk
    evidence: Mapping[str, str]
    missing_behavior: str = "unknown"

    def evaluate(self, values: Mapping[str, Any]) -> CheckResult:
        missing = [name for name in self.fields if values.get(name) is None]
        if missing:
            display = _format_values(values)
            display["missing"] = ", ".join(missing)
            message = self.evidence["unknown"].format_map(display)
            if self.missing_behavior == "fail":
                return fail(self.check_id, self.title, self.severity, self.fp_risk,
                            message, pack_operator=self.operator,
                            missing_fields=missing)
            return unknown(self.check_id, self.title, message,
                           pack_operator=self.operator, missing_fields=missing)

        if self.operator == "required":
            passed = True
        else:  # difference_equals: fields are [minuend, subtrahend, expected]
            left, right, expected = (values[name] for name in self.fields)
            passed = left - right == expected

        key = "pass" if passed else "fail"
        message = self.evidence[key].format(**_format_values(values))
        detail = {"pack_operator": self.operator,
                  "fields": {name: values[name] for name in self.fields}}
        if passed:
            return ok(self.check_id, self.title, message, **detail)
        return fail(self.check_id, self.title, self.severity, self.fp_risk,
                    message, **detail)


@dataclass(frozen=True)
class Pack:
    pack_id: str
    name: str
    version: int
    rules: tuple[PackRule, ...]

    def evaluate(self, values: Mapping[str, Any]) -> list[CheckResult]:
        if not isinstance(values, Mapping):
            raise TypeError("pack values must be a mapping")
        return [rule.evaluate(values) for rule in self.rules]


def load_pack(path: str | Path) -> Pack:
    """Load and validate one UTF-8 JSON pack, with actionable errors."""
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PackValidationError(f"{source}: cannot read pack: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PackValidationError(
            f"{source}: invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    try:
        return _validate(raw)
    except PackValidationError as exc:
        raise PackValidationError(f"{source}: {exc}") from exc


def _validate(raw: Any) -> Pack:
    _mapping(raw, "pack")
    allowed = {"schema_version", "id", "name", "version", "rules"}
    extra = set(raw) - allowed
    if extra:
        raise PackValidationError(f"pack: unknown key {sorted(extra)[0]!r}")
    if raw.get("schema_version") != 1:
        raise PackValidationError("pack.schema_version: must be 1")
    pack_id = _text(raw.get("id"), "pack.id")
    name = _text(raw.get("name"), "pack.name")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PackValidationError("pack.version: must be a positive integer")
    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise PackValidationError("pack.rules: must be a non-empty array")
    rules = tuple(_validate_rule(item, index) for index, item in enumerate(rules_raw))
    ids = [rule.check_id for rule in rules]
    if len(ids) != len(set(ids)):
        raise PackValidationError("pack.rules: check_id values must be unique")
    return Pack(pack_id, name, version, rules)


def _validate_rule(raw: Any, index: int) -> PackRule:
    at = f"pack.rules[{index}]"
    _mapping(raw, at)
    allowed = {"check_id", "title", "operator", "fields", "severity",
               "fp_risk", "evidence", "missing_behavior"}
    extra = set(raw) - allowed
    if extra:
        raise PackValidationError(f"{at}: unknown key {sorted(extra)[0]!r}")
    check_id = _text(raw.get("check_id"), f"{at}.check_id")
    title = _text(raw.get("title"), f"{at}.title")
    operator = raw.get("operator")
    arities = {"required": 1, "difference_equals": 3}
    if operator not in arities:
        raise PackValidationError(
            f"{at}.operator: must be 'required' or 'difference_equals'")
    fields = raw.get("fields")
    if (not isinstance(fields, list) or len(fields) != arities[operator] or
            any(not isinstance(item, str) or not item.strip() for item in fields)):
        raise PackValidationError(
            f"{at}.fields: {operator!r} requires {arities[operator]} field name(s)")
    severity = _enum(Severity, raw.get("severity"), f"{at}.severity")
    fp_risk = _enum(FPRisk, raw.get("fp_risk"), f"{at}.fp_risk")
    missing_behavior = raw.get("missing_behavior", "unknown")
    if missing_behavior not in {"unknown", "fail"}:
        raise PackValidationError(
            f"{at}.missing_behavior: must be 'unknown' or 'fail'")
    evidence = raw.get("evidence")
    _mapping(evidence, f"{at}.evidence")
    if set(evidence) != {"pass", "fail", "unknown"}:
        raise PackValidationError(
            f"{at}.evidence: must contain exactly pass, fail, and unknown")
    for key, value in evidence.items():
        _text(value, f"{at}.evidence.{key}")
        try:
            placeholders = {name for _, name, _, _ in string.Formatter().parse(value)
                            if name is not None}
        except ValueError as exc:
            raise PackValidationError(
                f"{at}.evidence.{key}: invalid format string: {exc}") from exc
        unsupported = placeholders - set(fields) - {"missing"}
        if unsupported:
            raise PackValidationError(
                f"{at}.evidence.{key}: unknown placeholder "
                f"{sorted(unsupported)[0]!r}")
        if key != "unknown" and "missing" in placeholders:
            raise PackValidationError(
                f"{at}.evidence.{key}: placeholder 'missing' is only "
                "available in unknown evidence")
    return PackRule(check_id, title, operator, tuple(fields), severity,
                    fp_risk, dict(evidence), missing_behavior)


def _mapping(value: Any, at: str) -> None:
    if not isinstance(value, dict):
        raise PackValidationError(f"{at}: must be an object")


def _text(value: Any, at: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackValidationError(f"{at}: must be a non-empty string")
    return value.strip()


def _enum(enum_type, value: Any, at: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise PackValidationError(f"{at}: must be one of {choices}") from exc


def _format_values(values: Mapping[str, Any]) -> dict[str, Any]:
    return dict(values)
