# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load a case folder for the engine: case.json, content.py, layout.py and artifact_content.py.

A case folder holds only what is particular to one diagram:

    case.json            names and header strings: slug (deliverable names) and title; optional subtitle,
                         group, members, aria_label, diagram_name, key, verify_probe (the config's
                         diagram.header.required_fields makes any of them required)
    content.py           nodes, edges, stage titles (CLUSTERS), bind() rules, ITALIC terms, PAIRS
    layout.py            grid data: POS, SPAN, KEY_SPAN, GROUPS, HINT, W_MAX ... (no code)
    artifact_content.py  the Artifact page's words: lede, figcaption, findings, sources

preflight() lists everything that stops a build, so a fresh scaffold fails with a message, not a
traceback.

A case folder is code: the engine imports content.py, layout.py, artifact_content.py, variants.py and
layout_<variant>.py. Case() trusts only the lt-suite data folder and this skill's examples/ by location. Other
folders need trust=True (the scripts' --trust-case flag), which records every Python file's sha256.
Changing any Python source requires fresh approval.

Variants: a smaller picture of the same case (a "core" page of the key steps), or any other cut, comes
from the same content.py:

    variants.py          per variant, the boxes it keeps (CORE = ids, or CORE_DROP = ids it drops, or a
                         function (id, node) -> keep), optional BRIDGES (edges to add where a dropped box
                         would break a chain) and META (case.json fields to change, e.g. the subtitle)
    layout_<variant>.py  the variant's own grid, the same fields as layout.py (autoplace.py drafts one)

Case(case_dir, variant="core") loads the full content.py, keeps the variant's boxes, drops every edge
with a dropped end, adds the variant's BRIDGES, drops the stages left empty and the RULED OUT pairs
with a dropped half, and reads layout_core.py. Everything downstream (layout_engine, render, drawio)
sees only the variant. Its slug is "<slug>-<variant>" and its draw.io page "<diagram_name> (<variant>)"
unless META says otherwise. variant_build.py builds a variant's outputs under their own names.
"""
import hashlib, importlib.util, json, pathlib, re, sys, tempfile, types

import _bootstrap  # noqa: F401  (engine and shared core on sys.path)
import core as E
import themes

ENGINE = pathlib.Path(__file__).resolve().parent
EXAMPLES = ENGINE.parent / "examples"
EXAMPLE = EXAMPLES / "toy-case"                           # the worked example (diagram.example_case overrides it)
PLACEHOLDER = "TODO"                                      # new_case.py marks every value to fill in with this
META_KEYS = ("slug", "title", "subtitle", "group", "members", "aria_label", "diagram_name")
ALWAYS_REQUIRED = ("slug", "title")
CODE_FILES = ("content.py", "layout.py", "artifact_content.py", "variants.py")


def example_case():
    """The worked example folder: diagram.example_case, else examples/toy-case beside the engine."""
    try:
        import style
        v = style.get("diagram.example_case")
    except Exception:  # noqa: BLE001
        v = None
    return pathlib.Path(v) if v else EXAMPLE


def header_rules():
    """(required case.json keys, members order) from diagram.header.*."""
    try:
        import style
        req = list(style.get("diagram.header.required_fields", ["title"]) or [])
        order = style.get("diagram.header.members_order", "as-given")
    except Exception:  # noqa: BLE001
        req, order = ["title"], "as-given"
    return tuple(dict.fromkeys(list(ALWAYS_REQUIRED) + [k for k in req if k in META_KEYS])), order


def trusted_roots():
    """Only the data folder and this skill's bundled examples trust case code by location."""
    import style
    return [EXAMPLES.resolve(), style.data_dir().resolve()]


def check_trust(d, trust=False):
    """Require explicit approval of every Python source hash outside the trusted roots."""
    import style
    d = pathlib.Path(d).resolve()
    if any(d == r or r in d.parents for r in trusted_roots()):
        return
    hashes = {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(d.rglob("*.py"))}
    state = style.data_dir() / "state" / "trusted-cases.json"
    try:
        records = json.loads(state.read_text())
        if not isinstance(records, dict):
            records = {}
    except (OSError, ValueError):
        records = {}
    if trust:
        print(f"--trust-case: running case code from {d}: " + ", ".join(hashes), file=sys.stderr)
        for name, digest in hashes.items():
            print(f"  sha256 {digest}  {name}", file=sys.stderr)
        records[str(d)] = hashes
        state.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=state.parent, delete=False) as f:
            tmp = pathlib.Path(f.name)
            json.dump(records, f, sort_keys=True, indent=2)
            f.write("\n")
        try:
            tmp.replace(state)
        finally:
            tmp.unlink(missing_ok=True)
        return
    if hashes and records.get(str(d)) == hashes:
        return
    raise CaseTrustError(f"{d} has unapproved or changed case code ({', '.join(hashes) or 'content.py, layout.py'}). "
                         "Read those files first, then run with --trust-case to record their sha256 hashes.")
VARIANT_NAME = re.compile(r"[a-z][a-z0-9_]*")             # names layout_<variant>.py and the outputs


class CaseError(Exception):
    """A case that cannot be built (yet); the message says what to fix."""


class CaseTrustError(CaseError):
    """Case code needs explicit approval before execution."""


def _load(path, modname):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True     # no __pycache__ in case folders
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = keep
    return mod


def surname(name):
    return name.split()[-1]


# a case folder's outputs (everything else is an input: content, case.json, layout files, assets ...)
OUTPUT_PATTERNS = ("final-*", "geometry*.json", "*.drawio", "*.html", "deliver", ".font-cache", "__pycache__",
                   "*.static.svg")


def scratch_copy(case_dir, dest):
    """Copy a case folder's inputs (not its outputs) to dest, keeping relative paths such as an inset's assets/
    image, so a tool can run the engine there without touching the case. Returns dest."""
    import shutil
    shutil.copytree(case_dir, dest, ignore=shutil.ignore_patterns(*OUTPUT_PATTERNS))
    return pathlib.Path(dest)


def _empty_layout():
    """A layout.py with nothing filled in (what new_case.py writes): for tools that only need content."""
    return types.SimpleNamespace(POS={}, SPAN={}, KEY_SPAN=None, GROUPS=[], GROUPS_ABOVE_ROW=None, HINT={}, W_MAX={})


class Case:
    def __init__(self, case_dir, variant=None, need_layout=True, need_meta=True, trust=False):
        """variant: None for the full case, else a name in variants.py ("core", "detail" ...).
        need_layout=False: a missing layout file loads as an empty one (autoplace.py needs content only).
        need_meta=False: a missing case.json loads as {} (layout runs need no names).
        trust=True: run the case's Python wherever the folder is (--trust-case)."""
        d = pathlib.Path(case_dir).resolve()
        if not d.is_dir():
            raise CaseError(f"{d} is not a folder")
        check_trust(d, trust)
        if (d / "case.json").exists():
            try:
                self.meta = json.loads((d / "case.json").read_text())
            except json.JSONDecodeError as ex:
                raise CaseError(f"{d / 'case.json'} is not valid JSON: {ex}")
        elif need_meta:
            raise CaseError(f"no case.json in {d}: start a case with  python3 {ENGINE / 'new_case.py'} <dir>")
        else:
            self.meta = {}
        if not (d / "content.py").exists():
            raise CaseError(f"no content.py in {d}: see {example_case() / 'content.py'} for the worked example")
        if variant is not None and not VARIANT_NAME.fullmatch(variant):
            raise CaseError(f"variant {variant!r}: use lower-case letters, digits and _ (it names layout_<variant>.py)")
        self.dir = d
        self.variant = variant
        self.layout_file = "layout.py" if variant is None else f"layout_{variant}.py"
        self.full_content = _load(d / "content.py", "case_content")
        self.bridges, self.variant_problems, self.variant_notes = [], [], []
        if variant is None:
            self.content = self.full_content
        else:
            if not (d / "variants.py").exists():
                raise CaseError(f"no variants.py in {d}: a variant needs one ({variant.upper()} = the node ids it keeps)")
            self.variants = _load(d / "variants.py", "case_variants")
            self.content = self._variant_content(variant)
            self.meta = self._variant_meta(variant)
        lp = d / self.layout_file
        if lp.exists():
            self.layout = _load(lp, "case_layout" if variant is None else f"case_layout_{variant}")
        elif need_layout:
            hint = (f"see {example_case() / 'layout.py'} for the worked example" if variant is None else "")
            raise CaseError(f"no {self.layout_file} in {d}: draft one with  python3 {ENGINE / 'autoplace.py'} {d}"
                            + (f" --variant {variant}" if variant else "") + f" --out {self.layout_file}"
                            + (f"; {hint}" if hint else ""))
        else:
            self.layout = _empty_layout()
        ap = d / "artifact_content.py"
        self.artifact = _load(ap, "case_artifact") if ap.exists() and variant is None else None
        E.set_italic(getattr(self.content, "ITALIC", ()))

    # ------------------------------------------------------------------ variants
    def _variant_keep(self, variant):
        """The node ids the variant keeps, from variants.py: <VARIANT> (ids or a function) or <VARIANT>_DROP."""
        V, nodes = self.variants, self.full_content.NODES
        name = variant.upper()
        keep_spec, drop_spec = getattr(V, name, None), getattr(V, name + "_DROP", None)
        if keep_spec is None and drop_spec is None:
            raise CaseError(f"variants.py has no {name} (the node ids variant {variant!r} keeps) and no {name}_DROP")
        if keep_spec is not None and drop_spec is not None:
            raise CaseError(f"variants.py gives both {name} and {name}_DROP: keep one")
        if callable(keep_spec):
            return {nid for nid, nd in nodes.items() if keep_spec(nid, nd)}, set()
        ids = set(keep_spec if keep_spec is not None else drop_spec)
        unknown = sorted(ids - set(nodes))
        keep = ids & set(nodes) if keep_spec is not None else set(nodes) - ids
        return keep, unknown

    def _variant_content(self, variant):
        full = self.full_content
        keep, unknown = self._variant_keep(variant)
        name = variant.upper()
        for nid in unknown:
            self.variant_problems.append(f"variants.py: {name} names {nid!r}, which is not a node in content.py")
        mod = types.ModuleType(f"case_content_{variant}")
        for k, v in vars(full).items():           # ITALIC, ROLE_NAME, _BIND ... pass through unchanged
            if not k.startswith("__"):
                setattr(mod, k, v)
        nodes = {nid: dict(nd) for nid, nd in full.NODES.items() if nid in keep}
        edges = [dict(ed) for ed in full.EDGES if ed["a"] in keep and ed["b"] in keep]
        have = {(ed["a"], ed["b"]) for ed in edges}
        raw = getattr(self.variants, "BRIDGES", {})
        raw = raw.get(variant, []) if isinstance(raw, dict) else list(raw)
        for br in raw:
            if not isinstance(br, (tuple, list)) or len(br) not in (2, 3):
                self.variant_problems.append(f"variants.py: bridge {br!r} must be (a, b) or (a, b, kind)")
                continue
            a, b, kind = (tuple(br) + ("stim",))[:3]
            if kind not in themes.EDGE_KINDS:
                self.variant_problems.append(f"variants.py: bridge {a}->{b}: kind {kind!r} is not one of {', '.join(themes.EDGE_KINDS)}")
                continue
            missing = [x for x in (a, b) if x not in full.NODES]
            if missing:
                self.variant_problems.append(f"variants.py: bridge {a}->{b} names {', '.join(map(repr, missing))}, not in content.py")
                continue
            if a not in keep or b not in keep:
                if isinstance(getattr(self.variants, "BRIDGES", {}), dict):   # named for this variant: say so
                    self.variant_notes.append(f"bridge {a}->{b} skipped: {a if a not in keep else b} is not in {variant}")
                continue
            if (a, b) in have:
                self.variant_notes.append(f"bridge {a}->{b} skipped: the edge is already there")
                continue
            edges.append(dict(a=a, b=b, kind=kind, label=None))
            have.add((a, b))
            self.bridges.append((a, b, kind))
        mod.NODES, mod.EDGES = nodes, edges
        mod.CLUSTERS = {cid: t for cid, t in full.CLUSTERS.items() if any(nd["cluster"] == cid for nd in nodes.values())}
        pairs = list(getattr(full, "PAIRS", []))
        mod.PAIRS = [p for p in pairs if p[0] in keep and p[3] in keep]
        for p in pairs:
            if (p[0] in keep) != (p[3] in keep):
                gone, kept = (p[0], p[3]) if p[3] in keep else (p[3], p[0])
                self.variant_notes.append(f"RULED OUT pair {p[0]}/{p[3]}: {gone} is dropped, so {kept} stands alone; "
                                          f"drop both or keep both")
        self.variant_notes += self._broken_chains(keep, have)
        return mod

    def _broken_chains(self, keep, have):
        """Chains a dropped box breaks: a kept cause that reached a kept effect only through dropped boxes,
        with no edge between them in the variant. Each is a BRIDGES candidate (a note, not a stop)."""
        full = self.full_content
        succ = {}
        for ed in full.EDGES:
            succ.setdefault(ed["a"], []).append(ed)
        notes = []
        for a in [n for n in full.NODES if n in keep]:
            for ed in succ.get(a, []):
                if ed["b"] in keep:
                    continue
                seen, stack = set(), [ed["b"]]
                while stack:                   # walk on through dropped boxes only
                    x = stack.pop()
                    if x in seen:
                        continue
                    seen.add(x)
                    for nxt in succ.get(x, []):
                        if nxt["b"] in keep:
                            if nxt["b"] != a and (a, nxt["b"]) not in have:
                                note = (f"chain broken: {a} -> {ed['b']} ... -> {nxt['b']} has no box left between "
                                        f"{a} and {nxt['b']}; BRIDGES candidate ({a!r}, {nxt['b']!r}, {ed['kind']!r})")
                                if note not in notes:
                                    notes.append(note)
                        else:
                            stack.append(nxt["b"])
        before = {n for ed in full.EDGES for n in (ed["a"], ed["b"])}
        after = {n for ab in have for n in ab}
        for n in full.NODES:
            if n in keep and n in before and n not in after:
                notes.append(f"{n} keeps no arrow in this variant (all its partners are dropped)")
        return notes

    def _variant_meta(self, variant):
        meta = dict(self.meta)
        if meta.get("slug"):
            meta["slug"] = f"{meta['slug']}-{variant}"
        if meta.get("diagram_name"):
            meta["diagram_name"] = f"{meta['diagram_name']} ({variant})"
        if isinstance(meta.get("stages"), list):
            # case.json "stages" (numbers, glyphs, times) for the stages the variant keeps; the numbers close up
            # in story order (1, 2, 3 ... with no gap where a stage was dropped)
            kept = [dict(s) for s in meta["stages"] if s.get("id") in self.content.CLUSTERS]
            numbered = sorted((s for s in kept if s.get("n") is not None), key=lambda s: s["n"])
            for i, s in enumerate(numbered, 1):
                s["n"] = i
            meta["stages"] = kept
        meta.update(getattr(self.variants, "META", {}).get(variant, {}))
        return meta

    # ------------------------------------------------------------------ case.json
    slug = property(lambda self: self.meta["slug"])
    title = property(lambda self: self.meta["title"])
    subtitle = property(lambda self: self.meta.get("subtitle") or "")
    group = property(lambda self: self.meta.get("group") or "")
    members = property(lambda self: list(self.meta.get("members") or []))
    aria_label = property(lambda self: self.meta.get("aria_label") or self.meta["title"])
    diagram_name = property(lambda self: self.meta.get("diagram_name") or self.meta["title"])

    @property
    def verify_probe(self):
        """The node whose label mkverify.js inspects for the face, weight and size."""
        probe = self.meta.get("verify_probe")
        if probe in self.content.NODES:
            return probe
        return next((k for k, v in self.content.NODES.items() if not v["bold"]), next(iter(self.content.NODES)))

    # ------------------------------------------------------------------ content
    @property
    def role_name(self):
        return getattr(self.content, "ROLE_NAME", themes.ROLE_NAME)

    def key_roles(self, default):
        return list(self.meta.get("key", {}).get("roles", default))

    def key_arrows(self, default):
        return [tuple(a) for a in self.meta.get("key", {}).get("arrows", default)]

    # ------------------------------------------------------------------ checks before building
    def preflight(self, need_meta=True):
        """Everything that stops a build, as messages (need_meta=False: not case.json, for layout runs)."""
        out = list(self.variant_problems)
        C, L, lf = self.content, self.layout, self.layout_file
        nodes, edges, clusters = getattr(C, "NODES", {}), getattr(C, "EDGES", []), getattr(C, "CLUSTERS", {})
        if not nodes:
            out.append(f"content.py has no nodes yet (NODES is empty): write the case's nodes, edges and stage titles; "
                       f"the worked example is {example_case() / 'content.py'}" if self.variant is None else
                       f"variant {self.variant!r} keeps no nodes")
        if not clusters:
            out.append("content.py has no stages yet (CLUSTERS is empty): one title per stage container")
        for nid, nd in nodes.items():
            if nd["cluster"] not in clusters and clusters:
                out.append(f"node {nid}: stage {nd['cluster']!r} is not in CLUSTERS")
            if nd["role"] not in themes.ROLES:
                out.append(f"node {nid}: role {nd['role']!r} is not one of {', '.join(themes.ROLES)}")
        for ed in edges:
            for end in (ed["a"], ed["b"]):
                if end not in nodes:
                    out.append(f"edge {ed['a']}->{ed['b']}: no node {end!r}")
            if ed["kind"] not in themes.EDGE_KINDS:
                out.append(f"edge {ed['a']}->{ed['b']}: kind {ed['kind']!r} is not one of {', '.join(themes.EDGE_KINDS)}")
        pos, span = getattr(L, "POS", {}), getattr(L, "SPAN", {})
        if not pos:
            if nodes:
                out.append(f"{lf} has no positions (POS is empty): give every node a (column, row), or draft them with "
                           f"python3 {ENGINE / 'autoplace.py'} {self.dir}" + (f" --variant {self.variant}" if self.variant else "")
                           + f"; see {example_case() / 'layout.py'}")
        else:
            missing, extra = [n for n in nodes if n not in pos], [n for n in pos if n not in nodes]
            if missing:
                out.append(f"{lf}: no POS for {', '.join(missing)}")
            if extra:
                out.append(f"{lf}: POS for nodes " + ("content.py does not have" if self.variant is None else
                           f"variant {self.variant!r} does not keep") + f": {', '.join(extra)}")
        if clusters and set(span) != set(clusters):
            gone, more = [c for c in clusters if c not in span], [c for c in span if c not in clusters]
            out.append(f"{lf}: SPAN must give every stage in CLUSTERS its (first col, last col, first row, last row)"
                       + (f"; missing {', '.join(gone)}" if gone else "") + (f"; unknown {', '.join(more)}" if more else ""))
        if nodes and getattr(L, "KEY_SPAN", None) is None:
            out.append(f"{lf}: KEY_SPAN is not set: the key's (first col, last col, first row, last row) on the grid")
        edge_keys = {(ed["a"], ed["b"]) for ed in edges}
        for k in getattr(L, "HINT", {}):
            if k not in edge_keys:
                out.append(f"{lf}: HINT for {k[0]}->{k[1]}, which is not an edge in "
                           + ("content.py" if self.variant is None else f"variant {self.variant!r}"))
        if not need_meta:
            return out
        required, order = header_rules()
        todo = [k for k in META_KEYS if (k in required and (k not in self.meta or not self.meta[k]))
                or (k in self.meta and PLACEHOLDER in json.dumps(self.meta[k], ensure_ascii=False))]
        if todo:
            out.append(f"case.json still needs: {', '.join(todo)}"
                       + (". Ask who to credit for group and members, and in which order" if
                          {"group", "members"} & set(todo) else ""))
        if "slug" not in todo and self.meta.get("slug"):
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", self.slug):
                out.append(f"case.json: slug {self.slug!r} must be lower case letters, digits, dots and hyphens "
                           f"(it names the deliverables)")
        if order == "surname" and "members" not in todo and self.members != sorted(self.members, key=surname):
            out.append("case.json: members must be in surname order (diagram.header.members_order): "
                       + ", ".join(sorted(self.members, key=surname)))
        return out
