# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capture sanitation metadata survives validation without relaxing object shapes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ltcore import validate

EXAMPLE = Path(__file__).resolve().parents[1] / "fixtures/examples/module.example.json"


@pytest.fixture
def module():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def panel(module, kind):
    key = "items" if kind == "item" else "content"
    return next(value for page in module["pages"] for value in page.get(key, []))


@pytest.mark.parametrize("kind", ["item", "content_panel"])
@pytest.mark.parametrize("count,reasons", [
    (0, []),
    (2, ["instruction-override", "agent-address", "agent-imperative",
         "tool-reference", "commit-route"]),
])
def test_capture_sanitation_metadata_validates(module, kind, count, reasons):
    target = panel(module, kind)
    target.update(hidden_text_dropped=count, suspect_text=reasons)
    result = validate.validate_doc(module, "module")
    assert result.exit_code == 0, (result.schema_errors, result.rail_errors)
    assert target["hidden_text_dropped"] == count
    assert target["suspect_text"] == reasons


@pytest.mark.parametrize("kind", ["item", "content_panel"])
@pytest.mark.parametrize("field,value", [
    ("hidden_text_dropped", -1),
    ("hidden_text_dropped", 1.5),
    ("hidden_text_dropped", True),
    ("hidden_text_dropped", "1"),
    ("suspect_text", "agent-address"),
    ("suspect_text", ["unknown-reason"]),
    ("suspect_text", ["agent-address", "agent-address"]),
    ("suspect_text", [1]),
    ("unrecognized_capture_field", 1),
])
def test_malformed_capture_metadata_fails_schema(module, kind, field, value):
    panel(module, kind)[field] = value
    result = validate.validate_doc(module, "module")
    assert result.exit_code == 2
    assert any(field in error for error in result.schema_errors), result.schema_errors
