# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validate synthetic routing datasets and score recorded observations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SKILLS = frozenset(
    {
        "lt-suite-setup",
        "lt-module-capture",
        "lt-module-answer",
        "lt-module-fill",
        "lt-module-export",
        "mechanistic-diagram",
    }
)


class InputError(ValueError):
    """An evaluation input does not satisfy the scoring contract."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"Duplicate JSON member: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise InputError(f"Non-finite JSON constant: {value}")


def read_cases(path: Path) -> dict[str, dict[str, Any]]:
    """Read a nonempty case list and reject duplicate case identifiers."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise InputError(f"Cannot read {path}: {exc}") from exc

    try:
        document = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise InputError(f"Invalid JSON in {path}: {exc.msg}") from exc

    if not isinstance(document, dict):
        raise InputError(f"{path}: expected a JSON object")

    rows = document.get("cases")
    if not isinstance(rows, list) or not rows:
        raise InputError(f"{path}: cases must be a nonempty list")

    indexed: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InputError(f"{path}: case {position} must be an object")
        case_id = row.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise InputError(f"{path}: case {position} needs a nonempty id")
        if case_id in indexed:
            raise InputError(f"{path}: duplicate case id {case_id}")
        indexed[case_id] = row
    return indexed


def _match_cases(
    expectations: dict[str, dict[str, Any]],
    observations: dict[str, dict[str, Any]],
) -> None:
    if not expectations:
        raise InputError("Expectations must contain at least one case")
    missing = sorted(expectations.keys() - observations.keys())
    extra = sorted(observations.keys() - expectations.keys())
    if missing or extra:
        raise InputError(
            f"Case identifiers differ: missing={missing}, unexpected={extra}"
        )


def _query(row: dict[str, Any], case_id: str) -> None:
    value = row.get("query")
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{case_id}: query must be a nonempty string")


def _boolean(row: dict[str, Any], field: str, case_id: str) -> bool:
    value = row.get(field)
    if type(value) is not bool:
        raise InputError(f"{case_id}: {field} must be a boolean")
    return value


def _skill(row: dict[str, Any], field: str, case_id: str) -> str:
    if field not in row:
        raise InputError(f"{case_id}: missing {field}")
    value = row[field]
    if value is None:
        return "none"
    if not isinstance(value, str) or value not in SKILLS:
        raise InputError(f"{case_id}: {field} must be a suite skill or null")
    return value


def score_triggers(
    expectations: dict[str, dict[str, Any]],
    observations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Score strict boolean trigger observations without dropping cases."""
    _match_cases(expectations, observations)
    counts = {
        "true_positive": 0,
        "true_negative": 0,
        "false_positive": 0,
        "false_negative": 0,
    }
    mismatches = []

    for case_id in sorted(expectations):
        expected_row = expectations[case_id]
        _query(expected_row, case_id)
        expected = _boolean(expected_row, "should_trigger", case_id)
        actual = _boolean(observations[case_id], "triggered", case_id)
        if expected and actual:
            counts["true_positive"] += 1
        elif not expected and not actual:
            counts["true_negative"] += 1
        elif actual:
            counts["false_positive"] += 1
        else:
            counts["false_negative"] += 1
        if expected != actual:
            mismatches.append(
                {"id": case_id, "expected": expected, "actual": actual}
            )

    total = len(expectations)
    passed = total - len(mismatches)
    return {
        "kind": "trigger",
        "total": total,
        "passed": passed,
        "failed": len(mismatches),
        "accuracy": passed / total,
        "counts": counts,
        "mismatches": mismatches,
    }


def score_confusion(
    expectations: dict[str, dict[str, Any]],
    observations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Score single-label routing and produce expected-by-actual counts."""
    _match_cases(expectations, observations)
    labels = ["none", *sorted(SKILLS)]
    matrix = {expected: {actual: 0 for actual in labels} for expected in labels}
    mismatches = []

    for case_id in sorted(expectations):
        expected_row = expectations[case_id]
        _query(expected_row, case_id)
        expected = _skill(expected_row, "expected_skill", case_id)
        actual = _skill(observations[case_id], "actual_skill", case_id)
        matrix[expected][actual] += 1
        if expected != actual:
            mismatches.append(
                {"id": case_id, "expected": expected, "actual": actual}
            )

    total = len(expectations)
    passed = total - len(mismatches)
    return {
        "kind": "confusion",
        "total": total,
        "passed": passed,
        "failed": len(mismatches),
        "accuracy": passed / total,
        "matrix": matrix,
        "mismatches": mismatches,
    }
