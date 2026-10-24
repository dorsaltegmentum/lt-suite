# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stdlib TOML writer round-trips every shape the config uses."""
from __future__ import annotations

import tomllib

import pytest

from ltcore import config, tomlw


def roundtrip(doc: dict) -> dict:
    text = tomlw.dumps(doc)
    return tomllib.loads(text)


def test_scalars_and_lists():
    doc = {"s": "plain", "i": 42, "neg": -7, "b": True, "f": False, "fl": 1.5,
           "empty": "", "lst": ["a", "b"], "nums": [1, 2, 3], "none": [],
           "mixed_bools": [True, False]}
    assert roundtrip(doc) == doc


def test_string_escapes():
    tricky = ['quote " inside', "back\\slash", "tab\there", "new\nline", "cr\rx",
              "unicode é ü ©", "control \x01 \x1f \x7f", "single ' quote", "both ' and \\",
              r"(?i)\bweek\s*(\d{1,2})\b", "[T1:{source}]", "#hash", "a = b"]
    doc = {f"k{i}": s for i, s in enumerate(tricky)}
    assert roundtrip(doc) == doc


def test_regex_uses_literal_string():
    text = tomlw.dumps({"p": r"^\d+$"})
    assert text.strip() == r"p = '^\d+$'"


def test_keys_that_need_quotes():
    doc = {"plain-key_1": 1, "has space": 2, "dot.ted": 3, "": 4, "é": 5,
           "tbl": {"a.b": {"c d": "x"}}}
    assert roundtrip(doc) == doc


@pytest.mark.parametrize("key", ["a\n", "a\nb", 'a"b', "a\\b"])
def test_special_keys_roundtrip_through_tomllib(key):
    doc = {key: 1, "table": {key: {key: 2}}, "rows": [{key: 3}]}
    assert tomllib.loads(tomlw.dumps(doc)) == doc


def test_nested_tables_and_arrays_of_tables():
    doc = {
        "schema_version": 1,
        "text_cleaners": [{"name": "a", "stage": "pdf", "argv": ["x", "{pdf}"], "timeout_s": 60}],
        "tiers": {
            "framing": "",
            "tier": [
                {"id": 1, "label": "One", "issuer_order": [], "meta": {"x": 1}},
                {"id": 2, "label": "Two", "sub": [{"k": "v"}, {"k": "w"}]},
            ],
        },
        "export": {"outputs": {"pdf": True}, "citations": {"page_aliases": {}, "kind_pages": {"ilo": "ILO"}}},
        "deep": {"a": {"b": {"c": {"d": 1}}}},
        "empty_table": {},
        "list_of_empty_tables": [{}],
        "inline_mix": [1, {"a": 1}],
    }
    assert roundtrip(doc) == doc


def test_long_list_wraps_and_reads_back():
    doc = {"long": [f"item-{i:03d}" for i in range(40)]}
    text = tomlw.dumps(doc)
    assert "\n" in text.strip()
    assert tomllib.loads(text) == doc


def test_header_comments():
    text = tomlw.dumps({"a": 1}, header=["line one", "line two"])
    assert text.startswith("# line one\n# line two\n")
    assert tomllib.loads(text) == {"a": 1}


def test_floats_special():
    doc = {"inf": float("inf"), "ninf": float("-inf"), "x": 0.1}
    back = roundtrip(doc)
    assert back["inf"] == float("inf") and back["ninf"] == float("-inf") and back["x"] == 0.1
    nan = roundtrip({"n": float("nan")})["n"]
    assert nan != nan


@pytest.mark.parametrize("bad", [None, object(), {1: "x"}, {"t": (1, 2)}])
def test_unsupported_types_raise(bad):
    doc = {"k": bad} if not isinstance(bad, dict) else bad
    with pytest.raises(TypeError):
        tomlw.dumps(doc)


def test_none_message_names_key():
    with pytest.raises(TypeError, match=r"tbl\.key"):
        tomlw.dumps({"tbl": {"key": None}})


def test_defaults_roundtrip():
    """The full built-in defaults (including style defaults when present) round-trip."""
    d, _ = config.defaults()
    assert roundtrip(d) == d


def test_dump_to_file(tmp_path):
    p = tmp_path / "x.toml"
    with open(p, "w", encoding="utf-8") as fh:
        tomlw.dump({"a": {"b": [1]}}, fh)
    assert tomllib.loads(p.read_text(encoding="utf-8")) == {"a": {"b": [1]}}
