# Generated from shared/ltcore/ltjs.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render page scripts for a browser tool, and check URLs and uploads before they are used.

Every page script runs inside one wrapper: its inputs as a frozen ``__LT_ARGS__`` object,
then ``browser/lt_guard.js`` (never-submit guards), then ``browser/lt_net.js`` for fill
scripts, then the script body, which ends with ``return <result>;``. The wrapper refuses
forbidden routes, adds ``guard_trips`` to the receipt and turns a throw into a receipt.

Forms:
  expression  ``await (async () => {...})();`` for tools that evaluate text (Claude in Chrome
              ``javascript_tool``, the DevTools console). It ends in a top-level await.
  function    ``async () => {...}`` for tools that call a function (chrome-devtools-mcp
              ``evaluate_script`` with ``sourcePath``, Playwright MCP ``browser_evaluate``).
  --stub      gzip base64 carries a parameterized body verified before execution through new Function.
  --cache     load compressed code from origin storage and request payload on misses.
  --no-compress  use an uncompressed stub when browser gzip decompression is unavailable.
  --chunk-bytes N  emit numbered payload files and snippets for checked inline transfers.

A bare name (``capture``, ``fill_lib``, ``guard``, ``ping``, ``upload``) resolves against the
skill folder this file sits in (``<skill>/scripts/browser/<name>.js``, then
``<skill>/scripts/_shared/browser/<name>.js``), never against the working directory.

CLI:
    python3 ltjs.py render <name|file> [--form expression|function] [--stub|--no-stub]
            [--arg k=v ...] [--arg-json k=<json> ...] [--args-file F.json] [--file PATH]
            [--plan plan.json] [--net|--no-net] [--out-dir D] [--config F] [--data-dir D]
    python3 ltjs.py check-url [--plan plan.json] [--from <current tab URL>] [--config F] <url>
    python3 ltjs.py check-upload [--config F] [--data-dir D] <path>

render prints {"path", "sha256", "form", "bytes", ...}. Exit codes: render 0 written,
1 refused (upload check), 2 usage, missing script, bad plan or unsafe output folder, 4
model_answer.js while model_answer.read is false. check-url 0 allowed, 1 forbidden, 2 error
(also forbids). check-upload 0 allowed, 1 refused, 2 error.

check-url maps URL page numbers to flow positions through the plan's own map (route numbers
count content pages only). With --from it also refuses a move that would pass a checkpoint
page or the stop on the way, in either direction.

Rendered files go to a folder only this user can open: --out-dir, else browser.js_tmp_dir,
else ``<OS temp>/lt-suite-js-<uid>`` (``lt-suite-js`` where there are no uids). The folder
must be a real directory owned by this user with no group or other permission bits; each file
is created new with mode 0600 and a random suffix, so nothing another account planted is read
or overwritten.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse  # noqa: E402
import base64  # noqa: E402
import gzip  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
import stat  # noqa: E402
import tempfile  # noqa: E402
from typing import Any, Mapping  # noqa: E402
from urllib.parse import parse_qs, urlsplit  # noqa: E402

from ltcore import config as config_mod  # noqa: E402
from ltcore import store as store_mod  # noqa: E402
from ltcore import __version__  # noqa: E402

NEEDS = ["browser/lt_guard.js", "browser/lt_net.js", "browser/guard.js"]

SHARED = Path(__file__).resolve().parent.parent
BROWSER = SHARED / "browser"
GUARD_JS = BROWSER / "lt_guard.js"
NET_JS = BROWSER / "lt_net.js"

FORMS = ("expression", "function")
MAX_UPLOAD = 8 * 1024 * 1024
MAGIC = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")
ARG_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
RUN_RE = re.compile(r"^/i/([^/]+)/student/courses/(\d+)/runs/(\d+)/run/(.*)$")
PAGE_RE = re.compile(r"^page/([1-9]\d*)$")
PREVIEW_RE = re.compile(r"/preview(/|$)", re.I)
FILL_NAMES = {"fill_lib", "confirm_dialog", "upload", "check_answer"}
FILL_PREFIXES = ("_fill-", "_verify-", "_check-", "_replace-", "_calibrate-")
CACHEABLE_BROWSER_NAMES = frozenset({
    "capture.js", "fetch_media.js", "fill_lib.js", "probe_panels.js", "check_answer.js",
    "confirm_dialog.js", "model_answer.js", "upload.js", "ping.js", "guard.js",
    "lt_guard.js", "lt_net.js", "fill_runner.js",
})
RESERVED = {"lt_guard", "lt_net"}
ADAPTER_FORM = {
    "claude-in-chrome": ("expression", True),
    "chrome-devtools-mcp": ("function", False),
    "antigravity": ("function", False),
    "playwright-mcp": ("function", False),
    "manual-paste": ("expression", False),
    "auto": ("expression", True),
}

EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_MODEL_ANSWER = 0, 1, 2, 4


class RenderRefused(Exception):
    """A render that must not happen. ``code`` is the CLI exit code."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class PlanError(ValueError):
    """The plan file is missing, unreadable or has a field of the wrong type."""


# --------------------------------------------------------------------------- locate

def skill_scripts_dir() -> Path | None:
    """``<skill>/scripts`` when this file is a skill's ``scripts/_shared`` copy, else None."""
    return SHARED.parent if SHARED.name == "_shared" else None


def resolve_target(target: str, cwd: str | os.PathLike | None = None) -> tuple[Path, str]:
    """(script path, name). A bare name never reads the working directory."""
    t = str(target)
    bare = t[:-3] if t.endswith(".js") else t
    if "/" not in t and os.sep not in t and NAME_RE.match(bare):
        if bare in RESERVED:
            raise RenderRefused(EXIT_USAGE, f"{bare}.js is prepended to every script; render a page script")
        tried = []
        scripts = skill_scripts_dir()
        if scripts is not None:
            tried.append(scripts / "browser" / f"{bare}.js")
        tried.append(BROWSER / f"{bare}.js")
        for p in tried:
            if p.is_file():
                return p, bare
        raise RenderRefused(EXIT_USAGE, f"no page script named {bare!r}; looked in: "
                            + ", ".join(str(p) for p in tried))
    base = Path.cwd() if cwd is None else Path(cwd)
    p = Path(os.path.expanduser(t))
    if not p.is_absolute():
        p = base / p
    if not p.is_file():
        raise RenderRefused(EXIT_USAGE, f"script file not found: {p}")
    name = p.name[:-3] if p.name.endswith(".js") else p.stem
    return p, name


def wants_net(path: Path | None, name: str) -> bool:
    """Fill scripts get lt_net.js: anything in the lt-module-fill skill, or a fill-named file."""
    if path is not None and "lt-module-fill" in path.resolve().parts:
        return True
    return name in FILL_NAMES or name.startswith(FILL_PREFIXES)


def default_form(cfg: "config_mod.Config | None") -> tuple[str, bool]:
    adapter = cfg.get("browser.adapter", "auto") if cfg is not None else "auto"
    return ADAPTER_FORM.get(adapter, ("expression", False))


def default_js_dir() -> Path:
    """``<OS temp>/lt-suite-js-<uid>``; ``lt-suite-js`` on systems without uids."""
    uid = os.getuid() if hasattr(os, "getuid") else None
    return Path(tempfile.gettempdir()) / ("lt-suite-js" if uid is None else f"lt-suite-js-{uid}")


def out_dir_for(cfg: "config_mod.Config | None", flag: str | os.PathLike | None = None) -> Path:
    """Rendered-script folder: --out-dir, then browser.js_tmp_dir, then default_js_dir().

    The last path part is never resolved, so a symlink there is seen and refused."""
    if flag:
        return Path(os.path.abspath(os.path.expanduser(str(flag))))
    v = cfg.get("browser.js_tmp_dir", "") if cfg is not None else ""
    if isinstance(v, str) and v:
        return Path(os.path.abspath(os.path.expanduser(v)))
    return default_js_dir()


def private_dir(d: Path) -> Path:
    """Create ``d`` with mode 0700, or check that it is safe to write scripts into.

    Safe: a real directory (no symlink or reparse point), owned by this user, with no group or other
    permission bits. Anything else raises RenderRefused(2) naming the problem."""
    def unsafe(why: str) -> RenderRefused:
        return RenderRefused(EXIT_USAGE, f"unsafe js_tmp_dir {d}: {why}. Use a folder only you "
                                         f"can open (mode 0700), or set browser.js_tmp_dir.")
    try:
        st = os.lstat(d)
    except FileNotFoundError:
        try:
            d.parent.mkdir(parents=True, exist_ok=True)
            os.mkdir(d, 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise unsafe(f"cannot create it ({exc.strerror or exc})") from None
        st = os.lstat(d)
    if getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise unsafe("it is a reparse point")
    if stat.S_ISLNK(st.st_mode):
        raise unsafe("it is a symlink")
    if not stat.S_ISDIR(st.st_mode):
        raise unsafe("it is not a directory")
    if hasattr(os, "getuid"):
        if st.st_uid != os.getuid():
            raise unsafe(f"it belongs to uid {st.st_uid}, not {os.getuid()}")
        if st.st_mode & 0o077:
            raise unsafe(f"mode {stat.S_IMODE(st.st_mode):04o} lets other accounts in")
    return d


# --------------------------------------------------------------------------- URL rules

def _no(reason: str, idx: int | None = None) -> dict:
    return {"allowed": False, "reason": reason, "page_index": idx}


def make_origin(scheme: str, host: str, port: int | None) -> str:
    host = host.lower()
    if ":" in host:
        host = f"[{host}]"
    default = {"https": 443, "http": 80}.get(scheme)
    return f"{scheme}://{host}" + (f":{port}" if port is not None and port != default else "")


def normalize_origin(value: str) -> str:
    """``https://host[:port]`` with the default port dropped; PlanError on anything else."""
    if not isinstance(value, str) or not value:
        raise PlanError("origin must be a non-empty string")
    try:
        u = urlsplit(value.strip())
        port = u.port
    except ValueError as exc:
        raise PlanError(f"origin {value!r} is not a URL: {exc}") from None
    scheme = u.scheme.lower()
    if scheme not in ("http", "https") or not u.hostname or u.username or u.password \
            or u.path not in ("", "/") or u.query or u.fragment:
        raise PlanError(f"origin {value!r} must look like https://host[:port] with no path")
    return make_origin(scheme, u.hostname, port)


def _bad_chars(url: str) -> bool:
    return any(c in "\\" or c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in url)


def classify(url: str, policy: Mapping[str, Any] | None) -> dict:
    """The navigation rule shared with lt_guard.js classify(). Returns
    {"allowed", "reason", "page_index"}; anything unexpected is forbidden."""
    p = dict(policy or {"mode": "block-all"})
    if not isinstance(url, str) or not url or _bad_chars(url):
        return _no("bad-url")
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        return _no("bad-url")
    scheme = u.scheme.lower()
    if scheme not in ("http", "https"):
        return _no("bad-scheme")
    if u.username is not None or u.password is not None or "@" in u.netloc:
        return _no("credentials-in-url")
    if not u.hostname:
        return _no("bad-url")
    path = u.path or "/"
    segs = path.split("/")
    if any(s in (".", "..") or s.lower() in ("%2e", "%2e%2e", ".%2e", "%2e.") for s in segs):
        return _no("bad-url")
    if PREVIEW_RE.search(path) or "preview" in parse_qs(u.query, keep_blank_values=True):
        return _no("preview")
    origin = make_origin(scheme, u.hostname, port)
    if p.get("origin_conflict"):
        return _no("origin-conflict")
    if p.get("origin") and origin != p["origin"]:
        return _no("other-origin")
    m = RUN_RE.match(path)
    if not m:
        return _no("not-a-run-page")
    if p.get("instance") is not None and m.group(1) != str(p["instance"]):
        return _no("wrong-instance")
    if p.get("course_id") is not None and int(m.group(2)) != int(p["course_id"]):
        return _no("wrong-course")
    if p.get("run_id") is not None and int(m.group(3)) != int(p["run_id"]):
        return _no("wrong-run")
    rest = m.group(4).rstrip("/")
    if rest == "complete" or rest.startswith("complete/"):
        return _no("complete")
    if rest == "checkpoint" or rest.startswith("checkpoint/"):
        return _no("checkpoint")
    n = None
    if rest != "background":
        pm = PAGE_RE.match(rest)
        if not pm:
            return _no("not-a-run-page")
        n = int(pm.group(1))
    if p.get("mode") != "plan":
        return _no("no-plan")
    if not p.get("origin"):
        return _no("no-origin")
    if rest in (p.get("never_routes") or []):
        return _no("never-navigated")
    idx = map_index(path, rest, n, p)
    if idx is None:
        # URL page numbers count content pages only and skip checkpoint pages, so without
        # the plan's own map a number says nothing certain about the flow position.
        return _no("page-not-in-plan" if p.get("mapped") else "plan-has-no-page-map")
    return index_rule(idx, p)


def map_index(path: str, rest: str, n: int | None, p: Mapping[str, Any]) -> int | None:
    """Flow index of a run URL through the plan's map: exact path, then URL page number."""
    key = path.rstrip("/")
    url_map = p.get("url_map") or {}
    page_map = p.get("page_map") or {}
    if key in url_map:
        return url_map[key]
    if rest == "background":
        return 1
    if n is not None and str(n) in page_map:
        return page_map[str(n)]
    return None


def index_rule(idx: int, p: Mapping[str, Any]) -> dict:
    """Report page, never-navigated index, checkpoint page and stop, applied to a flow index."""
    if idx in (p.get("report_indexes") or []):
        return _no("report-page", idx)
    if idx in (p.get("never_indexes") or []):
        return _no("never-navigated", idx)
    if idx in (p.get("checkpoint_indexes") or []):
        return _no("checkpoint", idx)
    sb = p.get("stop_before_page")
    if sb is not None and idx >= sb:
        return _no("at-or-past-stop", idx)
    return {"allowed": True, "reason": "allowed", "page_index": idx}


def cross_rule(frm: int, to: int, p: Mapping[str, Any]) -> dict | None:
    """Every flow position strictly between ``frm`` and ``to`` (either direction): none may be
    a checkpoint page or at or past the stop. Report and never-navigated pages may be passed."""
    sb = p.get("stop_before_page")
    cps = p.get("checkpoint_indexes") or []
    for i in range(min(frm, to) + 1, max(frm, to)):
        if i in cps:
            return _no("crosses-checkpoint", i)
        if sb is not None and i >= sb:
            return _no("crosses-stop", i)
    return None


def current_index(url: str, p: Mapping[str, Any]) -> tuple[str, int | None]:
    """Where the tab is under policy ``p``: ("index", n), ("outside", None) for a URL that is
    not a page of the plan's run, ("unmapped", None) for a run page the plan cannot place, or
    ("bad", None) for a URL that does not parse."""
    if not isinstance(url, str) or not url or _bad_chars(url):
        return "bad", None
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        return "bad", None
    if u.scheme.lower() not in ("http", "https") or not u.hostname:
        return "outside", None
    m = RUN_RE.match(u.path or "/")
    origin = make_origin(u.scheme.lower(), u.hostname, port)
    if (not m or (p.get("origin") and origin != p["origin"])
            or (p.get("instance") is not None and m.group(1) != str(p["instance"]))
            or (p.get("course_id") is not None and int(m.group(2)) != int(p["course_id"]))
            or (p.get("run_id") is not None and int(m.group(3)) != int(p["run_id"]))):
        return "outside", None
    rest = m.group(4).rstrip("/")
    pm = PAGE_RE.match(rest)
    idx = map_index(u.path, rest, int(pm.group(1)) if pm else None, p)
    return ("unmapped", None) if idx is None else ("index", idx)


def decide(url: str, policy: Mapping[str, Any], from_url: str | None = None) -> dict:
    """classify() plus, with ``from_url``, the crossing rule. Mirrors lt_guard.js decideNav()
    for a tab whose painted page is unknown."""
    r = classify(url, policy)
    r["crossing"] = "not-checked"
    if not r["allowed"] or from_url is None:
        return r
    kind, idx = current_index(from_url, policy)
    if kind == "bad":
        return {**_no("bad-from-url", r["page_index"]), "crossing": "error"}
    if kind == "outside":
        r["crossing"] = "outside-run"
        return r
    if kind == "unmapped":
        return {**_no("current-not-in-plan", r["page_index"]), "crossing": "unmapped"}
    x = cross_rule(idx, r["page_index"], policy)
    if x:
        return {**x, "crossing": "crosses", "from_index": idx}
    r.update({"crossing": "checked", "from_index": idx})
    return r


# --------------------------------------------------------------------------- plan

def load_plan(path: str | os.PathLike) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError as exc:
        raise PlanError(f"{path}: cannot read: {exc.strerror or exc}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PlanError(f"{path}: not valid JSON: {exc}") from None
    if not isinstance(doc, dict):
        raise PlanError(f"{path}: a plan is a JSON object")
    return doc


def _int(v: Any, what: str, *, minimum: int = 0) -> int:
    if isinstance(v, bool):
        raise PlanError(f"{what} must be an integer, got {v!r}")
    if isinstance(v, str) and v.isdigit():
        v = int(v)
    if not isinstance(v, int) or v < minimum:
        raise PlanError(f"{what} must be an integer >= {minimum}, got {v!r}")
    return v


def _route(v: str, what: str) -> str:
    r = v.strip().lstrip("/")
    if r.startswith("run/"):
        r = r[4:]
    r = r.rstrip("/")
    if not r or any(c.isspace() for c in r):
        raise PlanError(f"{what}: {v!r} is not a route")
    m = re.match(r"^(page|checkpoint)/0*(\d+)$", r)
    return f"{m.group(1)}/{int(m.group(2))}" if m else r


def _list(v: Any, what: str) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        raise PlanError(f"{what} must be a list")
    return v


def policy_from_plan(plan: Mapping[str, Any] | None, origin: str | None = None,
                     instance: str | None = None, *, check_answer_enabled: bool = False) -> dict:
    """Normalize a plan into the policy check-url and lt_guard.js read.

    ``origin`` and ``instance`` come from the config (lt.origin, lt.instance); the plan's own
    values win, and a plan origin that differs from a set lt.origin forbids every URL.
    No plan gives ``{"mode": "block-all"}``: every page change is forbidden."""
    cfg_origin = normalize_origin(origin) if origin else None
    if plan is None:
        return {"mode": "block-all", "origin": cfg_origin}
    if not isinstance(plan, Mapping):
        raise PlanError("a plan is a JSON object")
    lesson = plan.get("lesson") if isinstance(plan.get("lesson"), Mapping) else {}
    pol: dict[str, Any] = {"mode": "plan"}
    plan_origin = plan.get("origin", lesson.get("origin"))
    plan_origin = normalize_origin(plan_origin) if plan_origin else None
    pol["origin"] = plan_origin or cfg_origin
    pol["origin_conflict"] = bool(plan_origin and cfg_origin and plan_origin != cfg_origin)
    inst = plan.get("instance", lesson.get("instance")) or instance or None
    if inst is not None and (not isinstance(inst, str) or not re.match(r"^[A-Za-z0-9_-]+$", inst)):
        raise PlanError(f"instance must be letters, digits, _ or -, got {inst!r}")
    pol["instance"] = inst
    for k in ("course_id", "run_id"):
        v = plan.get(k, lesson.get(k))
        pol[k] = None if v is None else _int(v, k)
    sb = plan.get("stop_before_page")
    pol["stop_before_page"] = None if sb is None else _int(sb, "stop_before_page", minimum=1)

    never = plan.get("never_navigated")
    if never is None and isinstance(plan.get("boundary_map"), Mapping):
        never = plan["boundary_map"].get("never_navigated")
    routes: list[str] = []
    idxs: list[int] = []
    for i, e in enumerate(_list(never, "never_navigated")):
        what = f"never_navigated[{i}]"
        if isinstance(e, bool):
            raise PlanError(f"{what}: {e!r} is neither a route nor a page index")
        if isinstance(e, int):
            idxs.append(_int(e, what, minimum=1))
        elif isinstance(e, str):
            routes.append(_route(e, what))
        elif isinstance(e, Mapping):
            if e.get("route") is not None:
                if not isinstance(e["route"], str):
                    raise PlanError(f"{what}.route must be a string")
                routes.append(_route(e["route"], what))
            if e.get("page_index") is not None:
                idxs.append(_int(e["page_index"], f"{what}.page_index", minimum=1))
            if e.get("route") is None and e.get("page_index") is None:
                raise PlanError(f"{what} needs route or page_index")
        else:
            raise PlanError(f"{what}: {e!r} is neither a route nor a page index")

    report = [_int(v, f"report_pages[{i}]", minimum=1)
              for i, v in enumerate(_list(plan.get("report_pages"), "report_pages"))]
    checkpoints = [_int(v, f"checkpoint_pages[{i}]", minimum=1)
                   for i, v in enumerate(_list(plan.get("checkpoint_pages"), "checkpoint_pages"))]
    bm = plan.get("boundary_map") if isinstance(plan.get("boundary_map"), Mapping) else {}
    doors = list(_list(bm.get("doors"), "boundary_map.doors"))
    if isinstance(plan.get("first_boundary"), Mapping):
        doors.append(plan["first_boundary"])
    for i, d in enumerate(doors):
        if isinstance(d, Mapping) and d.get("class") == "checkpoint-page" and d.get("page_index") is not None:
            checkpoints.append(_int(d["page_index"], f"boundary door {i}.page_index", minimum=1))
    page_map: dict[str, int] = {}
    url_map: dict[str, int] = {}
    mapped = False
    for listname in ("pages", "skipped_pages", "flow_pages"):
        for i, pg in enumerate(_list(plan.get(listname), listname)):
            what = f"{listname}[{i}]"
            if not isinstance(pg, Mapping):
                raise PlanError(f"{what} must be an object")
            if pg.get("page_index") is None:
                continue
            idx = _int(pg["page_index"], f"{what}.page_index", minimum=1)
            if pg.get("is_report_page") is True or pg.get("reason") == "report-page":
                report.append(idx)
            if pg.get("is_checkpoint") is True or pg.get("reason") in ("checkpoint", "checkpoint-page"):
                checkpoints.append(idx)
            if pg.get("page_no") is not None:
                page_map[str(_int(pg["page_no"], f"{what}.page_no", minimum=1))] = idx
                mapped = True
            if pg.get("url"):
                if not isinstance(pg["url"], str):
                    raise PlanError(f"{what}.url must be a string")
                try:
                    up = urlsplit(pg["url"]).path.rstrip("/")
                except ValueError:
                    raise PlanError(f"{what}.url is not a URL") from None
                if up:
                    url_map[up] = idx
                    mapped = True
    pol.update({
        "never_routes": sorted(set(routes)), "never_indexes": sorted(set(idxs)),
        "report_indexes": sorted(set(report)), "checkpoint_indexes": sorted(set(checkpoints)),
        "page_map": page_map, "url_map": url_map, "mapped": mapped,
    })
    if check_answer_enabled is True and "check_answer_keys" in plan:
        keys = _list(plan["check_answer_keys"], "check_answer_keys")
        if any(not isinstance(p, list) or len(p) != 2 or not isinstance(p[1], str) for p in keys):
            raise PlanError("check_answer_keys must contain [page, panel_key] pairs")
        pol["check_answer_keys"] = [[_int(p[0], "check page", minimum=1), p[1]] for p in keys]
    return pol


def check_url(url: str, plan: Mapping[str, Any] | None, *, origin: str | None = None,
              instance: str | None = None, from_url: str | None = None) -> tuple[int, dict]:
    """(exit code, result). 0 allowed, 1 forbidden, 2 error (bad plan, bad --from URL or no
    origin). ``from_url`` is the tab's current URL; with it the move may not pass a checkpoint
    page or the stop."""
    try:
        pol = policy_from_plan(plan, origin, instance)
    except PlanError as exc:
        return EXIT_USAGE, {"allowed": False, "reason": "bad-plan", "error": str(exc), "page_index": None}
    r = decide(url, pol, from_url)
    r["url"] = url
    if r["reason"] == "no-origin":
        r["error"] = "lt.origin is not set and the plan has no origin"
        return EXIT_USAGE, r
    if r["reason"] == "bad-from-url":
        r["error"] = f"--from {from_url!r} is not a URL"
        return EXIT_USAGE, r
    return (EXIT_OK if r["allowed"] else EXIT_REFUSED), r


# --------------------------------------------------------------------------- upload

def check_upload(path: str | os.PathLike, data_dir: str | os.PathLike, *,
                 cwd: str | os.PathLike | None = None,
                 max_bytes: int = MAX_UPLOAD) -> tuple[int, dict, bytes | None]:
    """(exit code, result, bytes). 0 only for a regular PNG or JPEG file, not a symlink,
    inside the data folder, at most ``max_bytes``."""
    base = Path.cwd() if cwd is None else Path(cwd)
    p = Path(os.path.expanduser(str(path)))
    if not p.is_absolute():
        p = base / p
    res: dict[str, Any] = {"ok": False, "path": str(p)}
    try:
        lst = os.lstat(p)
    except OSError as exc:
        res["reason"] = f"cannot read: {exc.strerror or exc}"
        return EXIT_REFUSED, res, None
    if stat.S_ISLNK(lst.st_mode):
        res["reason"] = "symlink"
        return EXIT_REFUSED, res, None
    if not stat.S_ISREG(lst.st_mode):
        res["reason"] = "not a regular file"
        return EXIT_REFUSED, res, None
    real = Path(os.path.realpath(p))
    root = Path(os.path.realpath(data_dir))
    try:
        real.relative_to(root)
    except ValueError:
        res["reason"] = f"outside the data folder {root}"
        return EXIT_REFUSED, res, None
    if lst.st_size > max_bytes:
        res["reason"] = f"{lst.st_size} bytes; the limit is {max_bytes}"
        return EXIT_REFUSED, res, None
    if lst.st_size == 0:
        res["reason"] = "empty file"
        return EXIT_REFUSED, res, None
    try:
        fd = os.open(real, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        res["reason"] = f"cannot read: {exc.strerror or exc}"
        return EXIT_REFUSED, res, None
    with os.fdopen(fd, "rb") as fh:
        fst = os.fstat(fh.fileno())
        if not stat.S_ISREG(fst.st_mode) or (fst.st_dev, fst.st_ino) != (lst.st_dev, lst.st_ino):
            res["reason"] = "the file changed while it was checked"
            return EXIT_REFUSED, res, None
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        res["reason"] = f"more than {max_bytes} bytes"
        return EXIT_REFUSED, res, None
    mime = next((t for magic, t in MAGIC.items() if data.startswith(magic)), None)
    if mime is None:
        res["reason"] = "not a PNG or JPEG file (magic bytes)"
        return EXIT_REFUSED, res, None
    res.update({"ok": True, "reason": "ok", "bytes": len(data), "type": mime,
                "sha256": hashlib.sha256(data).hexdigest()})
    return EXIT_OK, res, data


# --------------------------------------------------------------------------- render

def _js_string(obj: Any, *, sort_keys: bool = True) -> str:
    """A JS string literal holding the JSON text of ``obj`` (ASCII only)."""
    return json.dumps(json.dumps(obj, ensure_ascii=True, sort_keys=sort_keys, allow_nan=False))


FREEZE = ("(function f(o) { if (o && typeof o === 'object' && !Object.isFrozen(o)) "
          "{ Object.freeze(o); for (const k of Object.keys(o)) f(o[k]); } return o; })")


def _read(p: Path) -> str:
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def function_text(body: str, *, name: str, args: Mapping[str, Any] | None,
                  policy: Mapping[str, Any] | None, net: bool,
                  parameterized: bool = False, form: str = "expression") -> str:
    """The script in function form: ``async () => { prelude; return LtGuard.run(...) }``."""
    pol = dict(policy or {"mode": "block-all"})
    parts = [
        "async (__ltArgs, __ltPolicy) => {" if parameterized else "async () => {",
        f"/* lt-suite page script {_safe_name(name)}, rendered by ltcore/ltjs.py. Edit the source,",
        "   not this file. Inputs: __LT_ARGS__ (frozen). Prepended: lt_guard.js"
        + (", lt_net.js. */" if net else ". */"),
        ("let" if parameterized else "const") + f" __LT_ARGS__ = {FREEZE}(" + ("__ltArgs" if parameterized else f"JSON.parse({_js_string(dict(args or {}))})") + ");",
        ("let" if parameterized else "const") + f" __LT_POLICY__ = {FREEZE}(" + ("__ltPolicy" if parameterized else f"JSON.parse({_js_string(pol)})") + ");",
    ]
    if parameterized:
        parts.append("try {")
    parts.append(_read(GUARD_JS).rstrip("\n") + ";")
    if net:
        parts.append(_read(NET_JS).rstrip("\n") + ";")
    parts.append("return await globalThis.LtGuard.run(__LT_POLICY__, async () => {")
    parts.append(body.rstrip("\n"))
    parts.append("}, { net: " + ("true" if net else "false") + " });")
    if parameterized:
        parts.append("} finally { __LT_ARGS__ = undefined; __LT_POLICY__ = undefined; __ltArgs = undefined; __ltPolicy = undefined; }")
    parts.append("}")
    if parameterized:
        parts.insert(1, f"/* lt-suite {__version__} {form} parameterized body accepts current frozen inputs. */")
    return "\n".join(parts) + "\n"


def _payload(inner: str, compress: bool = True) -> tuple[str, str]:
    """Encode rendered bytes without changing their contents before the integrity check."""
    raw = inner.encode("utf-8")
    packed = gzip.compress(raw, compresslevel=9, mtime=0) if compress else raw
    return base64.b64encode(packed).decode("ascii"), hashlib.sha256(raw).hexdigest()


def stub_text(inner: str, name: str, *, args: Mapping[str, Any] | None = None,
              policy: Mapping[str, Any] | None = None, compress: bool = True,
              cache: bool = False, chunked: bool = False, cacheable: bool = False) -> str:
    """Verify browser payload bytes before calling their body with fresh frozen inputs."""
    b64, want = _payload(inner, compress)
    lines = [
        "async () => {",
        f"/* Verify the {_safe_name(name)} body before running code with current inputs. */",
        f"const WANT = \"{want}\";",
        "const KEY = 'lt-suite:js:' + WANT, INDEX = 'lt-suite:js:index';",
        "const miss = () => ({ok:false, need:'payload', want:WANT.slice(0,12)});",
    ]
    if cache and not cacheable:
        return "\n".join(lines + ["return miss();", "}"]) + "\n"
    if cache:
        lines += [
            "let B64;",
            "try { B64 = localStorage.getItem(KEY); } catch (e) { return miss(); }",
            "if (B64 === null) return miss();",
            "const fail = () => {",
            "  try { const s = localStorage;",
            "    if (s.getItem(KEY) !== B64) return miss();",
            "    s.removeItem(KEY);",
            "    if (s.getItem(KEY) === null) { const a = JSON.parse(s.getItem(INDEX));",
            "      if (Array.isArray(a)) s.setItem(INDEX, JSON.stringify(a.filter(k => k !== KEY)));",
            "    }",
            "  } catch (e) {}",
            "  return miss();",
            "};",
        ]
    else:
        lines += [
            "const fail = () => ({ok:false, fatal:'stub-hash-mismatch'});",
        ]
        if chunked:
            lines += [
                "let B64;",
                "try { const p = globalThis.__ltSuitePayload; const a = p && p[WANT];",
                "  if (p) delete p[WANT];",
                "  if (!Array.isArray(a) || !a.length || a.some(s => typeof s !== 'string')) return fail();",
                "  B64 = a.join('');",
                "} catch (e) { return fail(); }",
            ]
        else:
            lines.append(f"const B64 = \"{b64}\";")
    if compress:
        lines.append("if (typeof DecompressionStream !== 'function') return {ok:false, fatal:'stub-no-decompression'};")
    lines += [
        "let bytes, src;",
        "try {",
        "  if (typeof B64 !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(B64)) return fail();",
        "  const bin = atob(B64);",
        "  bytes = new Uint8Array(bin.length);",
        "  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);",
    ]
    if compress:
        lines.append("  bytes = new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer());")
    lines += [
        "  const dig = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));",
        "  const got = Array.from(dig, b => b.toString(16).padStart(2, '0')).join('');",
        "  if (got !== WANT) return fail();",
        "  src = new TextDecoder('utf-8', {fatal:true}).decode(bytes);",
        "} catch (e) { return fail(); }",
    ]
    if not cache and compress and cacheable:
        lines += [
            "try {",
            "  const s = localStorage, valid = k => /^lt-suite:js:[a-f0-9]{64}$/.test(k);",
            "  s.setItem(KEY, B64);",
            "  let a; try { a = JSON.parse(s.getItem(INDEX)); } catch (e) {}",
            "  const entries = new Map();",
            "  for (let i = 0; i < s.length; i++) { const k = s.key(i);",
            "    if (valid(k)) { const v = s.getItem(k); if (v !== null) entries.set(k, v); }",
            "  }",
            "  a = Array.isArray(a) ? [...new Set(a.filter(k => typeof k === 'string' && valid(k) && entries.has(k)))] : [];",
            "  for (const k of [...entries.keys()].sort()) if (!a.includes(k)) a.push(k);",
            "  const keep = new Set([KEY, ...a.filter(k => k !== KEY).slice(-5)]);",
            "  s.setItem(INDEX, JSON.stringify(a));",
            "  const remove = a.filter(k => !keep.has(k));",
            "  for (const k of remove) if (s.getItem(k) === entries.get(k)) s.removeItem(k);",
            "  if (remove.length) s.setItem(INDEX, JSON.stringify(a.filter(k => s.getItem(k) !== null)));",
            "} catch (e) {}",
        ]
    lines += [
        "return await (new Function('return (' + src + ')'))()("
        f"JSON.parse({_js_string(dict(args or {}), sort_keys=False)}), JSON.parse({_js_string(dict(policy or {'mode': 'block-all'}))}));",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _form_text(fn: str, form: str) -> str:
    return fn if form == "function" else "await (" + fn.rstrip("\n") + ")();\n"


def chunk_text(payload: str, want: str, i: int, form: str) -> str:
    """Append one verified chunk in order and return its measured transfer receipt."""
    digest = hashlib.sha256(payload.encode("ascii")).hexdigest()
    fn = "\n".join([
        "async () => {",
        f"const WANT = {json.dumps(want)}, B64 = {json.dumps(payload)}, I = {i};",
        "const fail = () => { try { if (globalThis.__ltSuitePayload) delete globalThis.__ltSuitePayload[WANT]; } catch(e) {} return {ok:false, fatal:'stub-chunk-mismatch', i:I}; };",
        "try {",
        "  const d = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(B64)));",
        "  const sha = Array.from(d, b => b.toString(16).padStart(2,'0')).join('');",
        f"  if (sha !== {json.dumps(digest)}) {{ if (globalThis.__ltSuitePayload) delete globalThis.__ltSuitePayload[WANT]; return fail(); }}",
        "  const p = globalThis.__ltSuitePayload || (globalThis.__ltSuitePayload = Object.create(null));",
        "  if (I === 1) p[WANT] = [];",
        "  if (!Array.isArray(p[WANT]) || p[WANT].length !== I - 1) { delete p[WANT]; return fail(); }",
        "  p[WANT].push(B64);",
        "  return {i:I, len:B64.length, sha16:sha.slice(0,16)};",
        "} catch (e) { return fail(); }",
        "}",
    ]) + "\n"
    return _form_text(fn, form)


_TAIL_AWAIT = re.compile(r"^await\s+(?P<expr>.+?);?\s*$")


def tail_return(body: str) -> str:
    """A body whose last statement is a column-0 ``await <call>;`` returns that value.

    Generated fill scripts end that way (the old expression form); the wrapper needs
    ``return await <call>;``. Bodies that already return are unchanged."""
    lines = body.rstrip("\n").split("\n")
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i]
        if not s.strip() or s.strip().startswith("//"):
            continue
        m = _TAIL_AWAIT.match(s)
        if m and not s.startswith((" ", "\t")):
            lines[i] = f"return await {m.group('expr')};"
        break
    return "\n".join(lines) + "\n"


def render_source(body: str, *, name: str, form: str = "expression", stub: bool = False,
                  args: Mapping[str, Any] | None = None, net: bool = False,
                  policy: Mapping[str, Any] | None = None, compress: bool = True,
                  cache: bool = False, chunked: bool = False, cacheable: bool = False) -> str:
    """Rendered script text for ``body`` (a function body that ends with ``return``)."""
    if form not in FORMS:
        raise RenderRefused(EXIT_USAGE, f"form must be one of {', '.join(FORMS)}, got {form!r}")
    try:
        fn = function_text(tail_return(body), name=name, args=args, policy=policy, net=net,
                           parameterized=stub or cache, form=form)
        if stub or cache:
            fn = stub_text(fn, name, args=args, policy=policy, compress=compress, cache=cache,
                           chunked=chunked, cacheable=cacheable)
    except (TypeError, ValueError) as exc:
        raise RenderRefused(EXIT_USAGE, f"arguments must be plain JSON values: {exc}") from None
    return _form_text(fn, form)


def _safe_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "script"
    return s[:64]


def cacheable_target(path: Path) -> bool:
    """Cache bundled browser code while excluding generated files that can embed answers."""
    if path.name not in CACHEABLE_BROWSER_NAMES or path.name.startswith(FILL_PREFIXES):
        return False
    parents = {BROWSER.resolve()}
    scripts = skill_scripts_dir()
    if scripts is not None:
        parents.add((scripts / "browser").resolve())
    elif SHARED.name == "shared":
        parents.update(p.resolve() for p in (SHARED.parent / "skills").glob("*/scripts/browser"))
    return path.resolve().parent in parents


def browser_body(path: Path) -> str:
    """Compose the shipped fill runner from libraries beside its trusted source file."""
    body = _read(path)
    if path.name != "fill_runner.js" or not cacheable_target(path):
        return body
    probe = _read(path.with_name("probe_panels.js"))
    probe, count = re.subn(r"\n[ \t]*return await probeLtPanels\(\);[ \t]*\n?$", "\n", probe)
    if count != 1:
        raise RenderRefused(EXIT_USAGE, "probe entry point changed")
    check = _read(path.with_name("check_answer.js"))
    tail = "return await LtCheck.run(__LT_ARGS__.cfg, __LT_ARGS__.jobs);"
    if not check.rstrip().endswith(tail):
        raise RenderRefused(EXIT_USAGE, "check entry point changed")
    check = check.rstrip()[:-len(tail)]
    return body.replace("/*__LT_FILL_LIB__*/", _read(path.with_name("fill_lib.js"))).replace(
        "/*__LT_PROBE__*/", probe).replace("/*__LT_CHECK__*/", check)


def render_fill_call(target: str, *, args: Mapping[str, Any], **options) -> dict:
    """Render one loader and its checked chunked fallback with identical call arguments."""
    info = render(target, args=args, cache=True, **options)
    info["payload"] = render(target, args=args, chunk_bytes=20000, **options)
    return info


def _write(out_dir: Path, name: str, text: str) -> tuple[Path, str]:
    """Write a new file ``<name>-<sha8>-<random8>.js`` with mode 0600; never follow or reuse."""
    data = text.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    private_dir(out_dir)
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_BINARY", 0))
    for _ in range(8):
        path = out_dir / f"{_safe_name(name)}-{digest[:8]}-{secrets.token_hex(4)}.js"
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
        except BaseException:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        return path, digest
    raise RenderRefused(EXIT_USAGE, f"could not create a new file in {out_dir}")


def render(target: str, *, form: str | None = None, stub: bool | None = None,
           args: Mapping[str, Any] | None = None, net: bool | None = None,
           plan: Mapping[str, Any] | str | os.PathLike | None = None,
           cfg: "config_mod.Config | None" = None, out_dir: str | os.PathLike | None = None,
           file: str | os.PathLike | None = None, data_dir: str | os.PathLike | None = None,
           cwd: str | os.PathLike | None = None, compress: bool = True,
           cache: bool = False, chunk_bytes: int | None = None) -> dict:
    """Render ``target`` to ``<out_dir>/<name>-<sha8>.js`` and describe the file.

    Raises RenderRefused (exit code in ``.code``) or PlanError."""
    path, name = resolve_target(target, cwd)
    if cfg is None:
        cfg = _load_cfg(None)
    if name == "model_answer" and cfg is not None and cfg.get("model_answer.read", True) is False:
        raise RenderRefused(EXIT_MODEL_ANSWER,
                            "model_answer.read is false in the config, so model_answer.js is not rendered")
    if (name == "check_answer" or name.startswith("_check-")) and cfg.get("fill.check_answer.enabled", False) is not True:
        raise RenderRefused(5, "fill.check_answer.enabled is false; check script refused")
    if name == "fill_runner" and (args or {}).get("method") == "check" and cfg.get("fill.check_answer.enabled", False) is not True:
        raise RenderRefused(5, "fill.check_answer.enabled is false; check script refused")
    dform, dstub = default_form(cfg)
    form = form or dform
    if cache and (stub is False or not compress or chunk_bytes is not None):
        raise RenderRefused(EXIT_USAGE, "--cache cannot combine with --no-stub, --no-compress or --chunk-bytes")
    if chunk_bytes is not None and (chunk_bytes <= 0 or stub is False):
        raise RenderRefused(EXIT_USAGE, "--chunk-bytes needs a positive size and a stub")
    if cache or chunk_bytes is not None:
        stub = True
    stub = dstub if stub is None else bool(stub)
    if name == "probe_panels" and stub and compress and cacheable_target(path):
        runner = path.with_name("fill_runner.js")
        if runner.is_file() and (cache or chunk_bytes is not None):
            return render(str(runner), args={"method": "probe"}, form=form, stub=True, net=True,
                          plan=plan, cfg=cfg, out_dir=out_dir, cache=cache, chunk_bytes=chunk_bytes)
        if runner.is_file():
            return render_fill_call(str(runner), args={"method": "probe"}, form=form, net=True,
                                    plan=plan, cfg=cfg, out_dir=out_dir)
    net = wants_net(path, name) if net is None else bool(net)
    a: dict[str, Any] = dict(args or {})
    for k in a:
        if not ARG_KEY_RE.match(str(k)):
            raise RenderRefused(EXIT_USAGE, f"argument name {k!r} must be letters, digits and _")
    if file is not None:
        if data_dir is None:
            data_dir = store_mod.Store(cfg).data_dir
        code, res, data = check_upload(file, data_dir, cwd=cwd)
        if code != EXIT_OK or data is None:
            raise RenderRefused(EXIT_REFUSED, f"upload refused: {res.get('reason')} ({res.get('path')})")
        a["file"] = {"name": Path(res["path"]).name, "type": res["type"], "bytes": res["bytes"],
                     "sha256": res["sha256"], "b64": base64.b64encode(data).decode("ascii")}
    if isinstance(plan, (str, os.PathLike)):
        plan = load_plan(plan)
    origin = cfg.get("lt.origin", "") if cfg is not None else ""
    inst = cfg.get("lt.instance", "") if cfg is not None else ""
    policy = policy_from_plan(plan, origin or None, inst or None, check_answer_enabled=cfg.get("fill.check_answer.enabled", False))
    body = browser_body(path)
    cacheable = cacheable_target(path)
    text = render_source(body, name=name, form=form, stub=stub, args=a, net=net, policy=policy,
                         compress=compress, cache=cache, chunked=chunk_bytes is not None,
                         cacheable=cacheable)
    directory = out_dir_for(cfg, out_dir)
    body_hash = None
    chunks = []
    if stub:
        inner = function_text(tail_return(body), name=name, args=None, policy=None, net=net,
                              parameterized=True, form=form)
        payload, body_hash = _payload(inner, compress)
        if chunk_bytes is not None:
            for n, start in enumerate(range(0, len(payload), chunk_bytes), 1):
                part = payload[start:start + chunk_bytes]
                label = f"{name}-chunk-{n:03}"
                payload_path, part_hash = _write(directory, label + "-payload", part)
                snippet_path, _ = _write(directory, label, chunk_text(part, body_hash, n, form))
                chunks.append({"i": n, "path": str(snippet_path), "payload_path": str(payload_path),
                               "len": len(part), "sha16": part_hash[:16]})
    out, digest = _write(directory, name, text)
    return {"path": str(out), "sha256": digest, "form": form, "bytes": len(text.encode("utf-8")),
            "name": name, "source": str(path), "stub": stub, "net": net, "guard": policy["mode"],
            "cache": cache, "compression": ("gzip" if compress else "none") if stub else None,
            "body_sha256": body_hash, "chunks": chunks, "cacheable": cacheable}


def _needs_probe() -> None:
    """Open every shared file this module reads (tests/core/test_needs.py)."""
    for rel in NEEDS:
        _read(SHARED / rel)


# --------------------------------------------------------------------------- CLI

def _parse_args(a: argparse.Namespace) -> dict:
    out: dict[str, Any] = {}
    if a.args_file:
        try:
            with open(a.args_file, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            raise RenderRefused(EXIT_USAGE, f"--args-file {a.args_file}: {exc}") from None
        if not isinstance(doc, dict):
            raise RenderRefused(EXIT_USAGE, f"--args-file {a.args_file}: must hold a JSON object")
        out.update(doc)
    for kv in a.arg or []:
        k, sep, v = kv.partition("=")
        if not sep:
            raise RenderRefused(EXIT_USAGE, f"--arg {kv!r}: use key=value")
        out[k] = v
    for kv in a.arg_json or []:
        k, sep, v = kv.partition("=")
        if not sep:
            raise RenderRefused(EXIT_USAGE, f"--arg-json {kv!r}: use key=<json>")
        try:
            out[k] = json.loads(v)
        except ValueError as exc:
            raise RenderRefused(EXIT_USAGE, f"--arg-json {k}: not JSON: {exc}") from None
    return out


def _load_cfg(path: str | None, *, required: bool = True) -> "config_mod.Config | None":
    try:
        return config_mod.load(path)
    except config_mod.ConfigError as exc:
        if required:
            raise RenderRefused(EXIT_USAGE, f"config: {exc}") from None
        return None


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    from ltcore.streams import configure_utf8
    configure_utf8()

    ap = argparse.ArgumentParser(prog="ltjs.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="write a page script for a browser tool")
    r.add_argument("target", help="script name (capture, fill_lib, guard, ping, upload, ...) or file")
    r.add_argument("--form", choices=FORMS, help="default from browser.adapter")
    r.add_argument("--stub", dest="stub", action="store_true", default=None,
                   help="gzip base64 plus sha256 stub (default for claude-in-chrome and auto)")
    r.add_argument("--no-stub", dest="stub", action="store_false")
    r.add_argument("--cache", action="store_true", help="load verified gzip code from the page cache")
    r.add_argument("--no-compress", dest="compress", action="store_false",
                   help="use a legacy uncompressed stub without page caching")
    r.add_argument("--chunk-bytes", type=int, metavar="N", help="split payload into chunks, for example 20000")
    r.add_argument("--arg", action="append", metavar="K=V", help="string argument")
    r.add_argument("--arg-json", action="append", metavar="K=JSON", help="JSON argument")
    r.add_argument("--args-file", help="JSON object of arguments")
    r.add_argument("--file", help="PNG or JPEG inside the data folder to embed (upload)")
    r.add_argument("--plan", help="fill plan; bakes the navigation rules into the guard")
    r.add_argument("--net", dest="net", action="store_true", default=None, help="prepend lt_net.js")
    r.add_argument("--no-net", dest="net", action="store_false")
    r.add_argument("--out-dir", help="default browser.js_tmp_dir, else <OS temp>/lt-suite-js")
    r.add_argument("--config", help="config file")
    r.add_argument("--data-dir", help="data folder (for --file)")

    c = sub.add_parser("check-url", help="may the agent navigate to this URL?")
    c.add_argument("url")
    c.add_argument("--plan", help="fill plan (without one every page URL is forbidden)")
    c.add_argument("--from", dest="from_url", metavar="URL",
                   help="the tab's current URL; refuses a move past a checkpoint page or the stop")
    c.add_argument("--config", help="config file")

    u = sub.add_parser("check-upload", help="may this file be uploaded?")
    u.add_argument("path")
    u.add_argument("--config", help="config file")
    u.add_argument("--data-dir", help="data folder")

    a = ap.parse_args(argv)

    if a.cmd == "render":
        try:
            cfg = _load_cfg(a.config)
            data_dir = store_mod.Store(cfg, data_dir=a.data_dir).data_dir if a.file else None
            info = render(a.target, form=a.form, stub=a.stub, args=_parse_args(a), net=a.net,
                          plan=a.plan, cfg=cfg, out_dir=a.out_dir, file=a.file, data_dir=data_dir,
                          compress=a.compress, cache=a.cache, chunk_bytes=a.chunk_bytes)
        except RenderRefused as exc:
            print(f"ltjs render: {exc}", file=sys.stderr)
            return exc.code
        except PlanError as exc:
            print(f"ltjs render: bad plan: {exc}", file=sys.stderr)
            return EXIT_USAGE
        except OSError as exc:
            print(f"ltjs render: {exc}", file=sys.stderr)
            return EXIT_USAGE
        _emit(info)
        return EXIT_OK

    if a.cmd == "check-url":
        plan = None
        if a.plan:
            try:
                plan = load_plan(a.plan)
            except PlanError as exc:
                _emit({"allowed": False, "reason": "bad-plan", "error": str(exc), "url": a.url})
                print(f"ltjs check-url: {exc}", file=sys.stderr)
                return EXIT_USAGE
        try:
            cfg = _load_cfg(a.config)
        except RenderRefused as exc:
            _emit({"allowed": False, "reason": "bad-config", "error": str(exc), "url": a.url})
            print(f"ltjs check-url: {exc}", file=sys.stderr)
            return EXIT_USAGE
        origin = cfg.get("lt.origin", "") if cfg is not None else ""
        inst = cfg.get("lt.instance", "") if cfg is not None else ""
        code, res = check_url(a.url, plan, origin=origin or None, instance=inst or None,
                              from_url=a.from_url)
        _emit(res)
        if code == EXIT_USAGE:
            print(f"ltjs check-url: {res.get('error')}", file=sys.stderr)
        return code

    if a.cmd == "check-upload":
        try:
            cfg = _load_cfg(a.config)
        except RenderRefused as exc:
            print(f"ltjs check-upload: {exc}", file=sys.stderr)
            return EXIT_USAGE
        data_dir = store_mod.Store(cfg, data_dir=a.data_dir).data_dir
        code, res, _ = check_upload(a.path, data_dir)
        res["data_dir"] = str(data_dir)
        _emit(res)
        return code
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
