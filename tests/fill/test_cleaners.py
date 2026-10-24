# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the fill-text cleaners hook (config [[text_cleaners]], stage fill-text) in
`emit_fill.py payload`: cleaners run in config order through ltcore.cleaners, argv only (no
shell), may change `html` strings only, and any failure (exit code, timeout, no output, a
changed key or job count) refuses the payload with exit 3 and writes nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fillkit import ORIGIN, answers, drafted, module, text_pages  # noqa: E402

CLEANER = r'''
import json, sys, time
mode, src, dst = sys.argv[1], sys.argv[2], sys.argv[3]
doc = json.load(open(src, encoding="utf-8"))
def each(node, fn):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "html" and isinstance(v, str):
                node[k] = fn(v)
            else:
                each(v, fn)
    elif isinstance(node, list):
        for v in node:
            each(v, fn)
if mode.startswith("suffix:"):
    each(doc, lambda h: h.replace("</p>", mode[7:] + "</p>"))
elif mode == "drop-job":
    for pg in doc["pages"].values():
        pg["jobs"] = pg["jobs"][:-1]
elif mode == "rekey":
    for pg in doc["pages"].values():
        for j in pg["jobs"]:
            j["panel_key"] = "999"
elif mode == "fail":
    sys.exit(7)
elif mode == "sleep":
    time.sleep(10)
elif mode == "no-output":
    sys.exit(0)
elif mode == "argv":
    open(dst + ".argv", "w").write(json.dumps(sys.argv[4:]))
json.dump(doc, open(dst, "w", encoding="utf-8"))
'''


def config(kit, *entries) -> None:
    script = kit.tmp / "cleaner.py"
    script.write_text(CLEANER)
    text = f'schema_version = 1\n[lt]\norigin = "{ORIGIN}"\n'
    for name, stage, mode, extra, timeout in entries:
        places = ["{pdf}", "{pdf}"] if stage == "pdf" else ["{in}", "{out}"]
        argv = [sys.executable, str(script), mode, *places, *extra]
        text += (f"\n[[text_cleaners]]\nname = {json.dumps(name)}\nstage = {json.dumps(stage)}\n"
                 f"argv = {json.dumps(argv)}\ntimeout_s = {timeout}\n")
    kit.config(text)


def payload(kit):
    mod = module(text_pages(2))
    return kit.payload(mod, answers([drafted("p1-10"), drafted("p2-20")]))


def test_cleaners_run_in_config_order_and_hash_the_cleaned_text(kit, emit_mod):
    config(kit, ("first", "fill-text", "suffix: A", [], 30), ("second", "fill-text", "suffix: B", [], 30))
    rc, pay, out = payload(kit)
    assert rc == 0, out
    job = pay["pages"]["1"]["jobs"][0]
    assert job["html"] == "<p>Draft p1-10 A B</p>"
    assert any(n == "text cleaner first: 2 html value(s) changed" for n in pay["notes"])
    assert any(n == "text cleaner second: 2 html value(s) changed" for n in pay["notes"])
    # The scripts hash the text that is written, after the cleaners.
    r = kit.emit("scripts", "--out", "out", "--no-render")
    assert r.returncode == 0
    body = (kit.tmp / "out" / "_fill-c1-r101-p1.js").read_text()
    assert f'"h": {emit_mod.djb2("Draft p1-10 A B")}' in body


def test_only_the_fill_text_stage_runs(kit):
    config(kit, ("pdf-only", "pdf", "suffix: X", [], 30))
    rc, pay, out = payload(kit)
    assert rc == 0, out
    assert pay["pages"]["1"]["jobs"][0]["html"] == "<p>Draft p1-10</p>"


def test_argv_is_passed_without_a_shell(kit):
    config(kit, ("argv", "fill-text", "argv", ["; touch pwned", "$(id)"], 30))
    rc, pay, out = payload(kit)
    assert rc == 0, out
    assert not (kit.tmp / "pwned").exists()


@pytest.mark.parametrize("mode,timeout", [("drop-job", 30), ("rekey", 30), ("fail", 30), ("sleep", 1),
                                          ("no-output", 30)])
def test_a_failing_cleaner_refuses_the_payload(kit, mode, timeout):
    config(kit, ("bad", "fill-text", mode, [], timeout))
    rc, pay, out = payload(kit)
    assert rc == 3, out
    assert pay is None
    assert not (kit.tmp / "out" / "fill-payload.json").exists()
    assert not (kit.tmp / "out" / "plan.json").exists()
