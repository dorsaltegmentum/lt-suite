# Generated from shared/ltcore/validate.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validate an lt-suite handoff file against its JSON Schema, then against the rails.

A schema stops a malformed file. The rails stop a well-formed file that breaks a
safety or provenance rule: an answer key inside a capture, a Tier-4 citation with no
resolvable identifier, a fill run that wrote past a checkpoint or onto the completion
page. Each rail is tested by a document built to trip it (tests/rails).

CLI:
    python3 -m ltcore.validate <file.json> [--schema NAME] [--schemas-dir DIR]
        [--module module.json] [--approvals approvals.json] [--config FILE]
        [--model-answer-read true|false] [--quiet]

The schema is inferred from the file name when not given:
    module*.json | answers*.json | reference*.json | fill-report*.json | run*.json |
    approvals*.json

--module        cross-checks a fill report or an answers file against its capture.
--approvals     checks approval ids (single-attempt approvals and waivers) against the
                run's approvals file; without it only their format is checked.
--config        config file for model_answer.read (default: the normal config lookup).
--model-answer-read  overrides model_answer.read for this check.

Exit codes:
    0  valid
    1  rail violation (well-formed, but breaks a rail)
    2  schema violation
    3  usage, file, config or schema-load error, or python-jsonschema missing
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse  # noqa: E402
import collections  # noqa: E402
import datetime as dt  # noqa: E402
import hashlib  # noqa: E402
import html  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402

from ltcore import approvals as approvals_mod  # noqa: E402
from ltcore.boundaries import (  # noqa: E402,F401
    ONE_WAY_DOORS, PLACEMENT_TYPES, first_stop_boundary, inferred_locks, lock_cause,
    lock_evidence, lock_reach, single_pass_pages, submitted_single)
from ltcore import rat, page_kinds
from ltcore.digest import question_digest  # noqa: E402

NEEDS = [
    "schemas/module.schema.json",
    "schemas/answers.schema.json",
    "schemas/reference.schema.json",
    "schemas/run.schema.json",
    "schemas/fill-report.schema.json",
    "schemas/approvals.schema.json",
    "schemas/page-kinds.schema.json",
    "schemas/reconciled.schema.json",
]

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"

SCHEMA_BY_PREFIX = [
    ("reconciled", "reconciled.schema.json"),
    ("page-kinds", "page-kinds.schema.json"),
    ("fill-report", "fill-report.schema.json"),
    ("module", "module.schema.json"),
    ("answers", "answers.schema.json"),
    ("reference", "reference.schema.json"),
    ("approvals", "approvals.schema.json"),
    ("run", "run.schema.json"),
]

# Rail module-has-no-answer-key. Keys that would carry Lt's answer key. The capture
# stays free of them so it can be shared as an eval fixture; reading the model answer
# is governed by model_answer.read and stored only in model-answer.json.
DENIED_KEYS = {"modelanswer", "modelanswers", "answerkey", "iscorrect", "correctanswer", "checked"}
MODEL_ANSWER_SOURCE = "lt-model-answer"
CONNECTIVE_SHARE_MAX = 0.25
DEFAULT_META_TEXT_PATTERNS = (
    r"\bthis draft\b", r"\bin (the|this) draft\b",
    r"\bnot (yet )?(checked|verified) in\b", r"\bTODO\b", r"\bTBD\b",
    r"\bFIXME\b", r"\bplaceholder\b", r"\[T[1-4](?::[^\]]*)?\]",
)


def meta_text_errors(text, patterns=DEFAULT_META_TEXT_PATTERNS, label="segment"):
    """Reject drafting status in text that will reach the answer field."""
    plain = re.sub(r"<[^>]*>", "", html.unescape(str(text)))
    plain = re.sub(r"\s+", " ", plain)
    errors = []
    for pattern in patterns:
        try:
            if re.search(pattern, plain, re.IGNORECASE):
                errors.append(f"{label}: meta-text matches {pattern!r}; put verification status in uncertainties")
        except re.error as error:
            errors.append(f"answers.meta_text_patterns: invalid pattern {pattern!r}: {error}")
    return errors

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
PMID_RE = re.compile(r"^PMID:\d+$", re.IGNORECASE)

# reference-*.json holds the student's own submitted work, kept as eval ground truth and
# marked never_load_into_drafting_context. A draft that cites it measures nothing.
QUARANTINED_SOURCE_RE = re.compile(r"(^|/)reference-[^/]*\.json$")

# jsonschema's optional date-time format checkers need extra packages, so the format
# keyword alone would check nothing. Timestamps are checked here instead.
DATETIME_KEYS = {"captured_at", "checked_at", "drafted_at", "started_at", "ended_at",
                 "submitted_at", "recorded_at", "run_ts", "at", "created_at", "observed_at"}
DATE_KEYS = {"accessed", "death_date"}

# Each degrade reason implies exactly one class and a fact about the item that must be
# independently true. A degrade whose class and reason disagree is a TODO that lies
# about whether it will ever go away.
DEGRADE_CLASS = {
    "geometry-unknown-until-presented": "lifts-at-fill",
    "unmapped-type": "lifts-on-capture-support",
    "panel-accepts-no-upload": "permanent",
    "single-attempt-drag": "permanent",
    "coordinator-policy": "permanent",
    "notebook-never-written": "permanent",
}
DRAG_TYPES = {"drag-and-drop-labeling", "image-labeling", "categorization"}

# A write is any attempt that reached a panel. A failed write touched the item as surely
# as one that landed, so the door rails count all three. The read-only rails count only
# claims of success: a failed write to a locked panel is an honest record.
WRITTEN = {"filled", "filled-unverified", "failed"}
CLAIMED = {"filled", "filled-unverified"}

# Rails a recorded user approval may waive. The value says whether a waiver names items.
# Each is about how a run was supervised or where it wrote; every other rail stands.
# tests/rails keeps this equal to the fill-report schema enum and ltcore.approvals.
WAIVABLE_RAILS = {
    "approval-manual-at-boundary": False,
    "write-past-first-door": True,
    "passed-single-approval": True,
}
# Without --module these rails are not evaluated, so a waiver of one cannot be shown unused.
MODULE_RAILS = {"write-past-first-door", "passed-single-approval"}


@dataclass
class Result:
    """Outcome of one validation. ``exit_code`` follows the CLI codes."""
    schema_name: str
    exit_code: int = 0
    schema_errors: list[str] = field(default_factory=list)
    rail_errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    waived: list[str] = field(default_factory=list)
    fatal: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class _Ctx:
    """What the rails need beyond the document."""
    base_dir: Path
    module: dict | None = None
    module_path: Path | None = None
    meta_text_patterns: tuple | list = DEFAULT_META_TEXT_PATTERNS
    approvals: dict | None = None
    model_answer_read: bool = True
    model_answer_where: str = "default"
    rat_roots: list[Path] = field(default_factory=list)
    page_kinds: dict | None = None
    rat_policy: dict = field(default_factory=lambda: dict(rat.DEFAULT_POLICY))
    notes: list[str] = field(default_factory=list)
    waived: list[str] = field(default_factory=list)


class ValidationSetupError(Exception):
    """A file, schema or config problem: exit code 3."""


def infer_schema(path: str | Path) -> str | None:
    stem = Path(path).name.lower()
    for prefix, schema in SCHEMA_BY_PREFIX:
        if stem.startswith(prefix):
            return schema
    return None


def norm(key: str) -> str:
    return re.sub(r"[^a-z]", "", key.lower())


def drop_legacy_fields(doc: dict) -> list[str]:
    """Remove fields of the retired permission gate, in place.

    ``permission`` (module.json) and ``permission_check`` (run.json) came from a gate
    that no longer exists. Returns the dropped key names so the caller can print one
    note; an empty list means nothing changed.
    """
    dropped = []
    if isinstance(doc, dict):
        for key in ("permission", "permission_check"):
            if key in doc:
                del doc[key]
                dropped.append(key)
    return dropped


def legacy_note(dropped: list[str]) -> str:
    return (f"dropped legacy field(s) {', '.join(dropped)}: the permission gate they "
            f"recorded is retired")


def scan_timestamps(node, trail=""):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{trail}.{k}" if trail else k
            if isinstance(v, str) and (k in DATETIME_KEYS or k in DATE_KEYS):
                try:
                    parsed = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
                    if k in DATE_KEYS and len(v) != 10:
                        out.append(f"{here}: {v!r} should be a plain YYYY-MM-DD date")
                    elif k in DATETIME_KEYS and parsed.tzinfo is None:
                        out.append(f"{here}: {v!r} has no timezone offset; runs span "
                                   f"machines, so timestamps carry one")
                except ValueError:
                    out.append(f"{here}: {v!r} is not an ISO 8601 timestamp")
            out.extend(scan_timestamps(v, here))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(scan_timestamps(v, f"{trail}[{i}]"))
    return out


def scan_denied_keys(node, trail=""):
    """Rail module-has-no-answer-key. additionalProperties:false forbids these keys at
    every declared object, but free-form maps (census.by_type, timings, token_cost)
    are open by design, so the scan covers them too."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{trail}.{k}" if trail else k
            if norm(k) in DENIED_KEYS:
                out.append(f"denied key '{k}' at {here} (module-has-no-answer-key): Lt's answer "
                           f"key never enters a capture or handoff file, so captures stay "
                           f"shareable as eval fixtures")
            out.extend(scan_denied_keys(v, here))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(scan_denied_keys(v, f"{trail}[{i}]"))
    return out


def _policy_error(what: str, ctx: _Ctx) -> str:
    return (f"model-answer-policy: {what} cites Lt's model answer ({MODEL_ANSWER_SOURCE}), "
            f"but model_answer.read is false ({ctx.model_answer_where}); turn it on or "
            f"remove the citation")


# --- module.json ---------------------------------------------------------------------

def rails_module(doc, ctx: _Ctx | None = None):
    errs = []
    pages = doc.get("pages", [])
    reports = [p for p in pages if p.get("is_report_page")]
    if len(reports) > 1:
        errs.append(f"{len(reports)} pages flagged is_report_page; a run has at most one")
    for p in pages:
        tag = f"page_index {p.get('page_index')}"
        if p.get("is_report_page"):
            # The report page and the content pages use two numbering schemes.
            if p.get("page_no") is not None:
                errs.append(f"{tag}: report page carries page_no {p['page_no']}; /page/<n> "
                            f"redirects, so the report page is not addressable by number")
            if p.get("route") != "complete":
                errs.append(f"{tag}: report page route must be 'complete', got {p.get('route')!r}")
            if p.get("items"):
                errs.append(f"{tag}: report page carries {len(p['items'])} items; its panels "
                            f"are mirrors and must not be counted")
        elif p.get("is_checkpoint"):
            # A Checkpoint is the third page shape. Lt routes it as /run/checkpoint/<n> and
            # leaves it unnumbered in the page menu, and its own index() is not that URL
            # segment, so no url is constructed for it.
            if p.get("page_no") is not None:
                errs.append(f"{tag}: checkpoint page carries page_no {p['page_no']}; Lt routes "
                            f"it as /run/checkpoint/<n>, it has no /page/<n> address")
            if p.get("route") != "checkpoint":
                errs.append(f"{tag}: checkpoint page route must be 'checkpoint', got "
                            f"{p.get('route')!r}")
            if p.get("url") is not None:
                errs.append(f"{tag}: checkpoint page carries url {p['url']!r}; page.index() is "
                            f"not the URL segment, so no url may be constructed for it")
            if p.get("items"):
                errs.append(f"{tag}: checkpoint page carries {len(p['items'])} items; it holds "
                            f"no panels, only a Commit button")
            if not isinstance((p.get("checkpoint") or {}).get("committed"), bool):
                errs.append(f"{tag}: is_checkpoint with no checkpoint.committed boolean; whether "
                            f"the door is already shut tells a reader if the tail of the run "
                            f"is hidden")
        else:
            if not isinstance(p.get("page_no"), int):
                errs.append(f"{tag}: content page has no integer page_no")
            if p.get("route") is not None:
                errs.append(f"{tag}: only the report and checkpoint pages have their own route")
            if p.get("checkpoint") is not None:
                errs.append(f"{tag}: non-checkpoint page carries a checkpoint block")
    # answered_evidence.filled_map is a second count of the same fact, computed another
    # way. A map with no '#' beside answered:true is a grid nobody opened reading as
    # answered; a '#' beside answered:false is a completed grid reading as empty.
    for p in pages:
        for it in p.get("items", []):
            ev = it.get("answered_evidence")
            if not ev:
                continue
            tag = f"page {p.get('page_index')} item {it['item_id']}"
            if it.get("type") != "table-completion":
                errs.append(f"{tag}: answered_evidence on a {it.get('type')} item; the "
                            f"filled_map shape is defined for table-completion only")
                continue
            fm = ev.get("filled_map", [])
            # 'a' says "this cell holds exactly the author's text", which only a read of the
            # author's grid can know. An 'a' without that read takes a cell out of the
            # answered count on no evidence, the direction that permits an overwrite.
            ag = ev.get("author_grid") or {}
            n_a = sum(row.count("a") for row in fm)
            if n_a and ag.get("status") != "read":
                errs.append(f"{tag}: filled_map marks {n_a} cell(s) 'a' (authored) but "
                            f"author_grid.status is {ag.get('status')!r}; only a read of "
                            f"content.rows can call a cell the author's")
            if "authored_cells" in ag and ag["authored_cells"] != n_a:
                errs.append(f"{tag}: author_grid.authored_cells is {ag['authored_cells']} but "
                            f"filled_map carries {n_a} 'a' mark(s)")
            recomputed = any("#" in row for row in fm)
            if bool(it.get("answered")) != recomputed:
                errs.append(
                    f"{tag}: answered={it.get('answered')} but filled_map {fm} recomputes to "
                    f"{recomputed}. The flag and its own evidence disagree: a grid the student "
                    f"never opened reads as answered, or a completed one reads as empty")
            sus = ev.get("answered_suspect")
            if sus:
                # The flag states a shape; check the shape is there.
                cols = [{i for i, ch in enumerate(row) if ch == "#"} for row in fm]
                one_col = (len(fm) > 1 and len(set(map(frozenset, cols))) == 1
                           and len(cols[0]) == 1)
                # The authored L: body row 0 and body column 0 full, the corner allowed
                # empty, the interior empty.
                w = len(fm[0]) if fm else 0
                l_shape = (len(fm) > 1 and w > 1
                           and all(ch != "#" for row in fm[1:] for ch in row[1:])
                           and all(ch == "#" for ch in fm[0][1:])
                           and all(row[:1] == "#" for row in fm[1:]))
                if not (one_col or l_shape):
                    errs.append(f"{tag}: answered_suspect is set but filled_map {fm} is not the "
                                f"single-filled-column or authored-L shape its basis claims")

    # census must reconcile with the inventory, independently recomputed
    captured, by_type = 0, {}
    ids = []
    for p in pages:
        for it in p.get("items", []):
            captured += 1
            ids.append(it["item_id"])
            by_type[it["type"]] = by_type.get(it["type"], 0) + 1
    # A subpage is a container, not a question.
    answerable = captured - by_type.get("subpage", 0)
    census = doc.get("census", {})
    if census.get("captured_total") != captured:
        errs.append(f"census.captured_total {census.get('captured_total')} != {captured} items "
                    f"actually present")
    if census.get("answerable_total") != answerable:
        errs.append(f"census.answerable_total {census.get('answerable_total')} != {answerable} "
                    f"(captured {captured} minus {by_type.get('subpage', 0)} subpage container(s))")
    if census.get("by_type") != by_type:
        errs.append(f"census.by_type disagrees with the inventory: declared "
                    f"{census.get('by_type')}, counted {by_type}")
    if census.get("pages_total") != len(pages):
        errs.append(f"census.pages_total {census.get('pages_total')} != {len(pages)} pages present")
    if len(set(ids)) != len(ids):
        errs.append("duplicate item_id values")
    known = set(ids)
    for pid in (it.get("parent_item_id") for p in pages for it in p.get("items", [])):
        if pid and pid not in known:
            errs.append(f"parent_item_id {pid!r} does not resolve to a captured item")
    # the boundary map must agree with the items it points at
    cb = doc.get("commit_boundaries", {})
    declared_single = set(cb.get("single_attempt_items", []))
    actual_single = {it["item_id"] for p in pages for it in p.get("items", [])
                     if it.get("attempts") == "single"}
    if declared_single != actual_single:
        errs.append(f"commit_boundaries.single_attempt_items {sorted(declared_single)} != items "
                    f"with attempts=single {sorted(actual_single)}")
    declared_cp = set(cb.get("checkpoint_pages", []))
    actual_cp = {p["page_index"] for p in pages if p.get("is_checkpoint")}
    if declared_cp != actual_cp:
        errs.append(f"commit_boundaries.checkpoint_pages {sorted(declared_cp)} != pages flagged "
                    f"is_checkpoint {sorted(actual_cp)}")
    # After commitment, Lt removes that checkpoint from lessonFlowPages.
    # Compare the live-door list with the checkpoint inventory.
    inv = cb.get("checkpoints") or []
    in_flow = {e.get("page_index") for e in inv if e.get("in_flow")}
    if in_flow != actual_cp:
        errs.append(f"commit_boundaries.checkpoints in_flow page_index "
                    f"{sorted(x for x in in_flow if x is not None)} != pages flagged "
                    f"is_checkpoint {sorted(actual_cp)}")
    for e in inv:
        if e.get("in_flow") and e.get("page_index") is None:
            errs.append(f"commit_boundaries.checkpoints entry index={e.get('index')} is in_flow "
                        f"with page_index null")
        if not e.get("in_flow") and e.get("page_index") is not None:
            errs.append(f"commit_boundaries.checkpoints entry index={e.get('index')} is not "
                        f"in_flow but claims page_index {e['page_index']}; a checkpoint Lt has "
                        f"dropped occupies no page")
        # A committed checkpoint is not in lessonFlowPages to be walked, so an entry the
        # page walk alone supplied cannot be committed.
        if e.get("source") == "lessonFlowPages" and e.get("committed") and e.get("in_flow"):
            errs.append(f"commit_boundaries.checkpoints entry index={e.get('index')} is "
                        f"committed AND in_flow from the page walk alone; Lt removes a "
                        f"committed checkpoint from lessonFlowPages, so this pair has never "
                        f"been observed and must not be asserted")
    if cb.get("checkpoints_container_present") is False and inv and any(
            not e.get("in_flow") for e in inv):
        errs.append("commit_boundaries.checkpoints holds an entry that is not in_flow while "
                    "checkpoints_container_present is false; nothing but the container could "
                    "have supplied it")
    lesson = doc.get("lesson", {})
    # An uncommitted checkpoint hides the tail of the run: Lt drops every page beyond it,
    # the report page included. has_completion_page=true with no report page captured is
    # then the correct reading, not a dropped page.
    uncommitted_cp = [p for p in pages
                      if p.get("is_checkpoint") and not (p.get("checkpoint") or {}).get("committed")]
    if lesson.get("has_completion_page") != bool(reports):
        if not (lesson.get("has_completion_page") and not reports and uncommitted_cp):
            hint = ""
            if lesson.get("has_completion_page") and not reports:
                hint = ("; and no uncommitted checkpoint is mapped to explain the absence. If "
                        "the run has one, capture is not detecting it")
            errs.append(f"lesson.has_completion_page={lesson.get('has_completion_page')} but "
                        f"{len(reports)} report page(s) captured{hint}")
    # The run-level commit is a third boundary class, invisible from content pages.
    if lesson.get("has_completion_page") and not cb.get("run_level_commit", {}).get("present"):
        errs.append("lesson has a completion page but run_level_commit.present is false; that "
                    "page carries a live Commit button no per-item flag predicts")
    if lesson.get("show_page_menu_page_remaining") and not declared_cp:
        errs.append("showPageMenuPageRemaining is true but no checkpoint page is mapped")
    if lesson.get("page_count") != len(pages):
        errs.append(f"lesson.page_count {lesson.get('page_count')} != {len(pages)} pages captured")
    # A drag on a single-attempt item cannot be undone, so it is never automated.
    for p in pages:
        for it in p.get("items", []):
            if (it.get("attempts") == "single" and it["type"] in DRAG_TYPES
                    and it.get("automation") != "degrade-todo"):
                errs.append(f"{it['item_id']}: single-attempt {it['type']} must be "
                            f"automation=degrade-todo; a drag on a single-attempt item cannot "
                            f"be undone")
    # A RAT flag must be a true statement about the page: a flag whose basis does not
    # hold invites a human to rubber-stamp it.
    for p in pages:
        rc = p.get("rat_candidate")
        if rc is None:
            continue
        its = p.get("items", [])
        if not its or any(it["type"] != "multiple-choice" for it in its):
            errs.append(
                f"page_index {p.get('page_index')}: rat_candidate is set but the page is not all "
                f"multiple-choice ({sorted({i['type'] for i in its})}); the stated basis does "
                f"not hold")
        if p.get("is_report_page"):
            errs.append(f"page_index {p.get('page_index')}: the report page cannot be a RAT "
                        f"candidate")
    # A null presented_ratio means the target slot stores nothing; a structured path into
    # an empty slot is a guess, so the item degrades to a TODO.
    for p in pages:
        for it in p.get("items", []):
            tg = it.get("target_geometry")
            if (tg is not None and tg.get("presented_ratio") is None
                    and it.get("automation") != "degrade-todo"):
                errs.append(
                    f"{it['item_id']}: target_geometry.presented_ratio is null, so automation "
                    f"must be degrade-todo, got {it.get('automation')!r}")
            if tg is not None and (tg.get("presented_ratio") is None) != (
                    tg.get("geometry_source") == "unavailable-until-presented"):
                errs.append(
                    f"{it['item_id']}: target_geometry.geometry_source must be "
                    f"'unavailable-until-presented' exactly when presented_ratio is null")
            # An annotate-only panel cannot take an upload at any point in the run.
            if tg is not None and tg.get("accepts_upload") != (
                    it.get("input_mode") == "allowImageAndAnnotation"):
                errs.append(
                    f"{it['item_id']}: target_geometry.accepts_upload is "
                    f"{tg.get('accepts_upload')} but input_mode is {it.get('input_mode')!r}; "
                    f"only 'allowImageAndAnnotation' accepts an upload")
    errs += _degrade_rails(pages)
    return errs


def _degrade_rails(pages):
    errs = []
    for p in pages:
        for it in p.get("items", []):
            d = it.get("degrade")
            if d is None:
                continue
            iid, reason, klass = it["item_id"], d.get("reason"), d.get("class")
            expect = DEGRADE_CLASS.get(reason)
            if expect and klass != expect:
                errs.append(f"{iid}: degrade.reason {reason!r} implies class {expect!r}, "
                            f"not {klass!r}")
            tg = it.get("target_geometry") or {}
            if reason == "panel-accepts-no-upload" and tg.get("accepts_upload") is not False:
                errs.append(f"{iid}: degrade.reason is 'panel-accepts-no-upload' but "
                            f"target_geometry.accepts_upload is not false")
            if reason == "geometry-unknown-until-presented":
                if tg.get("presented_ratio") is not None:
                    errs.append(f"{iid}: degrade.reason is 'geometry-unknown-until-presented' "
                                f"but presented_ratio is {tg.get('presented_ratio')!r}")
                if tg.get("accepts_upload") is False:
                    errs.append(f"{iid}: degrade blames geometry, but this panel accepts no "
                                f"upload at all; the degrade is permanent, not pending")
            if reason == "unmapped-type" and it["type"] != "unmapped":
                errs.append(f"{iid}: degrade.reason is 'unmapped-type' but type is {it['type']!r}")
            # The student's notebook is never written. The same reason on another panel
            # would make an ordinary gap permanent.
            if reason == "notebook-never-written" and not (
                    it["type"] == "unmapped" and it.get("plugin_key") == "notebook"):
                errs.append(f"{iid}: degrade.reason is 'notebook-never-written' but the item is "
                            f"a {it['type']} with plugin_key {it.get('plugin_key')!r}; the "
                            f"reason belongs to unmapped notebook panels only")
            if reason == "single-attempt-drag" and not (
                    it.get("attempts") == "single" and it["type"] in DRAG_TYPES):
                errs.append(f"{iid}: degrade.reason is 'single-attempt-drag' but the item is "
                            f"{it.get('attempts')}-attempt {it['type']}")
    return errs


# --- answers.json --------------------------------------------------------------------

def _resolve_source(rel: str, base_dir: Path) -> Path | None:
    p = Path(rel).expanduser()
    if not p.is_absolute():
        p = base_dir / p
    return p if p.is_file() else None


def _unique_item_ids(doc: dict) -> list[str]:
    seen = set()
    errors = []
    for item in doc.get("items", []):
        iid = item["item_id"]
        if iid in seen:
            errors.append(f"duplicate item_id {iid}; each handoff item must appear once")
        seen.add(iid)
    return errors


def source_binding_errors(doc, ctx: _Ctx):
    """Check the recorded capture path and pins before generating any writable payload."""
    errs = []
    src = doc.get("source_module") or {}
    want, rel = src.get("sha256"), src.get("path")
    want_q = src.get("question_digest")
    got_q = None
    cand = _resolve_source(rel, ctx.base_dir) if rel else None
    if cand is None:
        errs.append(f"source_module.path {rel!r} does not resolve to a readable capture; "
                    "the binding cannot be verified")
    if cand is not None and ctx.module_path is not None and cand.resolve() != ctx.module_path.resolve():
        errs.append(f"source_module.path {rel!r} does not resolve to the supplied --module file")
    if not want and not want_q:
        errs.append("source_module.sha256 or question_digest must identify the capture")
    if (want or want_q) and rel:
        if cand is not None:
            # After a fill the file hash always moves; the question digest does not.
            try:
                raw = cand.read_bytes()
                got = hashlib.sha256(raw).hexdigest()
                capture = json.loads(raw)
                got_q = question_digest(capture)
                if capture.get("module_id") != doc.get("module_id"):
                    errs.append("source_module module_id differs from the draft module_id")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError,
                    AttributeError):
                got, got_q = None, None
            if not got_q:
                errs.append(
                    f"source_module.question_digest is recorded but {rel} could not be read as "
                    f"a capture, so the binding cannot be checked at all")
            elif want_q and want_q != got_q:
                errs.append(
                    f"source_module.question_digest is {want_q[:12]}... but {rel} digests to "
                    f"{got_q[:12]}...: the QUESTIONS this draft answers have changed (prompt, "
                    f"type, options or an authored constraint). A re-capture that changes a "
                    f"question invalidates the draft; re-draft rather than re-point it. "
                    f"`python3 -m ltcore.digest <old> --diff {rel}` names the items.")
            elif got != want and not (want_q and got_q):
                errs.append(
                    f"source_module.sha256 is {want[:12]}... but {rel} hashes to {got[:12]}...: "
                    f"the draft is bound to a capture that no longer exists. A re-capture "
                    f"invalidates a draft; re-draft rather than re-point it. This draft records "
                    f"no question_digest, so nothing can tell an edited question from an "
                    f"answered one; add one and a post-fill re-capture stops raising this.")
            elif got != want:
                ctx.notes.append(
                    f"{rel} has changed since this draft was written (sha256 {want[:12]}... -> "
                    f"{got[:12]}...) but no question-bearing field did (question_digest "
                    f"{want_q[:12]}... holds). The binding is intact; what moved is state, "
                    f"media or geometry, including a swapped stimulus image.")
    bound_q = want_q or got_q
    if ctx.module is not None:
        if not bound_q:
            errs.append("the supplied capture questions have no verifiable source_module binding")
        elif question_digest(ctx.module) != bound_q:
            errs.append("the supplied capture question_digest differs from source_module: "
                        "the questions changed; re-draft and request a new approval")
    return errs


def rails_answers(doc, ctx: _Ctx):
    errs = _unique_item_ids(doc) + source_binding_errors(doc, ctx)
    # Contamination cannot be laundered by the field it is declared in.
    for i, c in enumerate(doc.get("tier1_corpus", [])):
        for fld in ("path", "source_id"):
            v = c.get(fld, "")
            if QUARANTINED_SOURCE_RE.search(v):
                errs.append(f"tier1_corpus[{i}].{fld} is {v!r}; reference-*.json is quarantined "
                            f"eval ground truth, and a drafting run that read it measures nothing")
    for a in doc.get("items", []):
        for p in a.get("provenance", []):
            for fld in ("source_id", "locator"):
                v = p.get(fld, "")
                if QUARANTINED_SOURCE_RE.search(v):
                    errs.append(f"{a['item_id']}: provenance {p['ref']!r} cites {v!r}; "
                                f"reference-*.json is quarantined eval ground truth, never a "
                                f"drafting source")
    # Rail model-answer-policy.
    if not ctx.model_answer_read:
        for i, c in enumerate(doc.get("tier1_corpus", [])):
            if MODEL_ANSWER_SOURCE in (c.get("source_id"), c.get("path")):
                errs.append(_policy_error(f"tier1_corpus[{i}]", ctx))
        for a in doc.get("items", []):
            for p in a.get("provenance", []):
                if p.get("source_id") == MODEL_ANSWER_SOURCE:
                    errs.append(_policy_error(f"{a['item_id']}: provenance {p.get('ref')!r}", ctx))
    for a in doc.get("items", []):
        iid = a["item_id"]
        prov = {p["ref"]: p for p in a.get("provenance", [])}
        if len(prov) != len(a.get("provenance", [])):
            errs.append(f"{iid}: duplicate provenance ref")
        used = set()
        for i, seg in enumerate(a.get("segments", [])):
            errs.extend(meta_text_errors(seg.get("text", ""), ctx.meta_text_patterns, f"{iid}: segment {i}"))
            for r in seg.get("provenance_refs", []):
                used.add(r)
                if r not in prov:
                    errs.append(f"{iid}: segment {i} cites {r!r}, which is not in provenance[]")
        for r, p in prov.items():
            if r not in used:
                errs.append(f"{iid}: provenance {r!r} ({p['source_id']}) is cited by no segment; "
                            f"an uncited citation is decoration")
            # A Tier-4 identifier that cannot be resolved is a fabrication.
            if p["tier"] == 4 and not (DOI_RE.match(p["source_id"]) or PMID_RE.match(p["source_id"])):
                errs.append(f"{iid}: Tier-4 source_id {p['source_id']!r} is neither a DOI nor a "
                            f"PMID; an unresolvable identifier is a fabrication")
        # Every tier skipped on the way down needs its own recorded reason. Keyed on the
        # deepest tier cited.
        tiers = {p["tier"] for p in prov.values()}
        if tiers:
            deepest = max(tiers)
            descents = {(d["from_tier"], d["to_tier"]) for d in a.get("tier_descent", [])}
            for t in range(1, deepest):
                if (t, t + 1) not in descents:
                    errs.append(f"{iid}: cites tier {deepest} but records no reason for "
                                f"descending {t}->{t + 1}")
        segs = a.get("segments", [])
        if a.get("status") == "drafted" and not any(s["kind"] == "claim" for s in segs):
            errs.append(f"{iid}: drafted but contains no claim segment")
        # Code cannot separate a factual sentence from a connective one, so the share of
        # untagged text is capped instead: relabelling escapes citation only in small doses.
        total = sum(len(s["text"]) for s in segs)
        conn = sum(len(s["text"]) for s in segs if s["kind"] == "connective")
        if total and conn / total > CONNECTIVE_SHARE_MAX:
            errs.append(f"{iid}: {conn / total:.0%} of the draft is untagged connective prose "
                        f"(cap {CONNECTIVE_SHARE_MAX:.0%}); claims relabelled as linking text "
                        f"escape citation")
        # A conflict is surfaced, never reconciled; both sides must be real citations.
        for c in a.get("conflicts", []):
            for side in ("higher_ref", "lower_ref"):
                if c[side] not in prov:
                    errs.append(f"{iid}: conflict {side} {c[side]!r} does not resolve to "
                                f"provenance[]")
            if c["higher_ref"] in prov and c["lower_ref"] in prov:
                if prov[c["higher_ref"]]["tier"] >= prov[c["lower_ref"]]["tier"]:
                    errs.append(f"{iid}: conflict higher_ref tier {prov[c['higher_ref']]['tier']} "
                                f"is not above lower_ref tier {prov[c['lower_ref']]['tier']}")
        if a.get("needs_diagram") and not a.get("diagram_brief"):
            errs.append(f"{iid}: needs_diagram is true but no diagram_brief for "
                        f"mechanistic-diagram to consume")
        # Two cells at one coordinate: the second silently wins and the first is lost.
        seen_cells = {}
        for c in a.get("table_cells", []):
            rc = (c["row"], c["col"])
            if rc in seen_cells:
                errs.append(f"{iid}: two table_cells both address row {rc[0]} column {rc[1]}; "
                            f"one silently overwrites the other and the loss is invisible")
            seen_cells[rc] = c
    # Single-attempt approvals against the run's approvals file.
    if ctx.approvals is not None:
        for a in doc.get("items", []):
            ap = a.get("single_attempt_approval")
            if not ap:
                continue
            aid = ap.get("approval_id")
            if ctx.approvals.get("module_id") != doc.get("module_id"):
                errs.append(f"{a['item_id']}: the approvals file is for module "
                            f"{ctx.approvals.get('module_id')!r}, the draft is for "
                            f"{doc.get('module_id')!r}")
                continue
            rec = approvals_mod.find(ctx.approvals, aid)
            if rec is None:
                errs.append(f"{a['item_id']}: approval_id {aid} is not in the approvals file")
            elif rec.get("kind") != "single-attempt":
                errs.append(f"{a['item_id']}: approval {aid} is a {rec.get('kind')} approval, not "
                            f"a single-attempt approval")
            elif rec.get("item_id") != a["item_id"]:
                errs.append(f"{a['item_id']}: approval {aid} approves item "
                            f"{rec.get('item_id')!r}, not {a['item_id']!r}")
            elif rec.get("question_digest") != (question_digest(ctx.module)
                                                if ctx.module is not None else bound_q):
                errs.append(f"{a['item_id']}: approval {aid} question_digest differs from "
                            "the capture; request a new approval")
    return errs


def rails_answers_vs_module(doc, module):
    """Cross-checks that need the capture as well as the draft.

    A drafted table-completion answer with no table_cells renders as prose and holds no
    cell anyone can write or read back: it looks complete and is empty.
    """
    errs = []
    if not module:
        return errs
    items = {it["item_id"]: it for p in module.get("pages", []) for it in p.get("items", [])}
    types = {k: v["type"] for k, v in items.items()}
    cons = {k: (v.get("constraints") or {}) for k, v in items.items()}
    opts = {k: (v.get("options") or []) for k, v in items.items()}
    version = doc.get("schema_version", 1)
    for a in doc.get("items", []):
        iid = a["item_id"]
        if iid not in types:
            errs.append(f"{iid}: not an item in the capture this draft is bound to")
            continue
        if a.get("single_attempt_approval") and items[iid].get("attempts") != "single":
            errs.append(f"{iid}: single_attempt_approval on an item the capture does not mark "
                        f"single-attempt; an approval for nothing is a mistake, not a no-op")
        if a.get("status") != "drafted":
            continue
        if types[iid] == "table-completion" and not a.get("table_cells"):
            errs.append(f"{iid}: drafted table-completion with no table_cells; the segments are "
                        f"a summary, and the cells are the answer. Nothing can write or export "
                        f"an empty grid")
        if a.get("table_cells") and types[iid] != "table-completion":
            errs.append(f"{iid}: table_cells on a {types[iid]} item, which has no grid")
        # label_placements holds the whole answer of a labelling or categorisation item.
        # Required from schema_version 2; earlier drafts stay valid.
        placements = a.get("label_placements", [])
        if types[iid] in PLACEMENT_TYPES:
            if version >= 2 and not placements:
                errs.append(f"{iid}: drafted {types[iid]} with no label_placements; the "
                            f"segments are a summary, and the placements are the answer. "
                            f"Listing the label pool is copying the question back out")
        elif placements:
            errs.append(f"{iid}: label_placements on a {types[iid]} item, which has no "
                        f"label pool to place")
        if placements:
            # A multiset: the same label may be placed at two anchors.
            pool = collections.Counter(o["text"] for o in (opts.get(iid) or []))
            seen = collections.Counter(pl["label"] for pl in placements)
            if pool:
                for lab in (seen - pool):
                    if lab not in pool:
                        errs.append(f"{iid}: label {lab!r} is not one of this item's options; "
                                    f"invented vocabulary, not a placement")
                    else:
                        errs.append(f"{iid}: label {lab!r} placed {seen[lab]} times but appears "
                                    f"{pool[lab]} time(s) in the pool")
                # A pool larger than its drop zones holds distractors: the right answer
                # leaves (pool - locations) labels unplaced. The capture's note carries
                # `locations=N`; without it every label must be placed.
                m_loc = re.search(r"locations=(\d+)", str(cons.get(iid, {}).get("note") or ""))
                spare = max(0, sum(pool.values()) - int(m_loc.group(1))) if m_loc else 0
                unplaced = pool - seen
                if sum(unplaced.values()) > spare:
                    for lab in unplaced:
                        errs.append(f"{iid}: label {lab!r} is placed {seen[lab]} time(s) and the "
                                    f"pool has {pool[lab]}; a partial mapping is an undrafted "
                                    f"item under a heading that looks finished")
            # A reading off an image caps at medium and then owes its competing reading.
            for pl in placements:
                if pl.get("confidence") in ("medium", "low") and not pl.get("alternative"):
                    errs.append(f"{iid}: placement {pl['label']!r} is {pl['confidence']} "
                                f"confidence with no alternative; name the structure it might "
                                f"sit on instead")
                if pl.get("target_index") is not None and types[iid] != "categorization":
                    errs.append(f"{iid}: placement {pl['label']!r} carries target_index on a "
                                f"{types[iid]} item. Anchor order does not follow label order and "
                                f"capture emits no anchor coordinates, so the index is a guess")
        # A cell outside the captured grid cannot be written; the fill would find out live.
        rows, cols = cons.get(iid, {}).get("rows"), cons.get(iid, {}).get("cols")
        for c in a.get("table_cells", []):
            if cols is not None and c["col"] >= cols:
                errs.append(f"{iid}: table cell column {c['col']} is outside the captured grid "
                            f"({cols} columns)")
            if rows is not None and c["row"] >= rows:
                errs.append(f"{iid}: table cell row {c['row']} is outside the captured grid "
                            f"({rows} rows, header row included)")
    return errs


# --- fill-report.json ----------------------------------------------------------------

def _aware(v):
    """An ISO 8601 timestamp with an offset as a datetime, or None."""
    if not isinstance(v, str):
        return None
    try:
        t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def _plain_date(v):
    """True for a real calendar date written YYYY-MM-DD."""
    try:
        return len(v) == 10 and dt.date.fromisoformat(v) is not None
    except (TypeError, ValueError):
        return False


def _receipt_rails(doc):
    """``verified`` is the verdict of a fresh-load hash receipt. Where an item carries one,
    the result and verified flag must be what the receipt says, and the load must fall
    inside the run. A report built from receipts may not claim ``filled`` without one."""
    errs = []
    start, end = _aware(doc.get("started_at")), _aware(doc.get("ended_at"))
    from_receipts = "source_receipts" in doc
    for it in doc.get("items", []):
        iid, r = it["item_id"], it.get("receipt")
        if r is None:
            if from_receipts and it.get("result") == "filled":
                errs.append(f"{iid}: result='filled' with no receipt in a report built from "
                            f"source_receipts; verified comes from a fresh-load hash receipt, "
                            f"never from a literal")
            continue
        match = str(r.get("expected_hash")) == str(r.get("observed_hash"))
        want = "filled" if match else "failed"
        if not it.get("attempted"):
            errs.append(f"{iid}: a fresh-load receipt on an item reported attempted=false")
        if bool(it.get("verified")) != match or it.get("result") != want:
            errs.append(f"{iid}: result={it.get('result')!r}, verified={it.get('verified')!r}, "
                        f"but the receipt's hashes {'match' if match else 'differ'} (expected "
                        f"{r.get('expected_hash')}, observed {r.get('observed_hash')}), which "
                        f"gives result={want!r}, verified={match}; the receipt decides")
        seen = _aware(r.get("observed_at"))
        if seen and start and seen < start:
            errs.append(f"{iid}: receipt observed_at {r['observed_at']} precedes started_at "
                        f"{doc['started_at']}; a load taken before the run cannot verify its write")
        if seen and end and seen > end:
            errs.append(f"{iid}: receipt observed_at {r['observed_at']} is after ended_at "
                        f"{doc['ended_at']}; a run ends after its last check, so ended_at is wrong")
    return errs


def _apply_waivers(doc, held, module_checked, ctx: _Ctx):
    """Each (rail, item_id, message) in ``held`` violates a waivable rail. It passes only
    where a user_decisions entry names that rail and, for a per-item rail, that item.
    A waiver must rest on a recorded approval and must waive something: one that matches
    no violation would pass the next one unread."""
    errs = []
    decisions = doc.get("user_decisions") or []
    used = [set() for _ in decisions]
    for rail, iid, msg in held:
        k = next((k for k, d in enumerate(decisions) if d.get("rail") == rail
                  and (iid is None or iid in (d.get("item_ids") or []))), None)
        if k is None:
            errs.append(msg)
            continue
        d = decisions[k]
        used[k].add(iid)
        ctx.waived.append(f"{rail}:{iid}" if iid else rail)
        quote = f' Quote: "{d["quote"]}"' if d.get("quote") else ""
        ctx.notes.append(f"waived {rail}" + (f" for {iid}" if iid else "")
                         + f" by user_decisions[{k}]: {d.get('who')}, {d.get('when')}, "
                           f"{d.get('approval_id')}. {d.get('why')}{quote}")
    if decisions and ctx.approvals is None:
        ctx.notes.append("no --approvals given; user_decisions approval_id values were checked "
                         "for format only")
    for k, d in enumerate(decisions):
        rail, tag = d.get("rail"), f"user_decisions[{k}] ({d.get('rail')})"
        for p in approvals_mod.check_waiver(ctx.approvals, doc.get("module_id"),
                                            d.get("approval_id"), rail, d.get("item_ids"),
                                            question_digest=(question_digest(ctx.module)
                                                             if ctx.module is not None else None)):
            errs.append(f"{tag}: {p}")
        when = d.get("when")
        if not (isinstance(when, str) and (_aware(when) or _plain_date(when))):
            errs.append(f"{tag}: when={when!r} is neither a YYYY-MM-DD date nor a timestamp "
                        f"with an offset")
        if rail in MODULE_RAILS and not module_checked:
            continue
        if WAIVABLE_RAILS.get(rail):
            idle = sorted(set(d.get("item_ids") or []) - used[k])
            if idle:
                errs.append(f"{tag}: waives {idle}, but no violation of that rail names them; "
                            f"a waiver that waives nothing would pass the next violation unread")
        elif None not in used[k]:
            errs.append(f"{tag}: waives nothing, because the rail is not violated; a waiver "
                        f"that waives nothing would pass the next violation unread")
    return errs


def rails_fill(doc, ctx: _Ctx):
    module = ctx.module
    errs = _unique_item_ids(doc)
    source = doc.get("source_answers") or {}
    rel, want = source.get("path"), source.get("sha256")
    answers_path = _resolve_source(rel, ctx.base_dir) if rel else None
    if answers_path is None:
        errs.append(f"source_answers.path {rel!r} does not resolve to a readable answers file; "
                    "the binding cannot be verified")
    else:
        try:
            got = hashlib.sha256(answers_path.read_bytes()).hexdigest()
        except OSError:
            got = None
        if want == "0" * 64 or got != want:
            errs.append(f"source_answers.sha256 {want!r} differs from the referenced answers "
                        f"file hash {got!r}; the fill report must name the answers it used")
    held = []  # (rail, item_id, message) for each violation a recorded approval may waive
    br = doc.get("boundary_reached", {})
    doors = ", ".join(sorted(ONE_WAY_DOORS))
    if br.get("class") == "unmapped":
        errs.append(
            "boundary_reached.class='unmapped'; an unmapped item type is a gap in this suite, "
            "not a one-way door in Lt, and it may not stop a run. Skipping the item is a "
            "complete defence because nothing is written; record it as an item with "
            f"result='todo' and let the run continue. Legitimate stopping classes are {doors}.")
    if br.get("stopped") and br.get("class") not in ONE_WAY_DOORS:
        errs.append(f"boundary_reached.stopped=true with class={br.get('class')!r}, which is not "
                    f"a one-way door; a run may only be truncated by {doors}.")
    if doc.get("approval_mode") != "manual" and br.get("stopped"):
        held.append(("approval-manual-at-boundary", None,
                     "a run that met a commit boundary must have approval_mode=manual; a page "
                     "containing a boundary is always filled under manual approval"))
    routes = {n["route"] for n in doc.get("never_navigated", [])}
    mod_items = ({it["item_id"]: it for p in module.get("pages", []) for it in p.get("items", [])}
                 if module else {})
    for it in doc.get("items", []):
        iid = it["item_id"]
        captured = mod_items.get(iid) or {}
        attempts = captured.get("attempts") or it.get("attempts")
        if (attempts == "single" and it.get("result") in WRITTEN
                and it.get("method") != "manual-todo"):
            typ = captured.get("type") or it.get("item_type")
            failed = "; a failed write still reached the panel" if it["result"] == "failed" else ""
            if typ in DRAG_TYPES:
                errs.append(f"{iid}: single-attempt item reported as {it['result']!r} by "
                            f"automation on a {typ} item; a drag on a single-attempt item is "
                            f"never automated{failed}")
            elif not it.get("approval_id"):
                errs.append(f"{iid}: single-attempt item reported as {it['result']!r} by "
                            f"automation with no approval_id; a single-attempt item is written "
                            f"only with the user's recorded approval{failed}")
            elif ctx.approvals is not None:
                rec = approvals_mod.find(ctx.approvals, it["approval_id"])
                if ctx.approvals.get("module_id") != doc.get("module_id"):
                    errs.append(f"{iid}: the approvals file is for module "
                                f"{ctx.approvals.get('module_id')!r}, the report is for "
                                f"{doc.get('module_id')!r}")
                elif rec is None or rec.get("kind") != "single-attempt" or rec.get("item_id") != iid:
                    errs.append(f"{iid}: approval_id {it['approval_id']} is not a single-attempt "
                                f"approval for this item in the approvals file")
                elif module is None or rec.get("question_digest") != question_digest(module):
                    errs.append(f"{iid}: approval {it['approval_id']} question_digest cannot "
                                "be verified against the supplied capture")
        if it.get("attempted") and it.get("method") == "not-attempted":
            errs.append(f"{iid}: attempted=true with method=not-attempted")
        if it.get("result") == "filled" and not it.get("verified"):
            errs.append(f"{iid}: result=filled without verified=true; use filled-unverified "
                        f"(every item is verified before the run moves on)")
        # lt_conflict records a disagreement with Lt's marking so it reaches the review
        # packet. A conflict presupposes an answer written and left standing.
        c = it.get("lt_conflict")
        if c:
            if not it.get("attempted"):
                errs.append(f"{iid}: lt_conflict recorded for an unattempted item; with no answer "
                            f"written, Lt's marking has nothing to contradict")
            if it.get("result") not in CLAIMED:
                errs.append(f"{iid}: lt_conflict with result={it.get('result')!r}; the "
                            f"researched answer STAYS written on the page, so the only coherent "
                            f"results are filled and filled-unverified. Clearing or reverting an "
                            f"answer to resolve a disagreement is what the field exists to "
                            f"record instead of")
            if c.get("held_answer") == c.get("lt_indicated"):
                errs.append(f"{iid}: lt_conflict.held_answer and lt_indicated are the same text, "
                            f"so nothing is in conflict; record a conflict only where the "
                            f"marking contradicts the cited answer")
            if c.get("observed_via") == "model-answer" and not ctx.model_answer_read:
                errs.append(_policy_error(f"{iid}: lt_conflict observed_via 'model-answer'", ctx))
    errs += _receipt_rails(doc)
    if module:
        # The completion page is out of bounds, not merely stop-before-Commit.
        if module.get("lesson", {}).get("has_completion_page"):
            route = module["lesson"].get("completion_route") or "complete"
            if route not in routes:
                errs.append(f"module has a completion page but its route {route!r} is not listed "
                            f"in never_navigated; the completion page is never visited")
            report_idx = [p["page_index"] for p in module.get("pages", [])
                          if p.get("is_report_page")]
            for idx in report_idx:
                if idx in doc.get("pages_filled", []):
                    errs.append(f"pages_filled includes the report page (page_index {idx}); the "
                                f"report page is never visited")
        stop = first_stop_boundary(module)
        page_of = {it["item_id"]: p.get("page_index")
                   for p in module.get("pages", []) for it in p.get("items", [])}
        if stop:
            door = (f"the first one-way door ({stop[1]} on page {stop[0]}"
                    + (f", item {stop[2]}" if stop[2] else "") + ")")
            past = sorted(i for i in doc.get("pages_filled", []) if i >= stop[0])
            if past:
                errs.append(f"pages_filled includes page(s) {past} at or past {door}; a run "
                            f"never advances to or through it")
            # A write through the view model lands on another page without a visit, so each
            # write is checked by its own page as well. A failed write counts.
            for it in doc.get("items", []):
                pg = page_of.get(it["item_id"])
                if it.get("result") in WRITTEN and pg is not None and pg >= stop[0]:
                    held.append(("write-past-first-door", it["item_id"],
                                 f"{it['item_id']}: result={it['result']!r} on page {pg}, at or "
                                 f"past {door}; plan_fill plans no item there"
                                 + ("; a failed write still touched the item"
                                    if it["result"] == "failed" else "")))
        # A submitted single sets no door, but whether its Submit still works is
        # unmeasured. A run that reaches its page is on a page holding that Submit control:
        # manual approval, and the item listed as not attempted. A run that writes to any
        # later page counts as having reached it.
        report_items = {it["item_id"]: it for it in doc.get("items", [])}
        written = {page_of.get(it["item_id"]) for it in doc.get("items", [])
                   if it.get("result") in WRITTEN}
        visited = set(doc.get("pages_filled", [])) | (written - {None})
        passing = single_pass_pages(module)
        for p in module.get("pages", []):
            idx = p.get("page_index")
            reached = sorted(i for i in visited if idx in passing and i >= idx)
            if not reached:
                continue
            for it in (i for i in p.get("items", []) if submitted_single(i)):
                if doc.get("approval_mode") != "manual":
                    held.append(("passed-single-approval", it["item_id"],
                                 f"the run reaches page(s) {reached} by pages_filled or a written "
                                 f"item, at or past page {idx}, which holds the submitted "
                                 f"single-attempt item {it['item_id']}; a run that reaches it must "
                                 f"have approval_mode=manual, because whether its Submit still "
                                 f"works is unmeasured"))
                r = report_items.get(it["item_id"])
                if r is None or r.get("attempted") or r.get("result") not in {"skipped", "todo"}:
                    errs.append(f"{it['item_id']}: submitted single-attempt item on page {idx}, "
                                f"reached by a run that wrote to page(s) {reached}, must be listed "
                                f"with attempted=false and result skipped or todo")
        # A write to a locked panel reports success locally and never reaches the server,
        # so a report claiming one claims an answer Lt does not hold. If the fill ran before
        # a commit, validate against the capture taken before it.
        locks = inferred_locks(module)
        for it in doc.get("items", []):
            if it["item_id"] not in mod_items:
                errs.append(f"{it['item_id']}: not present in the supplied module.json")
                continue
            if it.get("result") not in CLAIMED:
                continue
            if mod_items[it["item_id"]].get("read_only") is True:
                errs.append(f"{it['item_id']}: result={it['result']!r} on a panel the capture "
                            f"reads read_only true; Lt accepts no write there")
            elif it["item_id"] in locks and (locks[it["item_id"]]["class"] == "read-only-inferred"
                                             or it["result"] == "filled-unverified"):
                # lock-unknown alone is a guess, so a fresh-load-verified write settles it.
                lk = locks[it["item_id"]]
                errs.append(f"{it['item_id']}: result={it['result']!r} on a subpage child "
                            f"plan_fill holds back as {lk['class']} ({lk['cause']}); a write "
                            f"there reports success locally and never reaches the server")
        for p in module.get("pages", []):
            if p.get("policy_skip") and p["page_index"] in doc.get("pages_filled", []):
                errs.append(f"pages_filled includes page_index {p['page_index']}, skipped by "
                            f"standing policy ({p['policy_skip']['reason']})")
            if p.get("policy_skip"):
                ids = {it["item_id"] for it in p.get("items", [])}
                for it in doc.get("items", []):
                    if it["item_id"] in ids and it.get("result") in WRITTEN:
                        errs.append(f"{it['item_id']}: result={it['result']!r} on page "
                                    f"{p['page_index']}, skipped by standing policy "
                                    f"({p['policy_skip']['reason']}); every write attempt counts")
    from ltcore import check_answer
    errs += check_answer.rails(doc, module, policy=ctx.rat_policy)
    if module and doc.get('approval_mode') != 'manual':
        pressed={i['item_id'] for i in doc['items'] if (i.get('check_answer') or {}).get('presses')}
        if any(any(i.get('attempts')=='single' for i in p['items']) and any(i['item_id'] in pressed for i in p['items']) for p in module['pages']):
            if not any(r[0]=='approval-manual-at-boundary' for r in held):
                held.append(('approval-manual-at-boundary',None,'approval-manual-at-boundary: a check page holds a single-attempt item'))
    return errs + _apply_waivers(doc, held, module is not None, ctx)


# --- reference.json, run.json, approvals.json ----------------------------------------

RAT_RUN_FILE = re.compile(
    r"(?:^|/)(?:page-kinds[^/]*\.json|fill-payload[^/]*\.json|fill-report[^/]*\.json|"
    r"check-answer[^/]*\.json|reconcile-worklist[^/]*\.json|reconciled[^/]*\.json|"
    r"fill-js(?:/|$)|logs(?:/|$))", re.I)


def _rat_source(value, ctx):
    if not isinstance(value, str):
        return False
    from urllib.parse import unquote
    value = unquote(value).replace("\\", "/")
    if RAT_RUN_FILE.search(value):
        return True
    value = value.split("#", 1)[0].split("?", 1)[0]
    if value.startswith("file://"):
        value = value[7:]
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ctx.base_dir / path
    return any(rat.within(path, root) for root in ctx.rat_roots)


def rails_rat_quarantine(doc, ctx):
    """Cited RAT roots and answer-bearing run files never become drafting evidence."""
    errors = []
    def walk(node, corpus=False):
        if isinstance(node, dict):
            for key, value in node.items():
                if (key in {"source_id", "locator"} or corpus and key == "path") and _rat_source(value, ctx):
                    errors.append("rat-root-quarantine: RAT sheets and run artifacts cannot be sources")
                walk(value, corpus or key == "tier1_corpus")
        elif isinstance(node, list):
            for child in node:
                walk(child, corpus)
    walk(doc)
    return errors


def rails_rat_drafts(doc, ctx):
    if not ctx.module:
        return []
    record = ctx.page_kinds
    statuses = {s["page_index"]: s for s in page_kinds.page_status(ctx.module, record, policy=ctx.rat_policy)}
    drafted = {a["item_id"] for a in doc.get("items", []) if a.get("status") == "drafted"}
    if "source_answers" in doc:
        rel = (doc.get("source_answers") or {}).get("path")
        if rel and not _rat_source(rel, ctx):
            path = Path(rel) if Path(rel).is_absolute() else ctx.base_dir / rel
            try:
                drafted = {a["item_id"] for a in json.loads(path.read_text(encoding="utf-8"))["items"] if a.get("status") == "drafted"}
            except (OSError, ValueError, KeyError):
                pass
    errors = []
    for page in ctx.module["pages"]:
        status = statuses[page["page_index"]]
        text, kind = page_kinds._text_evidence(page, ctx.rat_policy)
        if record and page_kinds.page_current(page, record):
            known_rat, known_kind = status["rat"] is True, status["kind"]
        else:
            known_rat, known_kind = text, kind
        if known_rat and known_kind != "trat" and any(it["item_id"] in drafted for it in page["items"]):
            errors.append("rat-irat-drafted: an individual or unknown-kind RAT cannot be drafted")
    return errors


def rails_rat_fill(doc, ctx):
    errors = []
    pages = {r["page_index"]: r for r in doc.get("rat_pages") or []}
    item_pages = {i["item_id"]: p["page_index"] for p in (ctx.module or {}).get("pages", []) for i in p["items"]}
    for item in doc.get("items", []):
        src = item.get("answer_source")
        if not src:
            continue
        idx = item_pages.get(item["item_id"])
        row = pages.get(idx, {})
        if row.get("decision") != "fill-from-sheet" or ctx.page_kinds is None:
            errors.append("rat-sheet-decision: a sheet item requires a pinned fill-from-sheet decision")
        if src.get("sha256") != row.get("sheet", {}).get("sha256"):
            errors.append("rat-sheet-hash: item and page sheet hashes differ")
        record = ctx.page_kinds or {}
        policy = record.get("scan", {}).get("policy", {})
        kind = row.get("kind")
        mode = policy.get(kind or "irat")
        choice = any(c["page_index"] == idx and c.get("fill") == "sheet" and
                     (not c.get("sheet") or c["sheet"]["sha256"] == src.get("sha256")) for c in record.get("choices", []))
        if mode != "from_dir" and not choice:
            errors.append("rat-sheet-policy: sheet reading needs from_dir or a current sheet choice")
        if record and ctx.module:
            page = next((p for p in ctx.module["pages"] if p["page_index"] == idx), None)
            decision = rat.decide(page, policy, record) if page else {}
            if decision.get("decision") != "fill-from-sheet":
                errors.append("rat-sheet-decision: shared decision does not permit sheet entry")
            else:
                pinned = decision.get("sheet") or {}
                for reported in (src, row.get("sheet") or {}):
                    if any(reported.get(k) != pinned.get(k) for k in ("root", "name", "sha256")):
                        errors.append("rat-sheet-hash: reported sheet identity differs from pinned decision")
    return errors


def rails_reference(doc, ctx: _Ctx):
    errs = []
    prov = doc.get("provenance_of_reference")
    if prov == MODEL_ANSWER_SOURCE and not ctx.model_answer_read:
        errs.append(_policy_error("provenance_of_reference", ctx))
    if not doc.get("never_load_into_drafting_context"):
        errs.append("never_load_into_drafting_context must be true; this file is grader "
                    "input only")
    seen = set()
    for it in doc.get("items", []):
        if it["item_id"] in seen:
            errs.append(f"duplicate item_id {it['item_id']}")
        seen.add(it["item_id"])
    return errs


def rails_run(doc, ctx: _Ctx | None = None):
    errs = []
    for d in doc.get("diff_classification", []):
        if d.get("steered_run_wrong") and not (d.get("before") and d.get("after")):
            errs.append(f"{d['item_id']}: steered_run_wrong is true but the diff carries no "
                        f"before/after evidence; only evidence drives a skill edit")
    return errs


def rails_approvals(doc, ctx: _Ctx | None = None):
    """Ids recompute from their records and are unique; the rest is the schema's job."""
    return [p for p in approvals_mod.problems(doc)
            if "does not match its record" in p or "appears twice" in p]


# --- driver --------------------------------------------------------------------------

def _load_schema(schemas_dir: Path, schema_name: str) -> dict:
    path = Path(schemas_dir) / schema_name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValidationSetupError(f"cannot load schema {path}: {e}") from e


def _schema_errors(schema: dict, doc) -> list[str]:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as e:
        from ltcore import missing_hint
        raise ValidationSetupError(missing_hint(["jsonschema"])) from e
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(doc),
                    key=lambda e: [str(p) for p in e.absolute_path])
    out = []
    for e in errors:
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        out.append(f"[{loc}] {e.message}")
    return out


def _model_answer_setting(model_answer_read, config_path) -> tuple[bool, str]:
    if model_answer_read is not None:
        return bool(model_answer_read), "flag --model-answer-read"
    from ltcore import config
    try:
        cfg = config.load(config_path)
    except config.ConfigError as e:
        raise ValidationSetupError(f"config: {e}") from e
    return bool(cfg.get("model_answer.read", True)), cfg.where("model_answer.read")


def validate_doc(doc, schema_name: str, *, module: dict | None = None,
                 approvals: dict | None = None, model_answer_read: bool | None = None,
                 config_path: str | os.PathLike | None = None,
                 schemas_dir: str | os.PathLike | None = None,
                 base_dir: str | os.PathLike | None = None, document_sha256: str | None = None,
                 module_path: str | os.PathLike | None = None) -> Result:
    """Validate a parsed document. ``schema_name`` is e.g. "module" or
    "module.schema.json". Relative source paths resolve against ``base_dir``.

    Module items and authored panels may carry ``hidden_text_dropped`` counts
    and ``suspect_text`` reason codes. The module schema checks these optional
    fields; they remain capture data and do not waive any rail.
    """
    if not schema_name.endswith(".json"):
        schema_name += ".schema.json"
    res = Result(schema_name)
    pre_notes = []
    if schema_name in ("module.schema.json", "run.schema.json") and isinstance(doc, dict):
        doc = dict(doc)  # never mutate the caller's document
        dropped = drop_legacy_fields(doc)
        if dropped:
            pre_notes.append(legacy_note(dropped))
    try:
        if approvals is not None:
            bad = approvals_mod.problems(approvals)
            if bad:
                raise ValidationSetupError("approvals file: " + "; ".join(bad))
        schema = _load_schema(Path(schemas_dir or SCHEMAS_DIR), schema_name)
        res.schema_errors = _schema_errors(schema, doc)
        if res.schema_errors:
            res.exit_code, res.notes = 2, pre_notes
            return res
        ctx = _Ctx(base_dir=Path(base_dir or os.getcwd()), module=module, approvals=approvals,
                   notes=pre_notes, module_path=Path(module_path) if module_path else None)
        if schema_name in ("answers.schema.json", "reference.schema.json",
                           "fill-report.schema.json", "reconciled.schema.json"):
            ctx.model_answer_read, ctx.model_answer_where = _model_answer_setting(
                model_answer_read, config_path)
    except ValidationSetupError as e:
        res.fatal, res.exit_code = str(e), 3
        return res
    if schema_name in ("answers.schema.json", "fill-report.schema.json", "reconciled.schema.json"):
        from ltcore import config as config_mod
        try:
            cfg = config_mod.load(config_path)
            ctx.meta_text_patterns = cfg.get("answers.meta_text_patterns", DEFAULT_META_TEXT_PATTERNS)
            ctx.rat_policy = rat.policy_from_config(cfg)
            ctx.rat_roots = [p for _, p in rat.effective_roots(ctx.rat_policy)]
            if module and schema_name == "answers.schema.json":
                ctx.page_kinds = page_kinds.load_current(module, cfg, doc, answers_sha256=document_sha256)
            pin = doc.get("source_page_kinds")
            # Check authorization uses recorded status, independent of today's store.
            # Still load a valid fill classification pin for ordinary draft rails.
            # Sheet and explicit tRAT draft rails require that pin to resolve.
            needs_record = not (schema_name == 'fill-report.schema.json' and doc.get('check_answer')) or any(i.get('answer_source') for i in doc.get('items',[])) or any(r.get('decision')=='fill-from-draft' for r in doc.get('rat_pages',[]))
            if pin:
                try:
                    path = Path(pin["path"])
                    path = path if path.is_absolute() else ctx.base_dir / path
                    if any(rat.within(path, root) for root in ctx.rat_roots):
                        raise ValueError("page-kinds pin enters a RAT root")
                    raw = path.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != pin["sha256"]:
                        raise ValueError("page-kinds pin mismatch")
                    ctx.page_kinds = page_kinds.validate_record(json.loads(raw))
                except (OSError, ValueError):
                    if needs_record:
                        raise
        except (config_mod.ConfigError, OSError, ValueError):
            res.fatal, res.exit_code = "invalid RAT config or page-kinds pin", 3
            return res
    errs = scan_denied_keys(doc) + scan_timestamps(doc)
    if (module is not None and schema_name in ("answers.schema.json", "fill-report.schema.json")
            and module.get("module_id") != doc.get("module_id")):
        errs.append(f"module_id {doc.get('module_id')!r} differs from the supplied capture's "
                    f"module_id {module.get('module_id')!r}; use the matching capture")
    if schema_name == "fill-report.schema.json":
        errs += rails_fill(doc, ctx) + rails_rat_fill(doc, ctx) + rails_rat_drafts(doc, ctx)
        if module is None:
            ctx.notes.append("no --module given; the completion-page and door cross-checks "
                             "were not run")
    elif schema_name == "answers.schema.json":
        errs += rails_answers(doc, ctx) + rails_answers_vs_module(doc, module) + rails_rat_quarantine(doc, ctx) + rails_rat_drafts(doc, ctx)
        if module is None:
            ctx.notes.append("no --module given; the table_cells and label_placements "
                             "cross-checks were not run")
    elif schema_name == "reconciled.schema.json":
        from ltcore.reconcile import rails_reconciled
        errs += rails_reconciled(doc, ctx) + rails_rat_quarantine(doc, ctx)
    elif schema_name == "module.schema.json":
        errs += rails_module(doc, ctx)
    elif schema_name == "reference.schema.json":
        errs += rails_reference(doc, ctx)
    elif schema_name == "run.schema.json":
        errs += rails_run(doc, ctx)
    elif schema_name == "approvals.schema.json":
        errs += rails_approvals(doc, ctx)
    res.rail_errors, res.notes, res.waived = errs, ctx.notes, ctx.waived
    res.exit_code = 1 if errs else 0
    return res


def _read_json(path: str | os.PathLike, what: str):
    p = Path(path)
    if not p.is_file():
        raise ValidationSetupError(f"no such {what} file: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValidationSetupError(f"{p}: {e}") from e


def validate_file(path: str | os.PathLike, schema: str | None = None, *,
                  module: str | os.PathLike | dict | None = None,
                  approvals: str | os.PathLike | dict | None = None,
                  model_answer_read: bool | None = None,
                  config_path: str | os.PathLike | None = None,
                  schemas_dir: str | os.PathLike | None = None) -> Result:
    """Validate a file. ``module`` and ``approvals`` take a path or a parsed document."""
    p = Path(path)
    name = schema or infer_schema(p)
    if not name:
        return Result(schema or "", exit_code=3,
                      fatal=f"cannot infer a schema from {p.name}; pass --schema")
    if not name.endswith(".json"):
        name += ".schema.json"
    try:
        doc = _read_json(p, "instance")
        mod = module if isinstance(module, dict) or module is None else _read_json(module, "module")
        if isinstance(approvals, dict) or approvals is None:
            appr = approvals
        else:
            appr = _read_json(approvals, "approvals")
            bad = approvals_mod.problems(appr)
            if bad:
                raise ValidationSetupError(f"approvals file {approvals}: " + "; ".join(bad))
    except ValidationSetupError as e:
        return Result(name, exit_code=3, fatal=str(e))
    return validate_doc(doc, name, module=mod, approvals=appr, model_answer_read=model_answer_read,
                        config_path=config_path, schemas_dir=schemas_dir, base_dir=p.parent, document_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                        module_path=module if isinstance(module, (str, os.PathLike)) else None)


def _bool_arg(v: str) -> bool:
    low = v.strip().lower()
    if low in ("true", "1", "yes", "on"):
        return True
    if low in ("false", "0", "no", "off"):
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {v!r}")


def main(argv: list[str] | None = None) -> int:
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(prog="python3 -m ltcore.validate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("instance")
    ap.add_argument("--schema", help="schema name, e.g. module or fill-report")
    ap.add_argument("--schemas-dir", default=str(SCHEMAS_DIR))
    ap.add_argument("--module", help="module.json to cross-check a fill report or answers file")
    ap.add_argument("--approvals", help="the run's approvals.json")
    ap.add_argument("--config", help="config file (for model_answer.read)")
    ap.add_argument("--model-answer-read", type=_bool_arg, default=None, metavar="true|false",
                    help="override model_answer.read for this check")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    res = validate_file(args.instance, args.schema, module=args.module, approvals=args.approvals,
                        model_answer_read=args.model_answer_read, config_path=args.config,
                        schemas_dir=args.schemas_dir)
    name = Path(args.instance).name
    if res.exit_code == 3:
        print(f"FATAL: {res.fatal}", file=sys.stderr)
        return 3
    if res.schema_errors:
        print(f"SCHEMA FAIL: {name} against {res.schema_name}: {len(res.schema_errors)} error(s)")
        for e in res.schema_errors:
            print(f"  {e}")
        return 2
    if res.notes and not args.quiet:
        for n in res.notes:
            print(f"  note: {n}")
    if res.rail_errors:
        print(f"RAIL FAIL: {name}: {len(res.rail_errors)} violation(s)")
        for e in res.rail_errors:
            print(f"  - {e}")
        return 1
    if not args.quiet:
        waived = (f"; {len(res.waived)} violation(s) waived by user_decisions, each printed above"
                  if res.waived else "")
        print(f"OK: {name} valid against {res.schema_name}; all rails pass{waived}")
    return 0


def _needs_probe() -> None:
    """Open every schema this module reads (tests/core/test_needs.py)."""
    for _, schema in SCHEMA_BY_PREFIX:
        _load_schema(SCHEMAS_DIR, schema)


if __name__ == "__main__":
    raise SystemExit(main())
