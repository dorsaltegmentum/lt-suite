# Generated from shared/ltcore/boundaries.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Commit boundaries of a captured module: one implementation for planner and validator.

A one-way door is a property of Lt: crossing it locks the student's work and cannot be
undone. A gap is a property of this suite: one item has no tested write path. The two
get different severities, because a gap that stops a run hides every later page.

    class                severity        effect
    checkpoint-page      stop            the run stops before this page (stop_before_page)
    single-attempt-item  per-item        the item is never written without a recorded
                                         approval and never submitted; later pages are
                                         filled, because moving between pages submits
                                         nothing
    unmapped             per-item        the item becomes a TODO; the run continues
    run-level-commit     never-navigate  the completion route is never visited
    report-page          never-navigate  never visited; its panels mirror real ones and
                                         its panel keys repeat keys of real pages

``first_stop_boundary(module)`` returns the first stop-severity door as
``(page_index, class, item_id)``. ``boundary_map(module)`` lists every door with its
severity, the routes never to visit, and the doors the capture shows already passed.

A submitted single-attempt item (``attempts`` single, ``read_only`` true, ``answered``
true) is listed as passed only on a page that a page-level lock covers: the lesson
reads ``run_locked``, or a committed checkpoint has left the flow and the page reads
locked. Anywhere else its Submit control may still work, so it stays a door. The lock
helpers below say which pages a lock covers and which subpage children inherit it.
"""
from __future__ import annotations

NEEDS: list[str] = []

SEVERITY = {
    "checkpoint-page": "stop",
    "single-attempt-item": "per-item",
    "run-level-commit": "never-navigate",
    "report-page": "never-navigate",
    "unmapped": "per-item",
}
# Classes that truncate a fill plan.
STOP_CLASSES = frozenset(c for c, sev in SEVERITY.items() if sev == "stop")
# Classes a fill report may name as the reason it stopped: every one-way door. A run may
# stop early at any door; `unmapped` is a gap in this suite, never a reason to stop.
ONE_WAY_DOORS = frozenset({"run-level-commit", "checkpoint-page", "single-attempt-item"})

# The item types whose answer is a label-to-target mapping (answers.label_placements).
PLACEMENT_TYPES = ("image-labeling", "drag-and-drop-labeling", "categorization")
# Containers, never a thing to type into.
NOT_FILLABLE = frozenset({"subpage"})


def submitted_single(it: dict) -> bool:
    """A single-attempt item the capture reads as submitted.

    All three fields must read literally True; null or false is no evidence of a Submit.
    The item stops being a door only on a page that single_pass_pages() names.
    """
    return (it.get("attempts") == "single" and it.get("read_only") is True
            and it.get("answered") is True)


def lock_cause(module: dict) -> str | None:
    """The page-level cause that locks pages in this capture, or None.

    ``lesson.run_locked``, or a checkpoint that is committed and has left the flow. A
    committed checkpoint that remains visible in the flow still requires a stop.
    """
    if (module.get("lesson") or {}).get("run_locked") is True:
        return "The lesson reads run_locked true"
    committed = [c for c in ((module.get("commit_boundaries") or {}).get("checkpoints") or [])
                 if c.get("committed") and not c.get("in_flow")]
    if committed:
        return f"Checkpoint index {committed[0].get('index')} is committed"
    return None


def lock_evidence(it: dict) -> bool:
    """Whether a top-level item's measured ``read_only`` can speak for its page's lock.

    Left out: subpage children (the capture reads their lock as null), subpage
    containers (no flag), unmapped items and notebooks (a notebook reads false on
    locked pages; excluded by plugin_key too, so one that gains a write path stays
    out), and items that refuse a write on their own account: a submitted single, a
    mirror, a permanent degrade.
    """
    return (not it.get("parent_item_id") and it.get("type") not in NOT_FILLABLE
            and it.get("type") != "unmapped" and it.get("plugin_key") != "notebook"
            and not submitted_single(it) and not it.get("mirror_of")
            and (it.get("degrade") or {}).get("class") != "permanent")


def lock_reach(module: dict) -> dict | None:
    """The pages a page-level lock covers, or None when the capture shows none.

    Returns {cause, run_locked, pages, evidence, locked}: the non-report pages inside
    the reach in page order, each page's lock_evidence() items, and the pages inside the
    reach whose evidence all reads true, each with those item ids.

    Pages before a committed checkpoint lock together. The checkpoint's own position is
    not recorded after a commit (page_index is null), so the reach ends at the first
    page with an evidence item that does not read true, or at a rat_candidate page that
    follows a page read locked (a submitted RAT may read locked, so evidence alone could
    carry the reach past the checkpoint). A page after the end that reads locked holds a
    lock of its own. A checkpoint commit provides no lock evidence unless a page within
    its reach reports a lock. Under run_locked the reach is the whole run.
    """
    cause = lock_cause(module)
    if cause is None:
        return None
    pages = sorted((p for p in module.get("pages", [])
                    if p.get("page_index") is not None and not p.get("is_report_page")),
                   key=lambda p: p["page_index"])
    evidence = {p["page_index"]: [i for i in p.get("items", []) if lock_evidence(i)]
                for p in pages}
    locked = {idx: [i["item_id"] for i in ev] for idx, ev in evidence.items()
              if ev and all(i.get("read_only") is True for i in ev)}
    run_locked = (module.get("lesson") or {}).get("run_locked") is True
    end = None
    if not run_locked:
        seen_lock = False
        for p in pages:
            idx = p["page_index"]
            if (evidence[idx] and idx not in locked) or (seen_lock and p.get("rat_candidate")):
                end = idx
                break
            seen_lock = seen_lock or idx in locked
    pages = [p for p in pages if end is None or p["page_index"] < end]
    locked = {idx: ids for idx, ids in locked.items() if end is None or idx < end}
    if not locked and not run_locked:
        return None
    return {"cause": cause, "run_locked": run_locked, "pages": pages, "evidence": evidence,
            "locked": locked}


def inferred_locks(module: dict) -> dict:
    """{item_id: {class, cause, basis}} for each subpage child whose lock the capture
    cannot read (``read_only`` null).

    Inside the reach a null child is ``read-only-inferred`` when its page's evidence all
    reads true, and ``lock-unknown`` when its page has no evidence item. Under
    run_locked such a page is lock-unknown with an empty basis. A page whose evidence
    reads false or null leaves its children alone.
    """
    reach = lock_reach(module)
    if reach is None:
        return {}
    near = [] if reach["run_locked"] else [f"page {i}" for i in reach["locked"]]
    out = {}
    for p in reach["pages"]:
        idx = p["page_index"]
        if idx in reach["locked"]:
            cls, basis = "read-only-inferred", reach["locked"][idx]
        elif not reach["evidence"][idx]:
            cls, basis = "lock-unknown", near
        else:
            continue
        for it in p.get("items", []):
            if it.get("parent_item_id") and it.get("read_only") is None:
                out[it["item_id"]] = {"class": cls, "cause": reach["cause"], "basis": basis}
    return out


def single_pass_pages(module: dict) -> set:
    """Pages on which a submitted single-attempt item sets no door: every page of the
    reach under run_locked, otherwise each page inside the reach that reads locked.

    A cause alone is module-wide and says nothing about the item's own page: there the
    item's read_only may come from its Submit alone, and the page still holds the Submit
    control.
    """
    reach = lock_reach(module)
    if reach is None:
        return set()
    if reach["run_locked"]:
        return {p["page_index"] for p in reach["pages"]}
    return set(reach["locked"])


def _door_key(d: dict) -> tuple:
    """Page order; on one page a checkpoint (item_id None) sorts before items."""
    idx = d["page_index"]
    return (idx if idx is not None else 10 ** 6, d["item_id"] or "")


def boundary_map(module: dict) -> dict:
    """Every boundary in the module, in page order, from the capture alone.

    Returns {doors, never_navigated, checkpoints_passed, single_attempts_passed,
    checkpoints_inventory_measured, run_level_commit}. Each door is
    {class, severity, page_index, item_id, detail}.
    """
    pages = module.get("pages", [])
    cb = module.get("commit_boundaries", {}) or {}
    lesson = module.get("lesson", {}) or {}

    doors: list[dict] = []
    singles_passed: list[dict] = []
    cause = lock_cause(module)
    lowered = cause and cause[0].lower() + cause[1:]
    reach = lock_reach(module)
    passing = single_pass_pages(module)
    for p in pages:
        idx = p.get("page_index")
        if p.get("is_report_page"):
            doors.append({"class": "report-page", "severity": SEVERITY["report-page"],
                          "page_index": idx, "item_id": None,
                          "detail": "the report page is never visited; its panels are mirrors "
                                    "and its panel keys repeat keys of real pages"})
            continue
        if p.get("is_checkpoint"):
            doors.append({"class": "checkpoint-page", "severity": SEVERITY["checkpoint-page"],
                          "page_index": idx, "item_id": None,
                          "detail": "never advance past a Checkpoint page"})
        for it in p.get("items", []):
            if submitted_single(it) and idx in passing:
                where = (f"and {lowered}" if reach["run_locked"] else
                         f"{lowered}, and page {idx} reads locked "
                         f"({', '.join(reach['locked'][idx])})")
                singles_passed.append({"class": "single-attempt-item", "page_index": idx,
                                       "item_id": it["item_id"],
                                       "detail": f"{it['type']} is single-attempt, the capture "
                                                 f"reads it read_only and answered, {where}: "
                                                 f"already submitted"})
            elif it.get("attempts") == "single":
                why = ("" if not submitted_single(it) else
                       ". The response appears submitted; checkpoint and run-lock evidence remain absent" if cause is None else
                       f". The capture reads it submitted and {lowered}, but page {idx} does "
                       f"not read locked inside that lock's reach")
                doors.append({"class": "single-attempt-item",
                              "severity": SEVERITY["single-attempt-item"],
                              "page_index": idx, "item_id": it["item_id"],
                              "detail": f"{it['type']} is single-attempt; Submit is a one-way "
                                        f"door, so the item is never submitted and is written "
                                        f"only with a recorded approval{why}"})
            if it.get("type") == "unmapped":
                doors.append({"class": "unmapped", "severity": SEVERITY["unmapped"],
                              "page_index": idx, "item_id": it["item_id"],
                              "detail": it.get("capture_note")
                              or "pluginKey has no tested write path"})
    doors.sort(key=_door_key)

    # Keyed by route: the report page can be the completion page (both route
    # 'complete'), and fill-report never_navigated is a set of routes.
    never: dict[str, str] = {}
    if lesson.get("has_completion_page"):
        route = lesson.get("completion_route") or "complete"
        never[route] = ("the Completion page carries the run-level commit and is never "
                        "visited, not merely stopped before Commit")
    for p in pages:
        if p.get("is_report_page"):
            r = p.get("route") or f"page/{p.get('page_index')}"
            extra = ("report page; its panels mirror real ones and its panel keys are not "
                     "unique across pages")
            never[r] = f"{never[r]} Also the report page." if r in never else extra
    # A committed checkpoint has left the flow and is no longer a door; it is reported so
    # an operator reading "no doors" knows the run is already through one.
    passed = [c for c in (cb.get("checkpoints") or [])
              if c.get("committed") and not c.get("in_flow")]
    return {"doors": doors,
            "never_navigated": [{"route": r, "reason": why} for r, why in never.items()],
            "checkpoints_passed": passed,
            "single_attempts_passed": singles_passed,
            "checkpoints_inventory_measured": cb.get("checkpoints_container_present") is True,
            "run_level_commit": cb.get("run_level_commit")}


def first_door(module: dict) -> dict | None:
    """The first stop-severity door as a boundary_map() door dict, or None."""
    return next((d for d in boundary_map(module)["doors"] if d["severity"] == "stop"), None)


def first_stop_boundary(module: dict) -> tuple | None:
    """(page_index, class, item_id) of the first stop-severity door, or None.

    The fill planner stops before this page and the validator rejects a write at or past
    it. Ties on a page break on item_id, a checkpoint first.
    """
    d = first_door(module)
    return (d["page_index"], d["class"], d["item_id"]) if d else None
