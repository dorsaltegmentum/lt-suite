#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write the navigation plan for the image pass of a capture: which content pages to visit.

fetch_media.js saves the images of the page on screen, so the agent visits the content pages
that hold images. Every visit goes through `ltjs.py check-url --plan`, which forbids any page
URL without a plan. This writes that plan from the captured module:

  * pages: the content pages (page_no and url) before the first live Checkpoint page;
  * stop_before_page: that Checkpoint page, from ltcore.boundaries.first_stop_boundary
    (only a checkpoint stops navigation; a single-attempt item does not);
  * never_navigated: the completion route and every report page;
  * media_pages: the page indexes whose items or content carry an image key.

    python3 media_plan.py <module-id | path/to/module-<id>.json> [--out PATH]

Default output: <data>/runs/<module_id>/media-plan.json. Prints the pages to visit.
Exit codes: 0 written, 3 usage, file or config error.
"""
import sys

if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: "
             "python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_shared"))
import ltcore  # noqa: E402

ltcore.bootstrap([])  # standard library only
from ltcore import boundaries, config, store  # noqa: E402

import argparse  # noqa: E402
import json  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402


def has_media(page: dict) -> bool:
    groups = [page.get("media")] + [it.get("media") for it in page.get("items", []) or []]
    groups += [c.get("media") for c in page.get("content", []) or []]
    return any(e.get("key") for g in groups for e in g or [])


def build_plan(module: dict) -> dict:
    """The check-url plan for visiting this module's content pages."""
    stop = boundaries.first_stop_boundary(module)
    stop_before = stop[0] if stop and stop[1] == "checkpoint-page" else None
    parts = urlsplit(module.get("source_url") or "")
    origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None
    pages, skipped, media_pages = [], [], []
    never = [{"route": "complete", "reason": "completion page: a live Commit button"}]
    for p in module.get("pages", []):
        idx = p.get("page_index")
        if p.get("is_report_page"):
            never.append({"page_index": idx, "reason": "report page"})
            skipped.append({"page_index": idx, "reason": "report-page"})
            continue
        if p.get("is_checkpoint"):
            skipped.append({"page_index": idx, "reason": "checkpoint-page"})
            continue
        if stop_before is not None and idx >= stop_before:
            skipped.append({"page_index": idx, "page_no": p.get("page_no"), "reason": "at-or-past-first-boundary"})
            continue
        pages.append({"page_index": idx, "page_no": p.get("page_no"), "url": p.get("url"),
                      "title": p.get("title", "")})
        if has_media(p):
            media_pages.append(idx)
    return {
        "purpose": "capture image pass",
        "module_id": module.get("module_id"),
        "origin": origin,
        "instance": module.get("instance"),
        "course_id": module.get("course_id"),
        "run_id": module.get("run_id"),
        "stop_before_page": stop_before,
        "never_navigated": never,
        "pages": pages,
        "skipped_pages": skipped,
        "media_pages": media_pages,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="module id, or the path of a module-<id>.json")
    ap.add_argument("--out", help="plan file (default: <data>/runs/<module_id>/media-plan.json)")
    ap.add_argument("--config", help="config file (default: the normal lookup)")
    ap.add_argument("--data-dir", help="data folder (overrides LT_SUITE_DATA and the config)")
    a = ap.parse_args(argv)
    try:
        cfg = config.load(a.config)
    except config.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    st = store.Store(cfg, data_dir=a.data_dir)
    target = Path(a.target).expanduser()
    if target.suffix == ".json" or "/" in a.target:
        path = target
    else:
        try:
            path = st.capture_path(a.target)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
    try:
        module = json.loads(path.read_text(encoding="utf-8"))
        mid = store.check_id(module["module_id"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"error: {path}: {e}", file=sys.stderr)
        return 3
    plan = build_plan(module)
    out = Path(a.out).expanduser() if a.out else st.run_dir(mid) / "media-plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"plan: {out}")
    if plan["stop_before_page"] is not None:
        print(f"stop before page_index {plan['stop_before_page']} (a live Checkpoint)")
    by_idx = {p["page_index"]: p for p in plan["pages"]}
    if not plan["media_pages"]:
        print("no content page before the first boundary holds an image key")
    for idx in plan["media_pages"]:
        p = by_idx[idx]
        print(f"visit page_index {idx}: {p['url']}  ({p['title']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
