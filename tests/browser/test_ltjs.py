# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ltcore.ltjs: render, check-url and check-upload.

- check-url allows a planned page (exit 0) and forbids every other class (exit 1); a bad plan
  is exit 2. URL page numbers reach a flow position only through the plan's map. With --from,
  a move that passes a checkpoint page or the stop is forbidden. The Python rule and
  lt_guard.js agree on one URL table and one crossing table.
- render resolves a bare name against the skill folder from another working directory,
  prepends the guard (and lt_net.js for fill scripts), freezes the arguments, and a --stub
  output refuses to run when its base64 was altered. Rendered files land in a 0700 folder
  owned by the user, as new 0600 files; a shared, foreign or symlinked folder is refused.
- check-upload accepts only a PNG or JPEG regular file inside the data folder, up to 8 MB.
- model_answer.js is refused (exit 4) when model_answer.read is false.

Tests that execute JavaScript need node and run the output in tests/browser/harness.js.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ltcore import ltjs

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SHARED = REPO / "shared"
PLAN = HERE / "fixtures" / "plan-demo.json"
ORIGIN = "https://lt.example.invalid"
RUN = f"{ORIGIN}/i/inst0001/student/courses/1/runs/101/run"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64

needs_node = pytest.mark.requires_bin("node")


def cli(run_py, *args, cwd=None, env=None):
    return run_py("-m", "ltcore.ltjs", *args, cwd=cwd, env=env)


def node(spec: dict, tmp_path: Path) -> dict:
    f = tmp_path / f"spec-{len(list(tmp_path.glob('spec-*')))}.json"
    f.write_text(json.dumps(spec), encoding="utf-8")
    p = subprocess.run(["node", str(HERE / "run_rendered.js"), str(f)], capture_output=True,
                       text=True, timeout=60, cwd=REPO)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# --------------------------------------------------------------------------- check-url

@pytest.mark.parametrize("url,idx", [
    (f"{RUN}/page/1", 1),
    (f"{RUN}/page/2", 2),
    (f"{RUN}/page/4/", 4),
    (f"{RUN}/background", 1),
])
def test_check_url_allows_planned_pages(run_py, url, idx):
    r = cli(run_py, "check-url", "--plan", str(PLAN), url)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["allowed"] is True and out["page_index"] == idx


FORBIDDEN = [
    (f"{RUN}/complete", "complete"),
    (f"{RUN}/complete/", "complete"),
    (f"{RUN}/checkpoint/1", "checkpoint"),
    (f"{RUN}/page/6", "report-page"),
    (f"{RUN}/page/2?preview=1", "preview"),
    (f"{RUN}/page/2?preview", "preview"),
    (f"{ORIGIN}/i/inst0001/student/courses/1/runs/101/preview/run/page/1", "preview"),
    ("https://other.example.invalid/i/inst0001/student/courses/1/runs/101/run/page/1", "other-origin"),
    ("http://lt.example.invalid/i/inst0001/student/courses/1/runs/101/run/page/1", "other-origin"),
    (f"{RUN}/page/5", "at-or-past-stop"),
    (f"{RUN}/page/3", "never-navigated"),
    (f"{RUN}/page/9", "page-not-in-plan"),
    (f"{ORIGIN}/i/inst0001/student/courses/1/runs/102/run/page/1", "wrong-run"),
    (f"{ORIGIN}/i/inst0001/student/courses/2/runs/101/run/page/1", "wrong-course"),
    (f"{ORIGIN}/i/inst0002/student/courses/1/runs/101/run/page/1", "wrong-instance"),
    (f"{ORIGIN}/i/inst0001/student/courses/1", "not-a-run-page"),
    (f"{RUN}/page/1/../../complete", "bad-url"),
    (f"{RUN}/page/1\\..\\..\\complete", "bad-url"),
    ("https://user:pw" + chr(64) + "lt.example.invalid/i/inst0001/student/courses/1/runs/101/run/page/1",
     "credentials-in-url"),
    ("javascript:alert(1)", "bad-scheme"),
    (f"{RUN}/page/1 ", "bad-url"),
]


@pytest.mark.parametrize("url,reason", FORBIDDEN)
def test_check_url_forbids_each_class(run_py, url, reason):
    r = cli(run_py, "check-url", "--plan", str(PLAN), url)
    assert r.returncode == 1, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["allowed"] is False and out["reason"] == reason


def test_check_url_without_plan_forbids_every_page(run_py):
    r = cli(run_py, "check-url", f"{RUN}/page/1")
    assert r.returncode == 1
    assert json.loads(r.stdout)["reason"] == "no-plan"


@pytest.mark.parametrize("content,needle", [
    (None, "cannot read"),
    ("{not json", "not valid JSON"),
    ("[1, 2]", "JSON object"),
    ('{"origin": "https://lt.example.invalid", "stop_before_page": "x"}', "stop_before_page"),
    ('{"origin": "https://lt.example.invalid", "stop_before_page": true}', "stop_before_page"),
    ('{"origin": "https://lt.example.invalid", "never_navigated": "complete"}', "never_navigated"),
    ('{"origin": "https://lt.example.invalid", "never_navigated": [1.5]}', "never_navigated"),
    ('{"origin": "https://lt.example.invalid", "pages": [{"page_index": "a"}]}', "page_index"),
    ('{"origin": "https://lt.example.invalid/path"}', "origin"),
    ('{"origin": "https://lt.example.invalid", "instance": "../x"}', "instance"),
])
def test_check_url_bad_plan_is_exit_2(run_py, tmp_path, content, needle):
    p = tmp_path / "plan.json"
    if content is not None:
        p.write_text(content, encoding="utf-8")
    r = cli(run_py, "check-url", "--plan", str(p), f"{RUN}/page/1")
    assert r.returncode == 2, r.stdout + r.stderr
    assert needle in (r.stdout + r.stderr)


def test_check_url_plan_without_origin_needs_config(run_py, tmp_path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"stop_before_page": 4, "pages": [
        {"page_index": i, "page_no": i} for i in (1, 2, 3, 4)]}), encoding="utf-8")
    r = cli(run_py, "check-url", "--plan", str(p), f"{RUN}/page/1")
    assert r.returncode == 2 and "lt.origin" in r.stderr
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[lt]\norigin = "{ORIGIN}"\n', encoding="utf-8")
    r = cli(run_py, "check-url", "--config", str(cfg), "--plan", str(p), f"{RUN}/page/1")
    assert r.returncode == 0, r.stdout + r.stderr
    r = cli(run_py, "check-url", "--config", str(cfg), "--plan", str(p), f"{RUN}/page/4")
    assert r.returncode == 1 and json.loads(r.stdout)["reason"] == "at-or-past-stop"


def test_check_url_config_origin_conflict_forbids(run_py, tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[lt]\norigin = "https://example.lt.app"\n', encoding="utf-8")
    r = cli(run_py, "check-url", "--config", str(cfg), "--plan", str(PLAN), f"{RUN}/page/1")
    assert r.returncode == 1
    assert json.loads(r.stdout)["reason"] == "origin-conflict"


def test_check_url_needs_the_plans_page_map():
    # URL page numbers count content pages only; without the plan's map a number is no position.
    plan = {"origin": ORIGIN, "stop_before_page": 3, "report_pages": [2]}
    assert ltjs.check_url(f"{RUN}/page/1", plan)[1]["reason"] == "plan-has-no-page-map"
    assert ltjs.check_url(f"{RUN}/page/1", plan)[0] == 1
    assert ltjs.check_url(f"{RUN}/background", plan)[0] == 0
    plan["flow_pages"] = [{"page_index": 1, "page_no": 1}, {"page_index": 2, "page_no": 2},
                          {"page_index": 3, "page_no": 3}]
    assert ltjs.check_url(f"{RUN}/page/1", plan)[0] == 0
    assert ltjs.check_url(f"{RUN}/page/2", plan)[1]["reason"] == "report-page"
    assert ltjs.check_url(f"{RUN}/page/3", plan)[1]["reason"] == "at-or-past-stop"
    assert ltjs.check_url(f"{RUN}/page/4", plan)[1]["reason"] == "page-not-in-plan"
    plan = {"origin": ORIGIN, "pages": [{"page_index": 2, "page_no": 2}, {"page_index": 7, "page_no": 7}],
            "boundary_map": {"never_navigated": [{"route": "/run/page/2"}]}}
    assert ltjs.check_url(f"{RUN}/page/2", plan)[1]["reason"] == "never-navigated"
    assert ltjs.check_url(f"{RUN}/page/7", plan)[0] == 0


def test_check_url_refuses_checkpoint_pages_from_every_plan_field():
    base = {"origin": ORIGIN, "pages": [{"page_index": i, "page_no": i} for i in range(1, 9)]}
    for extra in ({"checkpoint_pages": [4]},
                  {"boundary_map": {"doors": [{"class": "checkpoint-page", "page_index": 4}]}},
                  {"first_boundary": {"class": "checkpoint-page", "page_index": 4}},
                  {"flow_pages": [{"page_index": 4, "is_checkpoint": True}]}):
        plan = {**base, **extra}
        assert ltjs.policy_from_plan(plan)["checkpoint_indexes"] == [4], extra
        assert ltjs.check_url(f"{RUN}/page/4", plan)[1]["reason"] == "checkpoint"


CROSS_PLAN = {"origin": ORIGIN, "instance": "inst0001", "course_id": 1, "run_id": 101,
              "stop_before_page": 9, "checkpoint_pages": [4], "report_pages": [3],
              "flow_pages": [{"page_index": i, "page_no": n} for i, n in
                             ((1, 1), (2, 2), (3, 3), (5, 4), (6, 5), (7, 6), (8, 7), (9, 8))]}


@pytest.mark.parametrize("frm,to,code,reason", [
    ("page/1", "page/2", 0, "allowed"),
    ("page/2", "page/1", 0, "allowed"),
    ("page/1", "page/4", 1, "crosses-checkpoint"),   # page/4 is flow 5, past the checkpoint at 4
    ("page/6", "page/2", 1, "crosses-checkpoint"),   # backwards across it too
    ("page/1", "page/3", 1, "report-page"),
    ("page/2", "page/6", 1, "crosses-checkpoint"),
    ("page/5", "page/6", 0, "allowed"),
    ("page/3", "page/1", 0, "allowed"),              # a report page may be left
    ("page/12", "page/1", 1, "current-not-in-plan"),
    ("complete", "page/1", 1, "current-not-in-plan"),
    ("OUTSIDE", "page/1", 0, "allowed"),
    ("OTHER-RUN", "page/2", 0, "allowed"),
])
def test_check_url_from_refuses_moves_past_a_checkpoint(run_py, tmp_path, frm, to, code, reason):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(CROSS_PLAN), encoding="utf-8")
    src = {"OUTSIDE": f"{ORIGIN}/i/inst0001/student/courses/1",
           "OTHER-RUN": f"{ORIGIN}/i/inst0001/student/courses/1/runs/102/run/page/9"}.get(frm, f"{RUN}/{frm}")
    r = cli(run_py, "check-url", "--plan", str(plan), "--from", src, f"{RUN}/{to}")
    assert r.returncode == code, r.stdout + r.stderr
    assert json.loads(r.stdout)["reason"] == reason


def test_check_url_from_stop_and_bad_input(run_py, tmp_path):
    plan = {**CROSS_PLAN, "checkpoint_pages": [], "stop_before_page": 4}
    assert ltjs.check_url(f"{RUN}/page/1", plan, from_url=f"{RUN}/page/6")[1]["reason"] == "crosses-stop"
    assert ltjs.check_url(f"{RUN}/page/2", plan, from_url=f"{RUN}/page/1")[1]["crossing"] == "checked"
    assert ltjs.check_url(f"{RUN}/page/2", plan)[1]["crossing"] == "not-checked"
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    r = cli(run_py, "check-url", "--plan", str(p), "--from", "not a url", f"{RUN}/page/1")
    assert r.returncode == 2 and json.loads(r.stdout)["reason"] == "bad-from-url"


def test_policy_normalizes_plan_fields():
    pol = ltjs.policy_from_plan(json.loads(PLAN.read_text()))
    assert pol["mode"] == "plan" and pol["origin"] == ORIGIN and pol["mapped"] is True
    assert pol["page_map"] == {"1": 1, "2": 2, "3": 3, "4": 4, "5": 6, "6": 7}
    assert pol["report_indexes"] == [7] and pol["never_indexes"] == [3]
    assert pol["checkpoint_indexes"] == [5]
    assert pol["never_routes"] == ["complete"]
    assert ltjs.policy_from_plan(None) == {"mode": "block-all", "origin": None}
    assert ltjs.normalize_origin("HTTPS://LT.Example.invalid:443/") == ORIGIN


URL_TABLE = [u for u, _ in FORBIDDEN if "\\" not in u and " " not in u and ".." not in u] + [
    f"{RUN}/page/1", f"{RUN}/page/2", f"{RUN}/page/4", f"{RUN}/background",
    f"{RUN}/page/7", f"{ORIGIN}:443/i/inst0001/student/courses/1/runs/101/run/page/1",
    f"{RUN}/page/1#x", f"{RUN}/Preview/page/1",
]


@needs_node
def test_python_and_guard_classify_agree(tmp_path):
    plans = [json.loads(PLAN.read_text()), {"origin": ORIGIN, "stop_before_page": 3, "report_pages": [2]},
             {"origin": ORIGIN}, None, CROSS_PLAN]
    for plan in plans:
        pol = ltjs.policy_from_plan(plan)
        py = [ltjs.classify(u, pol) for u in URL_TABLE]
        js = node({"mode": "classify", "policy": pol, "urls": URL_TABLE}, tmp_path)
        for u, a, b in zip(URL_TABLE, py, js):
            assert (a["allowed"], a["reason"], a["page_index"]) == (b["allowed"], b["reason"], b["page_index"]), u


@needs_node
def test_python_and_guard_agree_on_crossing(tmp_path):
    pol = ltjs.policy_from_plan(CROSS_PLAN)
    froms = [f"{RUN}/page/{n}" for n in (1, 2, 5, 6, 12)] + [f"{RUN}/background", f"{RUN}/complete",
                                                              f"{ORIGIN}/i/inst0001/student/courses/1"]
    dests = [f"{RUN}/page/{n}" for n in (1, 2, 3, 4, 6, 8, 9)] + [f"{RUN}/background"]
    for frm in froms:
        cases = [d for d in dests if d.rstrip("/") != frm]
        js = node({"mode": "decide", "policy": pol, "from": frm, "urls": cases}, tmp_path)
        for d, b in zip(cases, js):
            a = ltjs.decide(d, pol, frm)
            assert (a["allowed"], a["reason"]) == (b["allowed"], b["reason"]), (frm, d)


# --------------------------------------------------------------------------- render

def make_skill(tmp_path: Path, name: str = "demo-skill") -> Path:
    """A skill folder laid out as tools/sync_shared.py writes it, with its own page script."""
    skill = tmp_path / "skills" / name
    sh = skill / "scripts" / "_shared"
    (sh / "ltcore").mkdir(parents=True)
    for f in (SHARED / "ltcore").glob("*.py"):
        shutil.copy2(f, sh / "ltcore" / f.name)
    shutil.copytree(SHARED / "browser", sh / "browser")
    (sh / "schemas").mkdir()
    shutil.copy2(SHARED / "schemas" / "config.schema.json", sh / "schemas" / "config.schema.json")
    for extra in ("config.defaults.toml", "style.defaults.toml"):
        if (SHARED / extra).is_file():
            shutil.copy2(SHARED / extra, sh / extra)
    b = skill / "scripts" / "browser"
    b.mkdir()
    (b / "ping.js").write_text(
        "globalThis.__ran = true;\nreturn { ok: true, who: 'skill-ping', x: __LT_ARGS__.x };\n", encoding="utf-8")
    (b / "model_answer.js").write_text("return { ok: true, keys: 0 };\n", encoding="utf-8")
    return skill


def render_in(skill: Path, run_py, *args, cwd: Path, env=None):
    script = skill / "scripts" / "_shared" / "ltcore" / "ltjs.py"
    r = run_py(str(script), "render", *args, cwd=cwd, env=env, shared_on_path=False)
    return r


def test_render_resolves_names_in_the_skill_from_another_cwd(run_py, tmp_path):
    skill = make_skill(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "scripts" / "browser").mkdir(parents=True)
    (elsewhere / "scripts" / "browser" / "ping.js").write_text("return 'decoy';\n")
    (elsewhere / "ping.js").write_text("return 'decoy';\n")
    out = tmp_path / "out"
    r = render_in(skill, run_py, "ping", "--form", "function", "--arg", "x=7", "--out-dir", str(out), cwd=elsewhere)
    assert r.returncode == 0, r.stderr
    info = json.loads(r.stdout)
    text = Path(info["path"]).read_text()
    assert "skill-ping" in text and "decoy" not in text
    assert Path(info["source"]) == skill / "scripts" / "browser" / "ping.js"
    assert Path(info["path"]).parent == out
    assert info["form"] == "function" and info["net"] is False and info["guard"] == "block-all"
    assert info["bytes"] == len(text.encode()) and info["sha256"][:8] in Path(info["path"]).name
    r = render_in(skill, run_py, "guard", "--form", "function", "--out-dir", str(out), cwd=elsewhere)
    assert r.returncode == 0, r.stderr
    assert Path(json.loads(r.stdout)["source"]) == skill / "scripts" / "_shared" / "browser" / "guard.js"
    r = render_in(skill, run_py, "nosuch", "--out-dir", str(out), cwd=elsewhere)
    assert r.returncode == 2 and "no page script named" in r.stderr
    r = render_in(skill, run_py, "lt_guard", "--out-dir", str(out), cwd=elsewhere)
    assert r.returncode == 2


def test_render_refuses_model_answer_when_reading_is_off(run_py, tmp_path):
    skill = make_skill(tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text("[model_answer]\nread = false\n", encoding="utf-8")
    r = render_in(skill, run_py, "model_answer", "--config", str(cfg), "--out-dir", str(tmp_path / "o"), cwd=tmp_path)
    assert r.returncode == 4 and "model_answer.read is false" in r.stderr
    assert not (tmp_path / "o").exists()
    cfg.write_text("[model_answer]\nread = true\n", encoding="utf-8")
    r = render_in(skill, run_py, "model_answer", "--config", str(cfg), "--out-dir", str(tmp_path / "o"), cwd=tmp_path)
    assert r.returncode == 0, r.stderr


def test_render_prepends_guard_always_and_net_for_fill(tmp_path):
    body = tmp_path / "x.js"
    body.write_text("return { ok: true };\n")
    plain = ltjs.render(str(body), form="function", out_dir=tmp_path / "o")
    text = Path(plain["path"]).read_text()
    assert "LtGuard" in text and "globalThis.LtNet" not in text and plain["net"] is False
    fill = tmp_path / "_fill-demo-p1.js"
    fill.write_text("return { ok: true };\n")
    info = ltjs.render(str(fill), form="function", out_dir=tmp_path / "o")
    assert info["net"] is True and "LtNet" in Path(info["path"]).read_text()
    assert ltjs.render(str(fill), form="function", net=False, out_dir=tmp_path / "o")["net"] is False
    assert ltjs.render(str(body), form="function", net=True, out_dir=tmp_path / "o")["net"] is True
    fill_dir = tmp_path / "skills" / "lt-module-fill" / "scripts" / "browser"
    fill_dir.mkdir(parents=True)
    (fill_dir / "probe.js").write_text("return 1;\n")
    assert ltjs.render(str(fill_dir / "probe.js"), out_dir=tmp_path / "o")["net"] is True


def test_render_forms_and_adapter_defaults(tmp_path, run_py):
    body = tmp_path / "x.js"
    body.write_text("return { ok: true };\n")
    e = Path(ltjs.render(str(body), form="expression", out_dir=tmp_path / "o")["path"]).read_text()
    f = Path(ltjs.render(str(body), form="function", out_dir=tmp_path / "o")["path"]).read_text()
    assert e.startswith("await (async () => {") and e.rstrip().endswith(")();")
    assert f.startswith("async () => {") and f.rstrip().endswith("}")
    cfg = tmp_path / "c.toml"
    for adapter, form, stub in [("claude-in-chrome", "expression", True), ("chrome-devtools-mcp", "function", False),
                                ("playwright-mcp", "function", False), ("manual-paste", "expression", False)]:
        cfg.write_text(f'[browser]\nadapter = "{adapter}"\n', encoding="utf-8")
        r = cli(run_py, "render", str(body), "--config", str(cfg), "--out-dir", str(tmp_path / "o"))
        info = json.loads(r.stdout)
        assert (info["form"], info["stub"]) == (form, stub), adapter


def test_render_out_dir_defaults(run_py, tmp_path):
    body = tmp_path / "x.js"
    body.write_text("return 1;\n")
    t = tmp_path / "tmp"
    t.mkdir()
    r = cli(run_py, "render", str(body), env={"TMPDIR": str(t)})
    assert r.returncode == 0, r.stderr
    p = Path(json.loads(r.stdout)["path"])
    want = "lt-suite-js" if not hasattr(os, "getuid") else f"lt-suite-js-{os.getuid()}"
    assert p.parent == t / want
    assert (p.parent.stat().st_mode & 0o777) == 0o700
    cfg = tmp_path / "c.toml"
    cfg.write_text('[browser]\njs_tmp_dir = "js-here"\n', encoding="utf-8")
    r = cli(run_py, "render", str(body), "--config", str(cfg))
    assert Path(json.loads(r.stdout)["path"]).parent == tmp_path / "js-here"


def test_render_argument_errors(run_py, tmp_path):
    body = tmp_path / "x.js"
    body.write_text("return 1;\n")
    o = str(tmp_path / "o")
    assert cli(run_py, "render", str(body), "--arg", "novalue", "--out-dir", o).returncode == 2
    assert cli(run_py, "render", str(body), "--arg-json", "k={bad", "--out-dir", o).returncode == 2
    assert cli(run_py, "render", str(body), "--arg", "bad-key=1", "--out-dir", o).returncode == 2
    (tmp_path / "a.json").write_text("[1]")
    assert cli(run_py, "render", str(body), "--args-file", str(tmp_path / "a.json"), "--out-dir", o).returncode == 2
    (tmp_path / "p.json").write_text('{"stop_before_page": "x"}')
    r = cli(run_py, "render", str(body), "--plan", str(tmp_path / "p.json"), "--out-dir", o)
    assert r.returncode == 2 and "bad plan" in r.stderr
    assert cli(run_py, "render", str(tmp_path / "missing.js"), "--out-dir", o).returncode == 2


@needs_node
@pytest.mark.parametrize("form", ["expression", "function"])
def test_rendered_script_runs_and_carries_guard_trips(tmp_path, form):
    body = tmp_path / "x.js"
    body.write_text("globalThis.__ran = true;\nreturn { ok: true, n: __LT_ARGS__.n, s: __LT_ARGS__.s };\n")
    info = ltjs.render(str(body), form=form, args={"n": 3, "s": "</script> \"'"}, out_dir=tmp_path / "o")
    out = node({"mode": "run", "file": info["path"], "raw_commit": True}, tmp_path)
    assert out["ran"] is True
    assert out["result"] == {"ok": True, "n": 3, "s": "</script> \"'", "guard_trips": 0}
    assert out["raw_commit_threw"] is True and out["commits"] == 0


@needs_node
def test_arguments_are_frozen_and_proto_safe(tmp_path):
    body = tmp_path / "x.js"
    body.write_text(
        "let threw = false;\n"
        "try { __LT_ARGS__.a.b = 2; } catch (e) { threw = true; }\n"
        "return { frozen: Object.isFrozen(__LT_ARGS__) && Object.isFrozen(__LT_ARGS__.a), threw,\n"
        "  b: __LT_ARGS__.a.b, own: Object.prototype.hasOwnProperty.call(__LT_ARGS__, '__proto__'),\n"
        "  polluted: ({}).polluted === true || __LT_ARGS__.polluted === true };\n")
    args_file = tmp_path / "args.json"
    args_file.write_text('{"__proto__": {"polluted": true}, "a": {"b": 1}}')
    info = ltjs.render(str(body), form="function", args=json.loads(args_file.read_text()), out_dir=tmp_path / "o")
    out = node({"mode": "run", "file": info["path"]}, tmp_path)
    # Page scripts run in sloppy mode, so the write fails silently; the value stays.
    assert out["result"]["frozen"] is True
    assert out["result"]["b"] == 1 and out["result"]["own"] is True and out["result"]["polluted"] is False


@needs_node
def test_stub_runs_when_intact_and_refuses_a_changed_byte(tmp_path):
    body = tmp_path / "x.js"
    body.write_text("globalThis.__ran = true;\nreturn { ok: true, who: 'stubbed' };\n")
    info = ltjs.render(str(body), form="expression", stub=True, out_dir=tmp_path / "o")
    text = Path(info["path"]).read_text()
    assert info["stub"] is True and "crypto.subtle.digest" in text and "new Function" in text
    out = node({"mode": "run", "file": info["path"]}, tmp_path)
    assert out["ran"] is True and out["result"]["who"] == "stubbed" and out["result"]["guard_trips"] == 0

    start = text.index('const B64 = "') + len('const B64 = "')
    i = start + 40
    swapped = "A" if text[i] != "A" else "B"
    bad = tmp_path / "bad.js"
    bad.write_text(text[:i] + swapped + text[i + 1:])
    out = node({"mode": "run", "file": str(bad)}, tmp_path)
    assert out["ran"] is False
    assert out["result"]["ok"] is False and out["result"]["fatal"] == "stub-hash-mismatch"

    broken = tmp_path / "broken.js"
    broken.write_text(text[:start] + "!!" + text[start + 2:])
    out = node({"mode": "run", "file": str(broken)}, tmp_path)
    assert out["ran"] is False and out["result"]["fatal"] == "stub-hash-mismatch"


@needs_node
def test_rendered_guard_with_plan_cancels_boundary_navigation(tmp_path):
    info = ltjs.render("guard", form="function", plan=str(PLAN), out_dir=tmp_path / "o")
    assert info["guard"] == "plan"
    out = node({"mode": "run", "file": info["path"], "path": "/i/inst0001/student/courses/1/runs/101/run/page/1",
                "navigate": [f"{RUN}/page/2", f"{RUN}/page/5", f"{RUN}/complete"]}, tmp_path)
    assert out["result"]["ok"] is True and out["result"]["policy"] == "plan"
    assert [n["canceled"] for n in out["navigate"]] == [False, True, True]
    assert out["trips"] == 2


@needs_node
def test_rendered_script_refuses_forbidden_route(tmp_path):
    body = tmp_path / "x.js"
    body.write_text("globalThis.__ran = true;\nreturn { ok: true };\n")
    info = ltjs.render(str(body), form="function", out_dir=tmp_path / "o")
    out = node({"mode": "run", "file": info["path"], "path": "/i/inst0001/student/courses/1/runs/101/run/complete"}, tmp_path)
    assert out["ran"] is False
    assert out["result"] == {"ok": False, "fatal": "forbidden-route", "route": "complete", "guard_trips": 0}


@needs_node
def test_fill_render_installs_lt_net(tmp_path):
    body = tmp_path / "_fill-demo-p1.js"
    body.write_text("return { ok: true, net: typeof LtNet.expect };\n")
    info = ltjs.render(str(body), form="function", out_dir=tmp_path / "o")
    out = node({"mode": "run", "file": info["path"]}, tmp_path)
    assert out["result"]["net"] == "function" and out["net_installed"] is True


# --------------------------------------------------------------------------- upload

@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    (d / "runs" / "m1").mkdir(parents=True)
    return d


def test_check_upload_accepts_png_and_jpeg_inside_the_data_dir(run_py, data_dir):
    for name, raw, mime in (("a.png", PNG, "image/png"), ("b.jpg", JPEG, "image/jpeg")):
        f = data_dir / "runs" / "m1" / name
        f.write_bytes(raw)
        r = cli(run_py, "check-upload", "--data-dir", str(data_dir), str(f))
        assert r.returncode == 0, r.stdout + r.stderr
        out = json.loads(r.stdout)
        assert out["ok"] is True and out["type"] == mime and out["bytes"] == len(raw)


def test_check_upload_refusals(run_py, tmp_path, data_dir):
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    big = data_dir / "big.png"
    big.write_bytes(PNG + b"\x00" * (ltjs.MAX_UPLOAD))
    text = data_dir / "fake.png"
    text.write_bytes(b"GIF89a" + b"\x00" * 10)
    empty = data_dir / "empty.png"
    empty.write_bytes(b"")
    link = data_dir / "link.png"
    os.symlink(outside, link)
    inner_link = data_dir / "inner-link.png"
    (data_dir / "real.png").write_bytes(PNG)
    os.symlink(data_dir / "real.png", inner_link)
    cases = [(outside, "outside the data folder"), (big, "limit"), (text, "magic"), (empty, "empty"),
             (link, "symlink"), (inner_link, "symlink"), (data_dir / "runs", "regular file"),
             (data_dir / "missing.png", "cannot read")]
    for path, needle in cases:
        r = cli(run_py, "check-upload", "--data-dir", str(data_dir), str(path))
        assert r.returncode == 1, (path, r.stdout)
        assert needle in json.loads(r.stdout)["reason"], (path, r.stdout)


def test_check_upload_uses_the_data_dir_from_env(run_py, data_dir):
    f = data_dir / "a.png"
    f.write_bytes(PNG)
    r = cli(run_py, "check-upload", str(f), env={"LT_SUITE_DATA": str(data_dir)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["data_dir"] == str(data_dir)


def test_render_upload_embeds_checked_bytes(run_py, tmp_path, data_dir):
    import base64
    import hashlib
    skill = make_skill(tmp_path)
    (skill / "scripts" / "browser" / "upload.js").write_text(
        "return { ok: true, sha: __LT_ARGS__.file.sha256, panel: __LT_ARGS__.panel };\n")
    f = data_dir / "runs" / "m1" / "label.png"
    f.write_bytes(PNG)
    r = render_in(skill, run_py, "upload", "--form", "function", "--arg", "panel=p7", "--file", str(f),
                  "--data-dir", str(data_dir), "--out-dir", str(tmp_path / "o"), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    info = json.loads(r.stdout)
    text = Path(info["path"]).read_text()
    assert info["net"] is True
    assert base64.b64encode(PNG).decode() in text.replace("\\", "")
    assert hashlib.sha256(PNG).hexdigest() in text
    outside = tmp_path / "x.png"
    outside.write_bytes(PNG)
    r = render_in(skill, run_py, "upload", "--file", str(outside), "--data-dir", str(data_dir),
                  "--out-dir", str(tmp_path / "o"), cwd=tmp_path)
    assert r.returncode == 1 and "upload refused" in r.stderr


def test_module_declares_its_shared_files():
    assert set(ltjs.NEEDS) == {"browser/lt_guard.js", "browser/lt_net.js", "browser/guard.js"}
    ltjs._needs_probe()


def test_render_sanitizes_names_and_refuses_non_json_args(tmp_path, run_py):
    odd = tmp_path / "x*-alert(1)-*.js"
    odd.write_text("return 1;\n")
    info = ltjs.render(str(odd), form="function", out_dir=tmp_path / "o")
    text = Path(info["path"]).read_text()
    assert "x*-alert" not in text.split("\n", 2)[1]
    assert "*/" not in Path(info["path"]).name
    body = tmp_path / "y.js"
    body.write_text("return 1;\n")
    with pytest.raises(ltjs.RenderRefused) as e:
        ltjs.render(str(body), args={"v": float("nan")}, out_dir=tmp_path / "o")
    assert e.value.code == 2
    r = cli(run_py, "render", str(body), "--arg-json", "v=NaN", "--out-dir", str(tmp_path / "o"))
    assert r.returncode == 2 and "plain JSON" in r.stderr


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX permissions")
def test_render_refuses_a_shared_or_symlinked_folder(run_py, tmp_path):
    body = tmp_path / "x.js"
    body.write_text("return 1;\n")
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    r = cli(run_py, "render", str(body), "--out-dir", str(shared))
    assert r.returncode == 2 and "unsafe js_tmp_dir" in r.stderr
    assert list(shared.iterdir()) == []
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    r = cli(run_py, "render", str(body), "--out-dir", str(link))
    assert r.returncode == 2 and "symlink" in r.stderr
    assert list(real.iterdir()) == []
    t = tmp_path / "tmp"
    t.mkdir()
    (t / f"lt-suite-js-{os.getuid()}").mkdir(mode=0o755)
    (t / f"lt-suite-js-{os.getuid()}").chmod(0o755)
    r = cli(run_py, "render", str(body), env={"TMPDIR": str(t)})
    assert r.returncode == 2 and "0755" in r.stderr
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[browser]\njs_tmp_dir = "{shared}"\n', encoding="utf-8")
    r = cli(run_py, "render", str(body), "--config", str(cfg))
    assert r.returncode == 2 and "unsafe js_tmp_dir" in r.stderr


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX permissions")
def test_rendered_files_are_new_private_files(tmp_path):
    body = tmp_path / "x.js"
    body.write_text("return 1;\n")
    out = tmp_path / "o"
    a = ltjs.render(str(body), form="function", out_dir=out)
    b = ltjs.render(str(body), form="function", out_dir=out)
    assert a["path"] != b["path"] and a["sha256"] == b["sha256"]
    for info in (a, b):
        p = Path(info["path"])
        assert (p.stat().st_mode & 0o777) == 0o600
        assert p.name.startswith("x-" + info["sha256"][:8] + "-")
    assert (out.stat().st_mode & 0o777) == 0o700


def test_check_url_checks_second_checkpoint_after_first_is_behind(run_py, tmp_path):
    # Checking only the first checkpoint incorrectly allows this move past flow index 7.
    plan = {"origin": ORIGIN, "checkpoint_pages": [3, 7], "flow_pages": [
        {"page_index": i, "page_no": n} for i, n in ((1, 1), (4, 3), (5, 4), (8, 6))]}
    path = tmp_path / "two-checkpoints.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    result = cli(run_py, "check-url", "--plan", str(path), "--from", f"{RUN}/page/3", f"{RUN}/page/6")
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["reason"] == "crosses-checkpoint"
    assert json.loads(result.stdout)["page_index"] == 7
    # Directly landing on the second checkpoint is forbidden too.
    plan["flow_pages"].append({"page_index": 7, "page_no": 5})
    assert ltjs.check_url(f"{RUN}/page/5", plan)[1]["reason"] == "checkpoint"
