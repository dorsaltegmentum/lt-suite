# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""config.schema.json: keyword subset, defaults agreement, key coverage, jsonschema parity."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from ltcore import config

REPO = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((REPO / "shared" / "schemas" / "config.schema.json").read_text(encoding="utf-8"))
with open(REPO / "shared" / "config.defaults.toml", "rb") as _fh:
    DEFAULTS = tomllib.load(_fh)

SUPPORTED = {"$schema", "$id", "title", "description", "type", "enum", "const", "properties",
             "additionalProperties", "required", "items", "minItems", "maxItems", "minimum",
             "maximum", "pattern", "minLength", "default", "x-path", "x-regex", "x-unique", "x-redact"}

# Every key in the design's key table (section 4), dotted; [] marks array-of-table items.
DESIGN_KEYS = """
schema_version institution.profile institution.name institution.unit_code institution.locale
institution.timezone lt.origin lt.instance lt.allowed_url_patterns paths.data_dir
paths.captures_dir paths.runs_dir paths.downloads_dir paths.downloads_mode paths.cache_dir
notes.graph_path notes.format notes.pages_dir notes.assets_dir notes.whiteboards_dir
notes.page_namespace notes.page_title notes.file_name_format notes.stamp_property
curriculum.root curriculum.learning_outcomes_file curriculum.skip_dirs curriculum.text_extensions
curriculum.week_number_pattern curriculum.week_dir_pattern curriculum.title_stopwords
curriculum.patterns.lesson curriculum.patterns.bare_number curriculum.patterns.ilo_heading
curriculum.patterns.ilo_kind rat.answers_dir rat.answers_dirs rat.kind_markers.irat rat.kind_markers.trat rat.irat rat.trat model_answer.read tiers.tier
tiers.tier[].id tiers.tier[].label tiers.tier[].tag_template tiers.tier[].requires_accessed
tiers.tier[].issuer_order tiers.tier[].access_note tiers.reference_set tiers.fallback_sources
tiers.framing export.outputs.module_page export.outputs.pdf export.outputs.notes_graph
export.outputs.whiteboard export.outputs.ilo_map export.outputs.flashcard_source
export.outputs.tracker_handoff export.flashcard_source.file export.flashcard_source.tag_prefix
export.citations.page_aliases export.citations.url_aliases export.citations.kind_pages
export.citations.hidden_skip_reasons pdf.author pdf.strip_metadata style.palettes
style.print_palette style.custom_palettes_file fonts.sans fonts.mono fonts.sans_path
fonts.system_fallback fonts.pdf.sans fonts.pdf.mono fonts.diagram.sans fonts.diagram.mono
fonts.labels.sans annotate.palette_copies fonts.mono_path fonts.mono_bold_path fonts.text_px fonts.allow_download fonts.page_fonts
browser.adapter browser.chromium browser.chromium_user_data_dir browser.devtools_url browser.transport browser.sink_port browser.lib_mode browser.lib_port
browser.js_tmp_dir fill.check_answer.enabled fill.approval_mode fill.max_pages_per_call fill.save_wait.quiet_ms
fill.save_wait.max_ms fill.save_wait.fallback_ms fill.save_wait.render_wait_ms
fill.dialog.max_ms fill.dialog.fallback_ms text_cleaners text_cleaners[].name
text_cleaners[].stage text_cleaners[].argv text_cleaners[].timeout_s annotate.casing
annotate.ink annotate.margin_fill annotate.font_path annotate.naming_rules diagram.floor_path
diagram.page.max_w diagram.page.max_h diagram.rubric_path diagram.example_case
diagram.header.required_fields diagram.header.members_order diagram.naming.case_slug_pattern
diagram.naming.eyebrow_template diagram.delivery.lms_name diagram.delivery.submission_label
diagram.illustration.credit diagram.treatment_check.provider_label
diagram.treatment_check.ledger_name diagram.anki.tag_prefix diagram.layout.workers
capture.drop_text_patterns capture.timeouts.post_ms improvement.benchmark_cells
""".split()


def walk(s: dict, prefix: str = ""):
    """Yield (dotted key, schema) for every leaf; array-of-table items as key[].sub."""
    for k, sub in s.get("properties", {}).items():
        key = f"{prefix}{k}"
        if sub.get("type") == "object" and "properties" in sub:
            yield from walk(sub, key + ".")
            continue
        yield key, sub
        items = sub.get("items", {})
        if sub.get("type") == "array" and items.get("type") == "object":
            for ik, isub in items.get("properties", {}).items():
                yield f"{key}[].{ik}", isub


def keywords(node, out: set):
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(k)
            if k in ("properties",):
                for sub in v.values():
                    keywords(sub, out)
            elif k in ("items", "additionalProperties") and isinstance(v, dict):
                keywords(v, out)
    return out


def lookup(doc, dotted):
    for part in dotted.split("."):
        if not isinstance(doc, dict) or part not in doc:
            return KeyError
        doc = doc[part]
    return doc


def test_only_supported_keywords():
    assert keywords(SCHEMA, set()) <= SUPPORTED


def test_schema_covers_every_design_key():
    keys = {k for k, _ in walk(SCHEMA)}
    assert set(DESIGN_KEYS) == keys, (set(DESIGN_KEYS) ^ keys)


def test_every_leaf_has_a_description():
    missing = [k for k, s in walk(SCHEMA) if not s.get("description")]
    assert not missing


def test_defaults_file_matches_schema_defaults():
    for key, s in walk(SCHEMA):
        if "[]" in key:
            continue
        top = key.split(".")[0]
        got = lookup(DEFAULTS, key)
        if top in ("style", "fonts"):
            assert got is KeyError, f"{key} belongs in style.defaults.toml"
            continue
        assert got is not KeyError, f"{key} missing from config.defaults.toml"
        assert got == s.get("default"), f"{key}: defaults file {got!r} != schema {s.get('default')!r}"


def test_defaults_file_has_no_unknown_keys():
    errors, _ = config.check(DEFAULTS)
    assert errors == []


def test_fill_batch_default_is_five():
    assert DEFAULTS["fill"]["max_pages_per_call"] == 5


def test_id_points_at_the_repository():
    assert SCHEMA["$id"] == "https://github.com/dorsaltegmentum/lt-suite/schemas/config.schema.json"


@pytest.mark.parametrize("doc, ok", [
    ({}, True),
    ({"lt": {"origin": "https://lt.example.invalid"}}, True),
    ({"lt": {"origin": "nope"}}, False),
    ({"lt": {"bogus": 1}}, False),
    ({"fill": {"max_pages_per_call": 0}}, False),
    ({"tiers": {"tier": [{"id": 1, "label": "x"}]}}, False),
    ({"text_cleaners": [{"name": "c", "stage": "pdf", "argv": ["x", "{pdf}"]}]}, True),
    ({"export": {"citations": {"page_aliases": {"A": 1}}}}, False),
    ({"style": {"palettes": ["Bad Id"]}}, False),
])
def test_matches_jsonschema(doc, ok):
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(SCHEMA)
    js_ok = not list(validator.iter_errors(doc))
    errors = []
    config._validate(doc, SCHEMA, "", errors)
    assert js_ok == ok
    assert (not errors) == ok, errors


@pytest.mark.parametrize("name", sorted(p.relative_to(REPO) for p in [REPO / "examples/configs/minimal.toml",
                                                                     *(REPO / "examples").glob("*/config.toml")]))
def test_examples_pass_jsonschema(name):
    jsonschema = pytest.importorskip("jsonschema")
    with open(REPO / name, "rb") as fh:
        doc = tomllib.load(fh)
    jsonschema.Draft202012Validator(SCHEMA).validate(doc)
