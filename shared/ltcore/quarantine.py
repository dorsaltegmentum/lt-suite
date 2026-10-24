# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find stamped module output and matching capture text in a notes directory.

The scanner reads every regular Markdown file recursively without following links.
Exit 0 after a scan, 1 when --check finds quarantine, 3 on usage or file errors.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
import os
import re
import unicodedata
from urllib.parse import unquote

from ltcore import config
from ltcore.stamp import parse_stamp, stamp_regexes

NEEDS: list[str] = []


def flatten(text: str) -> str:
    """Normalize compatibility characters and whitespace, retaining punctuation."""
    return " ".join(unicodedata.normalize("NFKC", text or "").split())


def module_triggers(module: dict, min_len: int = 200, probe_len: int = 120) -> list[dict]:
    """Use long authored content panels as exact probes, excluding report mirrors."""
    if min_len < 1 or probe_len < 1:
        raise ValueError("min_len and probe_len must be positive")
    out = []
    for page in module.get("pages", []):
        if page.get("is_report_page"):
            continue
        for panel in page.get("content", []):
            text = flatten(panel.get("text") or "")
            if len(text) >= min_len:
                out.append({"page_index": page.get("page_index"),
                            "panel_key": panel.get("panel_key"), "probe": text[:probe_len]})
    return out


def page_name_of(path: Path, text: str) -> str:
    """Prefer an explicit page title, falling back to its decoded filename."""
    match = re.search(r"^title::[^\S\n]*(.+?)[^\S\n]*$", text[:2000], re.MULTILINE)
    return match.group(1) if match else unquote(path.stem.replace("___", "/"))


def markdown_paths(directory: Path) -> list[Path]:
    """List <directory>/**/*.md; fail visibly if a directory cannot be read."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"notes directory does not exist: {directory}")
    out = []

    def fail(error):
        raise error

    for base, dirs, files in os.walk(directory, followlinks=False, onerror=fail):
        dirs[:] = sorted(name for name in dirs if not (Path(base) / name).is_symlink())
        out.extend(Path(base) / name for name in sorted(files)
                   if name.endswith(".md") and not (Path(base) / name).is_symlink())
    return sorted(out)


def scan(directory: Path, triggers: list[dict] | None = None, *,
         stamp_property: str = "lt-module") -> list[dict]:
    """Return findings with path, page, reason, module_id and evidence fields.

    A stamp quarantines module output regardless of the captured module ID.
    Trigger matches are candidates for human review because quoted text can match.
    """
    stamp_regexes(stamp_property)  # Validate even when the graph has no pages.
    findings = []
    for path in markdown_paths(directory):
        text = path.read_text(encoding="utf-8")
        module_id = parse_stamp(text, stamp_property)
        if module_id:
            findings.append({"path": str(path), "page": page_name_of(path, text),
                             "reason": "stamp", "module_id": module_id,
                             "evidence": f"{stamp_property}:: {module_id}"})
            continue
        normalized = flatten(text)
        for trigger in triggers or []:
            if trigger["probe"] and trigger["probe"] in normalized:
                findings.append({"path": str(path), "page": page_name_of(path, text),
                                 "reason": "trigger-text", "module_id": None,
                                 "evidence": f"verbatim content match on page {trigger['page_index']} "
                                             f"panel {trigger['panel_key']}"})
                break
    return findings


class Parser(argparse.ArgumentParser):
    """Use the scanner's documented usage-error code."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(3, f"{self.prog}: error: {message}\n")


def main(argv=None) -> int:
    ap = Parser(description=__doc__)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--graph", type=Path, help="override notes.graph_path; scan recursively")
    ap.add_argument("--module", type=Path, help="enable verbatim content probes from a capture")
    ap.add_argument("--check", type=Path, help="return 1 when this Markdown page is quarantined")
    ap.add_argument("--min-len", type=int, default=200)
    ap.add_argument("--probe-len", type=int, default=120)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.min_len < 1 or args.probe_len < 1:
            raise ValueError("--min-len and --probe-len must be positive")
        cfg = config.load(args.config)
        graph_value = str(args.graph.expanduser().resolve()) if args.graph else cfg.get("notes.graph_path")
        origin = "flag --graph" if args.graph else cfg.where("notes.graph_path")
        if not graph_value:
            if args.check:
                raise ValueError("--check needs --graph or notes.graph_path")
            result = {"graph": "", "graph_from": origin, "disabled": True,
                      "pages_scanned": 0, "triggers_probed": 0, "findings": []}
        else:
            graph = Path(graph_value)
            paths = markdown_paths(graph)
            triggers = []
            if args.module:
                module = json.loads(args.module.read_text(encoding="utf-8"))
                triggers = module_triggers(module, args.min_len, args.probe_len)
            findings = scan(graph, triggers, stamp_property=cfg.get("notes.stamp_property"))
            result = {"graph": graph_value, "graph_from": origin, "disabled": False,
                      "pages_scanned": len(paths), "triggers_probed": len(triggers), "findings": findings}
            if args.check:
                want = args.check.expanduser().resolve()
                if want not in {path.resolve() for path in paths} or args.check.is_symlink():
                    raise ValueError("--check must name a scanned Markdown page inside notes.graph_path")
                hit = next((f for f in findings if Path(f["path"]).resolve() == want), None)
                check_result = dict(hit, quarantined=True) if hit else {"path": str(want), "quarantined": False}
                check_result["path_from"] = "flag --check"
                print(json.dumps(check_result, indent=2) if args.json else
                      f"{'QUARANTINED' if hit else 'not quarantined'}: {want} (from flag --check)")
                if hit and not args.json:
                    print(f"by {hit['reason']}: {hit['evidence']}")
                return 1 if hit else 0
    except (config.ConfigError, OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
        print(f"quarantine: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"notes.graph_path: {graph_value or '(unset; scan disabled)'} (from {origin})")
        print(f"{result['pages_scanned']} pages scanned; {len(result['findings'])} quarantine findings")
        for finding in result["findings"]:
            print(f"[{finding['reason']}] {finding['page']}: {finding['evidence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
