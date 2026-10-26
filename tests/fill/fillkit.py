# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Builders for the fill skill's Python tests: synthetic modules and answers, and Kit, a
runner for the skill's scripts with shared/ on PYTHONPATH and every lt-suite folder inside
the test's temp folder. All ids are synthetic (origin https://lt.example.invalid, instance
inst0001, course 1, run 101, module c1-r101). conftest.py turns these into fixtures; test
files import this module by path (pytest runs with --import-mode=importlib).
"""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "skills" / "lt-module-fill" / "scripts"
ORIGIN = "https://lt.example.invalid"


def item(iid, key, typ, plugin, **kw):
    d = {"item_id": iid, "panel_key": key, "plugin_key": plugin, "type": typ, "attempts": "multi",
         "assessment_type": "noAssessment", "commit_state": "open", "read_only": False,
         "automation": "structured-path", "answered": False, "parent_item_id": None,
         "prompt_text": "Q " + iid}
    d.update(kw)
    return d


def module(pages, **kw):
    d = {"schema_version": 1, "module_id": "c1-r101", "instance": "inst0001", "course_id": 1, "run_id": 101,
         "source_url": f"{ORIGIN}/i/inst0001/student/courses/1/runs/101/run",
         "lesson": {"title": "Synthetic", "has_completion_page": True, "completion_route": "complete",
                    "run_locked": False},
         "commit_boundaries": {"checkpoint_pages": [], "checkpoints": [], "checkpoints_container_present": True},
         "pages": [{"page_index": i, "page_no": i, "route": None, "title": f"Page {i}",
                    "url": f"{ORIGIN}/i/inst0001/student/courses/1/runs/101/run/page/{i}",
                    "is_report_page": False, "is_checkpoint": False, "items": its} for i, its in pages]}
    d.update(kw)
    return d


def drafted(iid, **kw):
    d = {"item_id": iid, "status": "drafted", "confidence": "high",
         "segments": [{"kind": "claim", "text": f"Draft {iid}", "provenance_refs": ["p1"]}],
         "provenance": [{"ref": "p1", "tier": 1, "source_id": "X", "locator": "slide 1"}]}
    d.update(kw)
    return d


def answers(items, **kw):
    d = {"schema_version": 2, "module_id": "c1-r101", "run_id": 101, "drafted_at": "2000-01-01T00:00:00Z",
         "source_module": {"path": "module.json", "sha256": "0" * 64}, "items": items}
    d.update(kw)
    return d


def text_pages(n_pages: int, per_page: int = 1, start: int = 1):
    """Pages start..start+n-1, each with `per_page` text items (keys page*10+k)."""
    return [(p, [item(f"p{p}-{p * 10 + k}", str(p * 10 + k), "text-response", "text-question")
                 for k in range(per_page)]) for p in range(start, start + n_pages)]


class Kit:
    """Runs the skill's scripts in one temp folder; config in ./lt-suite.toml."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.data = tmp / "data"
        self.env = dict(os.environ)
        for k in list(self.env):
            if k.startswith("LT_SUITE_"):
                del self.env[k]
        self.env.update({"PYTHONPATH": str(REPO / "shared"), "LT_SUITE_DATA": str(self.data),
                         "LT_SUITE_CACHE": str(tmp / "cache"), "XDG_CONFIG_HOME": str(tmp / "xdg"),
                         "PYTHONDONTWRITEBYTECODE": "1"})

    def config(self, text: str) -> Path:
        p = self.tmp / "lt-suite.toml"
        p.write_text(text, encoding="utf-8", newline="\n")
        return p

    def write(self, name: str, doc) -> Path:
        p = self.tmp / name
        if str(name) == "answers.json" and (self.tmp / "module.json").is_file():
            source = doc.get("source_module", {})
            if source.get("sha256") == "0" * 64 or str(source.get("path", "")).startswith("../modules/"):
                source.update(path="module.json", sha256=hashlib.sha256((self.tmp / "module.json").read_bytes()).hexdigest())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc), encoding="utf-8", newline="\n")
        return p

    def read(self, name) -> dict:
        return json.loads((self.tmp / name).read_text(encoding="utf-8"))

    def run(self, script: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], cwd=self.tmp,
                              env=self.env, capture_output=True, text=True, timeout=timeout, encoding="utf-8")

    def emit(self, *args: str) -> subprocess.CompletedProcess:
        return self.run("emit_fill.py", *args)

    def payload(self, mod: dict, ans: dict, *extra: str, out: str = "out"):
        self.write("module.json", mod)
        self.write("answers.json", ans)
        r = self.emit("payload", "--module", "module.json", "--answers", "answers.json", "--out", out,
                      "--force", *extra)
        name = "replace-payload.json" if "--replace" in extra else "fill-payload.json"
        p = self.tmp / out / name
        pay = json.loads(p.read_text(encoding="utf-8")) if r.returncode in (0, 2) and p.exists() else None
        return r.returncode, pay, r.stdout + r.stderr

    @property
    def calibration_path(self) -> Path:
        return self.data / "state" / "calibration.json"

    def run_dir(self, mid: str = "c1-r101") -> Path:
        return self.data / "runs" / mid



def jobs(pay) -> list[str]:
    return [j["item"] for pg in pay["pages"].values() for j in pg["jobs"]]


def reasons(pay, key) -> list[tuple[str, str]]:
    return sorted((x["item"], x["reason"]) for x in pay[key])
