# Generated from shared/ltcore/config.py by tools/sync_shared.py. Edit the source.
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find, load, validate and write the lt-suite config file (one TOML file).

Lookup order, first hit wins:
  1. --config <path> on any script
  2. $LT_SUITE_CONFIG
  3. ./lt-suite.toml in the current folder (sandboxed agents can only write here)
  4. the platform file: ${XDG_CONFIG_HOME:-~/.config}/lt-suite/config.toml on Linux,
     ~/Library/Application Support/lt-suite/config.toml on macOS,
     %APPDATA%\\lt-suite\\config.toml on Windows
  5. no file: built-in defaults only

The effective config is config.defaults.toml, then style.defaults.toml when it
exists, then the user file, merged table by table (arrays replace). Relative paths
in the user file resolve against the file's own folder and ``~`` expands. The
schema in schemas/config.schema.json is checked by a small built-in validator, so
validation works with the standard library alone.

CLI:
    python3 -m ltcore.config show [--origins] [--config F]
    python3 -m ltcore.config path [--config F]
    python3 -m ltcore.config get <key> [--config F]
    python3 -m ltcore.config validate [--config F]
    python3 -m ltcore.config locate [--config F]
    python3 -m ltcore.config write --from <answers.toml|.json> [--force] [--stdout] [--config F]

Exit codes: 0 ok, 1 invalid, refused or unknown key, 2 usage, 3 missing config file
or unset key (``get`` on a key whose value is "" or []).
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse  # noqa: E402
import copy  # noqa: E402
import difflib  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import tempfile  # noqa: E402
import tomllib  # noqa: E402
import time  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Mapping  # noqa: E402

from ltcore import paths, tomlw  # noqa: E402

NEEDS = ["config.defaults.toml", "schemas/config.schema.json"]
# Opened when present; sync.toml decides which skills get it.
OPTIONAL_NEEDS = ["style.defaults.toml"]

SHARED = Path(__file__).resolve().parent.parent
DEFAULTS_FILE = SHARED / "config.defaults.toml"
STYLE_DEFAULTS_FILE = SHARED / "style.defaults.toml"
SCHEMA_FILE = SHARED / "schemas" / "config.schema.json"

SCHEMA_VERSION = 1
ENV_CONFIG = "LT_SUITE_CONFIG"
ENV_DATA = "LT_SUITE_DATA"
ENV_CACHE = "LT_SUITE_CACHE"
ENV_NO_INSTALL = "LT_SUITE_NO_INSTALL"

SETUP_HINT = "Run the lt-suite-setup skill or set it in {path}."
_MISSING = object()


def is_unset(value: Any) -> bool:
    """True for the values that mean "not set": None, "" and []."""
    return value is None or value == "" or value == []


class ConfigError(Exception):
    """The config file is missing, unreadable or invalid. The message says which."""


# --------------------------------------------------------------------------- locate

@dataclass
class Location:
    """Where the config comes from and where a new one would be written."""
    path: Path | None   # file to read; None means no file (defaults only)
    source: str         # flag --config | env LT_SUITE_CONFIG | cwd ./lt-suite.toml | platform | none
    target: Path        # file write() replaces or creates
    target_source: str

    @property
    def exists(self) -> bool:
        return self.path is not None and self.path.is_file()


def _abs(p: str | os.PathLike, base: Path) -> Path:
    q = Path(os.path.expanduser(str(p)))
    return q if q.is_absolute() else base / q


def locate(flag: str | os.PathLike | None = None, *, environ: Mapping[str, str] | None = None,
           cwd: str | os.PathLike | None = None, platform: str | None = None) -> Location:
    """Apply the lookup order. A flag or env path is returned even when it is missing."""
    env = os.environ if environ is None else environ
    here = Path.cwd() if cwd is None else Path(cwd)
    if flag:
        p = _abs(flag, here)
        return Location(p, "flag --config", p, "flag --config")
    if env.get(ENV_CONFIG):
        p = _abs(env[ENV_CONFIG], here)
        return Location(p, f"env {ENV_CONFIG}", p, f"env {ENV_CONFIG}")
    ws = here / paths.WORKSPACE_CONFIG
    if ws.is_file():
        return Location(ws, f"cwd ./{paths.WORKSPACE_CONFIG}", ws, f"cwd ./{paths.WORKSPACE_CONFIG}")
    plat = Path(str(paths.config_file(platform, env)))
    if plat.is_file():
        return Location(plat, "platform", plat, "platform")
    return Location(None, "none", plat, "platform")


def probe_dir(path: str | os.PathLike) -> tuple[bool, str]:
    """Can a file be created at or under ``path``? Tries a real temp file, then removes it."""
    p = Path(path)
    probe = p
    while not probe.exists():
        if probe.parent == probe:
            return False, f"no existing parent folder for {p}"
        probe = probe.parent
    if not probe.is_dir():
        return False, f"{probe} is not a folder"
    try:
        fd, name = tempfile.mkstemp(prefix=".lt-suite-probe-", dir=probe)
        os.close(fd)
        os.unlink(name)
    except OSError as exc:
        return False, f"cannot write in {probe}: {exc.strerror or exc}"
    return True, f"writable ({probe})"


def probe_loopback() -> tuple[bool, str]:
    """Can this process bind and listen on 127.0.0.1? The capture sink needs it."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
    except OSError as exc:
        return False, f"cannot bind 127.0.0.1: {exc.strerror or exc}"
    return True, f"bound 127.0.0.1:{port}"


WORKSPACE_VALUES = {
    "paths": {"data_dir": ".lt-suite/data", "downloads_mode": "copy"},
    "browser": {"transport": "download"},
}


def locate_report(flag: str | os.PathLike | None = None, *,
                  environ: Mapping[str, str] | None = None,
                  cwd: str | os.PathLike | None = None,
                  probes: Mapping[str, Any] | None = None) -> dict:
    """What setup.py locate prints: the config path, whether it exists, probe results
    and a proposal. Any failed probe proposes the workspace layout (./lt-suite.toml,
    .lt-suite/data, download transport, copy mode)."""
    env = os.environ if environ is None else environ
    here = Path.cwd() if cwd is None else Path(cwd)
    loc = locate(flag, environ=env, cwd=here)
    if probes is None:
        from ltcore import store  # late import: store imports this module
        data, data_src = store.resolve_data_dir(environ=env, cwd=here, location=loc)
        cfg_ok, cfg_msg = probe_dir(loc.target.parent)
        data_ok, data_msg = probe_dir(data)
        lb_ok, lb_msg = probe_loopback()
        probes = {
            "config_dir": {"ok": cfg_ok, "path": str(loc.target.parent), "detail": cfg_msg},
            "data_dir": {"ok": data_ok, "path": str(data), "from": data_src, "detail": data_msg},
            "loopback": {"ok": lb_ok, "detail": lb_msg},
        }
    failed = [k for k, v in probes.items() if not v.get("ok")]
    if failed and loc.source in ("none", "platform"):
        proposal = {"path": str(here / paths.WORKSPACE_CONFIG), "workspace": True,
                    "values": copy.deepcopy(WORKSPACE_VALUES),
                    "reason": "probe failed: " + ", ".join(failed)}
    else:
        proposal = {"path": str(loc.target), "workspace": False, "values": {},
                    "reason": "all probes passed" if not failed
                    else f"config path fixed by {loc.source}"}
    return {
        "config": {"path": str(loc.path) if loc.path else None, "source": loc.source,
                   "exists": loc.exists, "target": str(loc.target),
                   "target_source": loc.target_source},
        "probes": dict(probes),
        "proposal": proposal,
    }


# --------------------------------------------------------------------------- read

def read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: not valid TOML: {exc}") from None
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc.strerror or exc}") from None


def schema() -> dict:
    """The config JSON schema (shared/schemas/config.schema.json)."""
    with open(SCHEMA_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _merge(base: dict, over: Mapping, origins: dict, label: str, prefix: str = "") -> dict:
    """Merge ``over`` into ``base`` table by table; arrays and scalars replace.
    ``origins`` maps each leaf key (dotted) to the label of the layer that set it."""
    for k, v in over.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            if not base[k] and v:
                origins.pop(key, None)  # an empty default table is no longer a leaf
            _merge(base[k], v, origins, label, key + ".")
        else:
            for old in [o for o in origins if o == key or o.startswith(key + ".")]:
                del origins[old]
            base[k] = copy.deepcopy(v)
            for leaf in _leaves(v, key):
                origins[leaf] = label
    return base


def _leaves(v: Any, key: str) -> list[str]:
    if isinstance(v, dict) and v:
        out: list[str] = []
        for k, x in v.items():
            out.extend(_leaves(x, f"{key}.{k}"))
        return out
    return [key]


def defaults(*, with_style: bool = True) -> tuple[dict, dict]:
    """Built-in defaults and the origin of each leaf key."""
    origins: dict[str, str] = {}
    base: dict = {}
    _merge(base, read_toml(DEFAULTS_FILE), origins, "default")
    if with_style and STYLE_DEFAULTS_FILE.is_file():
        _merge(base, read_toml(STYLE_DEFAULTS_FILE), origins, "style default")
    return base, origins


# --------------------------------------------------------------------------- validate

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _type_ok(v: Any, t: str) -> bool:
    if isinstance(t, list):
        return any(_type_ok(v, x) for x in t)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def _tname(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    return {dict: "table", list: "array", str: "string", float: "float"}.get(type(v), type(v).__name__)


def _check_regex(value: str, mode: Any, where: str, errors: list[str]) -> None:
    pat = value
    if mode == "template":
        if "{num}" not in value:
            errors.append(f"{where}: must contain {{num}} where the week number goes")
            return
        pat = value.replace("{num}", re.escape("1"))
    if not pat:
        return
    try:
        rx = re.compile(pat)
    except re.error as exc:
        errors.append(f"{where}: not a valid regular expression: {exc}")
        return
    if mode == "week" and rx.groups not in (1, 2):
        errors.append(f"{where}: needs 1 capture group (week) or 2 (week, session); has {rx.groups}")


def _validate(v: Any, sch: dict, where: str, errors: list[str]) -> None:
    shown = "value hidden" if sch.get("x-redact") else repr(v)
    t = sch.get("type")
    if t and not _type_ok(v, t):
        want = {"object": "table", "array": "array"}.get(t, t) if isinstance(t, str) else str(t)
        errors.append(f"{where}: expected {want}, got {_tname(v)} {shown}"[:300])
        return
    if "enum" in sch and v not in sch["enum"]:
        errors.append(f"{where}: {shown} is not one of {', '.join(map(repr, sch['enum']))}")
    if "const" in sch and v != sch["const"]:
        errors.append(f"{where}: must be {sch['const']!r}")
    if isinstance(v, str):
        if len(v) < sch.get("minLength", 0):
            errors.append(f"{where}: must not be empty")
        if "pattern" in sch and not re.search(sch["pattern"], v):
            errors.append(f"{where}: {shown} does not match {sch['pattern']}")
        if sch.get("x-regex"):
            _check_regex(v, sch["x-regex"], where, errors)
    if isinstance(v, int) and not isinstance(v, bool):
        if "minimum" in sch and v < sch["minimum"]:
            errors.append(f"{where}: {v} is below the minimum {sch['minimum']}")
        if "maximum" in sch and v > sch["maximum"]:
            errors.append(f"{where}: {v} is above the maximum {sch['maximum']}")
    if isinstance(v, list):
        if len(v) < sch.get("minItems", 0):
            errors.append(f"{where}: needs at least {sch['minItems']} item(s)")
        if "maxItems" in sch and len(v) > sch["maxItems"]:
            errors.append(f"{where}: allows at most {sch['maxItems']} item(s)")
        if "items" in sch:
            for i, x in enumerate(v):
                _validate(x, sch["items"], f"{where}[{i}]", errors)
        if sch.get("x-unique"):
            k = sch["x-unique"]
            seen = [x.get(k) for x in v if isinstance(x, dict)]
            dup = sorted({repr(s) for s in seen if seen.count(s) > 1})
            if dup:
                errors.append(f"{where}: {k} must be unique; repeated: {', '.join(dup)}")
    if isinstance(v, dict):
        for r in sch.get("required", []):
            if r not in v:
                errors.append(f"{where}.{r}: required key is missing")
        props = sch.get("properties", {})
        extra = sch.get("additionalProperties", True)
        for k, x in v.items():
            sub = f"{where}.{k}" if where else k
            if k in props:
                _validate(x, props[k], sub, errors)
            elif extra is False:
                near = difflib.get_close_matches(k, list(props), n=1)
                hint = f" (did you mean {near[0]!r}?)" if near else ""
                errors.append(f"{sub}: unknown key{hint}")
            elif isinstance(extra, dict):
                _validate(x, extra, sub, errors)


def _semantic(doc: dict, errors: list[str], warnings: list[str]) -> None:
    browser = doc.get("browser") or {}
    url = browser.get("devtools_url", "")
    folder = browser.get("chromium_user_data_dir", "")
    if url:
        match = re.fullmatch(r"http://(127\.0\.0\.1|\[::1\]):([0-9]{1,5})/?", url)
        if (not match or re.search(r"[\x00-\x1f\x7f-\x9f]", url)
                or not 1 <= int(match[2]) <= 65535):
            errors.append("browser.devtools_url: value hidden; use a loopback http address with port 1 to 65535")
    if re.search(r'[\x00-\x1f\x7f-\x9f"%!]', folder):
        errors.append("browser.chromium_user_data_dir: unsupported folder characters")
    if browser.get("chromium") == "other" and not url and not folder:
        errors.append("browser.chromium_user_data_dir: required for browser.chromium other")
    if url and folder:
        warnings.append("browser.chromium_user_data_dir is ignored when browser.devtools_url is set")
    for i, c in enumerate(doc.get("text_cleaners") or []):
        if not isinstance(c, dict):
            continue
        argv = " ".join(a for a in c.get("argv", []) if isinstance(a, str))
        stage = c.get("stage")
        if stage == "pdf" and "{pdf}" not in argv:
            errors.append(f"text_cleaners[{i}].argv: a pdf-stage cleaner needs {{pdf}}")
        if stage in ("fill-text", "diagram") and not ("{in}" in argv and "{out}" in argv):
            errors.append(f"text_cleaners[{i}].argv: a {stage}-stage cleaner needs {{in}} and {{out}}")
    out = (doc.get("export") or {}).get("outputs") or {}
    if out.get("whiteboard") and not out.get("notes_graph"):
        warnings.append("export.outputs.whiteboard needs export.outputs.notes_graph = true; "
                        "no whiteboard is written")
    if out.get("notes_graph") and not (doc.get("notes") or {}).get("graph_path"):
        warnings.append("export.outputs.notes_graph is on but notes.graph_path is empty; "
                        "no notes page is written")
    rat_cfg = doc.get("rat") or {}
    if not rat_cfg.get("answers_dir") and not rat_cfg.get("answers_dirs"):
        for kind in ("irat", "trat"):
            if rat_cfg.get(kind) == "from_dir":
                warnings.append(f'rat.{kind} = "from_dir" but rat.answers_dir is empty; RAT pages are asked')


def check(doc: dict) -> tuple[list[str], list[str]]:
    """Validate a merged config document. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    sv = doc.get("schema_version", SCHEMA_VERSION)
    if isinstance(sv, int) and not isinstance(sv, bool) and sv > SCHEMA_VERSION:
        return [f"schema_version {sv} is newer than this lt-suite reads ({SCHEMA_VERSION}); "
                f"update lt-suite"], []
    _validate(doc, schema(), "", errors)
    if not errors:
        _semantic(doc, errors, warnings)
    return errors, warnings


def _fill_item_defaults(doc: dict, sch: dict) -> None:
    """Give array-of-table entries the defaults their item schema declares."""
    for k, s in sch.get("properties", {}).items():
        v = doc.get(k)
        if isinstance(v, dict):
            _fill_item_defaults(v, s)
        elif isinstance(v, list) and isinstance(s.get("items"), dict):
            props = s["items"].get("properties", {})
            for entry in v:
                if isinstance(entry, dict):
                    for pk, ps in props.items():
                        if pk not in entry and "default" in ps:
                            entry[pk] = copy.deepcopy(ps["default"])


# --------------------------------------------------------------------------- resolve

def _resolve_paths(doc: dict, sch: dict, base: Path, curriculum_root: str) -> None:
    for k, s in sch.get("properties", {}).items():
        if k not in doc:
            continue
        v = doc[k]
        if isinstance(v, dict) and s.get("type") == "object":
            _resolve_paths(v, s, base, curriculum_root)
        elif isinstance(v, list) and s.get("x-path"):
            doc[k] = [paths.expand(x, base) if isinstance(x, str) and x else x for x in v]
        elif isinstance(v, str) and v:
            mode = s.get("x-path")
            if mode == "config":
                doc[k] = paths.expand(v, base)
            elif mode == "curriculum":
                doc[k] = paths.expand(v, curriculum_root or base)


class Config:
    """The effective config: merged values with paths resolved, plus where each came from."""

    def __init__(self, values: dict, *, raw: dict, location: Location,
                 origins: dict, warnings: list[str], errors: list[str] | None = None):
        self.values = values
        self.raw = raw
        self.location = location
        self.path = location.path if location.exists else None
        self.source = location.source
        self.origins = origins
        self.warnings = warnings
        self.errors = errors or []

    def get(self, key: str, default: Any = _MISSING) -> Any:
        node: Any = self.values
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is _MISSING:
                    raise KeyError(key)
                return default
            node = node[part]
        return node

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        try:
            self.get(key)
        except KeyError:
            return False
        return True

    def where(self, key: str) -> str:
        """Origin of a key: default, style default or config <path>."""
        if key in self.origins:
            return self.origins[key]
        hits = {v for k, v in self.origins.items() if k.startswith(key + ".")}
        return hits.pop() if len(hits) == 1 else ("mixed" if hits else "unknown")

    def setting_hint(self) -> str:
        return SETUP_HINT.format(path=self.path or self.location.target)

    def require(self, key: str) -> Any:
        """Return a setting that has no usable default, or exit 3 naming the file to edit."""
        v = self.get(key, None)
        if is_unset(v):
            print(f"{key} is not set. {self.setting_hint()}", file=sys.stderr)
            raise SystemExit(3)
        return v

    def as_dict(self) -> dict:
        return copy.deepcopy(self.values)

    def digest(self) -> str:
        """sha256 of the effective config, for run.json config_digest."""
        blob = json.dumps(self.values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_toml(self) -> str:
        return tomlw.dumps(self.values)


def _rat_roots_check(doc, errors, warnings):
    # Import lazily so config remains usable before consumers load RAT helpers.
    from ltcore import rat
    policy = doc.get("rat") or {}
    pairs = [("rat.answers_dir", policy.get("answers_dir"))]
    pairs += [(f"rat.answers_dirs[{i}]", p) for i, p in enumerate(policy.get("answers_dirs") or [])]
    seen = []
    for key, value in pairs:
        if not value:
            continue
        root = Path(value).resolve()
        if root.exists() and not root.is_dir():
            errors.append(f"{key}: root must be a directory")
        if not root.exists():
            warnings.append(f"{key}: root does not exist")
        if any(rat.within(root, p) and rat.within(p, root) for p in seen):
            warnings.append(f"{key}: duplicate real root")
        seen.append(root)
        for label, other, symmetric in (
            ("notes.graph_path", (doc.get("notes") or {}).get("graph_path"), True),
            ("curriculum.root", (doc.get("curriculum") or {}).get("root"), False),
            ("paths.data_dir", os.environ.get(ENV_DATA) or (doc.get("paths") or {}).get("data_dir") or paths.data_dir(), True),
        ):
            if other and (rat.within(other, root) or (symmetric and rat.within(root, other))):
                errors.append(f"{key}: root overlaps {label}")


def build(user: dict, location: Location, *, validate: bool = True,
          cwd: str | os.PathLike | None = None) -> Config:
    """Merge ``user`` over the defaults, validate, resolve paths."""
    merged, origins = defaults()
    label = f"config {location.path}" if location.path else "config"
    _merge(merged, user, origins, label)
    sch = schema()
    _fill_item_defaults(merged, sch)
    errors, warnings = check(merged)
    if errors and validate:
        where = location.path if location.path else "built-in defaults"
        raise ConfigError(f"{where}: {len(errors)} problem(s)\n  " + "\n  ".join(errors))
    base = location.path.parent if location.path else (Path.cwd() if cwd is None else Path(cwd))
    cur = merged.get("curriculum")
    croot = cur.get("root", "") if isinstance(cur, dict) else ""
    croot = paths.expand(croot, base) if isinstance(croot, str) and croot else ""
    _resolve_paths(merged, sch, base, croot)
    if not errors:
        _rat_roots_check(merged, errors, warnings)
    if errors and validate:
        raise ConfigError("invalid RAT roots: " + "; ".join(errors))
    return Config(merged, raw=user, location=location, origins=origins, warnings=warnings,
                  errors=errors)


def load(path: str | os.PathLike | None = None, *, environ: Mapping[str, str] | None = None,
         cwd: str | os.PathLike | None = None, validate: bool = True) -> Config:
    """Locate, read, merge and validate the config. Raises ConfigError on any problem."""
    loc = locate(path, environ=environ, cwd=cwd)
    user: dict = {}
    if loc.path is not None:
        if not loc.path.is_file():
            raise ConfigError(f"config file not found: {loc.path} ({loc.source})")
        user = read_toml(loc.path)
    return build(user, loc, validate=validate, cwd=cwd)


def no_install(environ: Mapping[str, str] | None = None) -> bool:
    """True when LT_SUITE_NO_INSTALL=1 forbids installing into the notes graph."""
    env = os.environ if environ is None else environ
    return env.get(ENV_NO_INSTALL, "") == "1"


# --------------------------------------------------------------------------- write

HEADER = ["lt-suite config, written by ltcore.config.",
          "Every key and its default: docs/configuration.md"]


def render(doc: dict) -> str:
    """TOML text for ``doc``; checks that it reads back unchanged."""
    text = tomlw.dumps(doc, header=HEADER)
    if tomllib.loads(text) != doc:
        raise ConfigError("internal error: the TOML writer did not round-trip this config")
    return text


@contextmanager
def _write_lock(target: Path):
    """Serialize cooperating writers across processes, including backup creation."""
    lock = target.with_name(f".{target.name}.lock")
    deadline = time.monotonic() + 10
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ConfigError(f"{target}: another writer holds {lock}") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.rmdir()


def write(doc: dict, path: str | os.PathLike | None = None, *, force: bool = False,
          environ: Mapping[str, str] | None = None, cwd: str | os.PathLike | None = None) -> Path:
    """Validate ``doc`` and write it atomically. Refuses to replace a file unless
    ``force``; a replaced file is kept as ``<name>.bak``. Returns the path written."""
    loc = locate(path, environ=environ, cwd=cwd)
    target = loc.target
    build(doc, Location(target, loc.source, target, loc.target_source), cwd=cwd)
    text = render(doc)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock(target):
        fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                # Publish a complete file exclusively; a late creator always wins.
                os.link(tmp, target)
            except FileExistsError:
                if not force:
                    raise ConfigError(f"{target} exists ({loc.target_source}); pass --force "
                                      f"to replace it (the old file is kept as {target.name}.bak)") from None
                shutil.copy2(target, target.with_name(target.name + ".bak"))
                os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return target


# --------------------------------------------------------------------------- CLI

def _read_answers(src: str) -> dict:
    if src == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(src).read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"{src}: cannot read: {exc.strerror or exc}") from None
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        try:
            doc = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{src}: neither JSON nor TOML: {exc}") from None
    if not isinstance(doc, dict):
        raise ConfigError(f"{src}: the top level must be a table")
    return doc


def _fmt(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="config file (else LT_SUITE_CONFIG, ./lt-suite.toml, "
                        "the platform file)")
    ap = argparse.ArgumentParser(prog="python3 -m ltcore.config", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", dest="config_top", help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("show", parents=[common], help="print the effective config as TOML")
    p.add_argument("--origins", action="store_true", help="print each key with its origin")
    sub.add_parser("path", parents=[common], help="print the config path and how it was found")
    p = sub.add_parser("get", parents=[common], help="print one key")
    p.add_argument("key")
    sub.add_parser("validate", parents=[common], help="check the config against the schema")
    sub.add_parser("locate", parents=[common], help="print the locate report as JSON")
    p = sub.add_parser("write", parents=[common], help="write a config from a TOML or JSON file")
    p.add_argument("--from", dest="src", required=True, help="TOML or JSON file, - for stdin")
    p.add_argument("--force", action="store_true", help="replace an existing file (keeps .bak)")
    p.add_argument("--stdout", action="store_true", help="print the TOML instead of writing it")
    a = ap.parse_args(argv)
    flag = getattr(a, "config", None) or a.config_top

    if a.cmd == "locate":
        print(json.dumps(locate_report(flag), indent=1))
        return 0
    if a.cmd == "path":
        loc = locate(flag)
        if loc.path is None:
            print(f"{loc.target}  (no config file; setup would write here: {loc.target_source})")
            return 0
        print(f"{loc.path}  ({loc.source}, {'exists' if loc.exists else 'missing'})")
        return 0 if loc.exists else 3
    if a.cmd == "write":
        try:
            doc = _read_answers(a.src)
            if a.stdout:
                loc = locate(flag)
                build(doc, Location(loc.target, loc.source, loc.target, loc.target_source))
                sys.stdout.write(render(doc))
                return 0
            out = write(doc, flag, force=a.force)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"wrote {out}")
        return 0

    loc = locate(flag)
    if loc.path is not None and not loc.exists:
        print(f"error: config file not found: {loc.path} ({loc.source})", file=sys.stderr)
        return 3
    try:
        cfg = load(flag, validate=(a.cmd != "validate"))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if a.cmd == "validate":
        name = cfg.path or "built-in defaults (no config file)"
        for w in cfg.warnings:
            print(f"WARN {w}")
        if cfg.errors:
            print(f"INVALID {name}")
            for e in cfg.errors:
                print(f"  {e}")
            return 1
        print(f"OK {name} ({cfg.source})")
        return 0
    for w in cfg.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if a.cmd == "show":
        if a.origins:
            for key in sorted(cfg.origins):
                print(f"{key} = {json.dumps(cfg.get(key, None), ensure_ascii=False)}  # {cfg.origins[key]}")
        else:
            sys.stdout.write(cfg.to_toml())
        return 0
    if a.cmd == "get":
        try:
            v = cfg.get(a.key)
        except KeyError:
            print(f"error: unknown key {a.key}", file=sys.stderr)
            return 1
        if is_unset(v):
            print(f"{a.key} is not set. {cfg.setting_hint()}", file=sys.stderr)
            return 3
        print(_fmt(v))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
