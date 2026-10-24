# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate or check 7 deterministic, entirely invented Lt module fixtures.

Run with --seed 20001, --check or --out DIR. README.md in tests/fixtures describes
coverage, ownership and the independently handwritten boundary oracle.
Exit 0 on success, 1 on drift or validation failure, 2 on invalid CLI arguments.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import struct
import sys
import tempfile
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
SHARED = REPO / "shared"

SEED_DEFAULT = 20001
TS = "2000-01-01T00:00:00Z"
DATE = "2000-01-01"
ORIGIN = "https://lt.example.invalid"
INSTANCE = "inst0001"
COURSE = 1
LESSON_ID = 1
UNIT = "X101"
COURSE_TITLE = "Example Course 2000 (X101)"
TOOL_VERSION = "0.1.0"
CONFIG_DIGEST = hashlib.sha256(b"lt-suite fixture config").hexdigest()

# Directories the generator owns. Everything in them except HAND_WRITTEN is rewritten and
# checked; a file in them that the generator did not produce is an orphan.
OWNED_DIRS = ("modules", "answers", "references", "fill-reports", "runs", "curriculum",
              "viewmodels", "media", "pages")
HAND_WRITTEN = frozenset({"modules/expected-boundaries.json"})

# module id -> (run id, file stem used in module-/answers-/reference-/fill-report- names)
RUNS = {
    "c1-r101": (101, "c1-r101"), "c1-r102": (102, "c1-r102"), "c1-r103": (103, "c1-r103"),
    "c1-r104": (104, "c1-r104"), "c1-r105": (105, "c1-r105"), "c1-r106": (106, "c1-r106"),
    "c1-r107": (107, "demo"),
}
FILL_REPORT_MODULES = ("c1-r101", "c1-r102", "c1-r103", "c1-r104", "c1-r106", "c1-r107")
RUN_RECORD_MODULES = ("c1-r101", "c1-r102", "c1-r103", "c1-r104", "c1-r106", "c1-r107")


class Rng:
    """splitmix64: the same stream on every Python version and platform."""
    M = (1 << 64) - 1

    def __init__(self, seed: int):
        self.s = seed & self.M

    def next(self) -> int:
        self.s = (self.s + 0x9E3779B97F4A7C15) & self.M
        z = self.s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & self.M
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & self.M
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        return self.next() % n

    def hash32(self) -> int:
        return self.next() & 0xFFFFFFFF

    def fork(self, label: str) -> "Rng":
        h = int(hashlib.sha256(label.encode("utf-8")).hexdigest()[:16], 16)
        return Rng(self.s ^ h)


def dumps(obj) -> bytes:
    """Fixture JSON: two-space indent would bloat the long arrays, so one space."""
    return (json.dumps(obj, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- PNG writer -------------

def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def _stored_zlib(raw: bytes) -> bytes:
    """A zlib stream of stored (uncompressed) deflate blocks. No compressor is involved, so
    the bytes are the same under any zlib build."""
    out = bytearray(b"\x78\x01")
    pos = 0
    while True:
        block = raw[pos:pos + 65535]
        pos += len(block)
        final = 1 if pos >= len(raw) else 0
        out += struct.pack("<BHH", final, len(block), len(block) ^ 0xFFFF) + block
        if final:
            break
    out += struct.pack(">I", zlib.adler32(raw) & 0xFFFFFFFF)
    return bytes(out)


PALETTE = [(250, 250, 244), (60, 90, 140), (200, 90, 70), (80, 150, 100), (220, 180, 60),
           (140, 90, 160), (60, 60, 60), (230, 150, 170)]


def make_png(rng: Rng, w: int = 64, h: int = 48) -> bytes:
    """An 8-bit paletted PNG of a few filled ellipses and a frame. IHDR, PLTE, IDAT, IEND
    only: no text chunks, no timestamp, no gamma."""
    px = [[0] * w for _ in range(h)]
    for _ in range(3 + rng.below(3)):
        cx, cy = 8 + rng.below(w - 16), 8 + rng.below(h - 16)
        rx, ry = 5 + rng.below(9), 4 + rng.below(7)
        colour = 1 + rng.below(len(PALETTE) - 2)
        for y in range(max(0, cy - ry), min(h, cy + ry + 1)):
            for x in range(max(0, cx - rx), min(w, cx + rx + 1)):
                if ((x - cx) * ry) ** 2 + ((y - cy) * rx) ** 2 <= (rx * ry) ** 2:
                    px[y][x] = colour
    for x in range(w):
        px[0][x] = px[h - 1][x] = 6
    for y in range(h):
        px[y][0] = px[y][w - 1] = 6
    raw = b"".join(b"\x00" + bytes(row) for row in px)
    plte = b"".join(bytes(c) for c in PALETTE)
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
           + _chunk(b"PLTE", plte)
           + _chunk(b"IDAT", _stored_zlib(raw))
           + _chunk(b"IEND", b""))
    assert len(png) < 5000, len(png)
    return png


# ---------------------------------------------------------------- text helpers -----------

def S(h) -> str:
    """capture.js S(): authored HTML to plain text with block boundaries as newlines."""
    s = "" if h is None else str(h)
    s = re.sub(r"<\s*br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<\s*li[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"<\s*/\s*(p|div|li|ul|ol|h[1-6]|tr|table|blockquote)\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
    s = re.sub(r"&#39;|&rsquo;", "'", s).replace("&quot;", '"').replace("&#43;", "+")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ---------------------------------------------------------------- view-model specs -------
# A spec is the Lt view model as plain JSON, in Lt's own field names: a Backbone collection
# is a JSON array, a model is a plain object, an observable is its value. tests/capture
# serves it to capture.js as is and again wrapped in observables. The constructors below
# build the panel shapes capture.js reads; nothing here knows about module.json.

def panel(plugin, key, content, input=None, ro=None):
    st = {"content": content}
    if input is not None:
        st["input"] = input          # an answerable panel is one whose state carries `input`
    if ro is not None:
        st["isReadOnly"] = ro
    return {"pluginKey": plugin, "panelKey": str(key), "panelState": st}


def _common(single=False, assess="noAssessment", grade=None):
    d = {"singleAttempt": single, "assessmentType": assess}
    if grade is not None:
        d["maximumGrade"] = grade
    return d


def _inp(d, locked):
    if locked:
        d["locked"] = True
    return d


def text_q(key, question, answer="", *, single=False, assess="noAssessment", grade=None,
           ro=None, locked=False):
    content = {"question": question, **_common(single, assess, grade)}
    return panel("text-question", key, content, _inp({"answer": answer}, locked), ro)


def mcq(key, question, options, selected=(), *, multi=False, single=False,
        assess="noAssessment", grade=None, ro=None, locked=False):
    content = {"question": question, "answers": [{"answer": o} for o in options],
               "multiselect": multi, **_common(single, assess, grade)}
    return panel("select", key, content, _inp({"selectedAnswers": list(selected)}, locked), ro)


def row(*cells, header=False):
    """A table row. A plain string is a body cell; ("text",) marks a header column cell."""
    out = [{"content": c[0], "headerColumn": True} if isinstance(c, tuple) else {"content": c}
           for c in cells]
    return {"headerRow": header, "cells": out}


def table_q(key, question, student, author=None, *, single=False, assess="noAssessment",
            grade=None, ro=None, locked=False):
    content = {"question": question}
    if author is not None:
        content["rows"] = author
    content.update(_common(single, assess, grade))
    return panel("table-question", key, content, _inp({"rows": student}, locked), ro)


UPLOAD = "allowImageAndAnnotation"
ANNOTATE = "allowAnnotation"


def annot_q(key, question, mode, *, author_key=None, student_key=None, ratio=0,
            annotations=(), alt=None, ann_key=None, single=False, assess="noAssessment",
            grade=None, ro=None, locked=False):
    content = {}
    if question is not None:
        content["questionText"] = question
    content["inputMode"] = mode
    if author_key:
        content["predefinedImageKey"] = author_key
    if alt:
        content["altTextDescription"] = alt
    if mode == ANNOTATE:
        content["presentedRatio"] = ratio
    content.update(_common(single, assess, grade))
    inp = {"annotations": list(annotations)}
    if student_key:
        inp["imageKey"] = student_key
    if ann_key:
        inp["annotationsKey"] = ann_key
    if mode == UPLOAD:
        inp["presentedRatio"] = ratio
    return panel("image-annotation", key, content, _inp(inp, locked), ro)


def label_q(plugin, key, question, labels, locations, links=(), *, image_key, ratio,
            alt=None, single=False, assess="noAssessment", grade=None, ro=None, locked=False):
    """plugin: kcImageLabelling or drag-and-drop. locations: (x, y) proportions of the
    image; links: (label index, location index) pairs the student has made."""
    content = {"question": question,
               "labelItems": [{"label": lb} for lb in labels],
               "labelLocationItems": [{"dropZoneLeftProportion": x, "dropZoneTopProportion": y,
                                       "anchorX": x, "anchorY": y} for x, y in locations],
               "presentedRatio": ratio, "imageResourceKey": image_key}
    if alt:
        content["altTextDescription"] = alt
    content.update(_common(single, assess, grade))
    inp = {"linkedLabels": [{"locationIndex": loc, "labelIndex": lab} for lab, loc in links]}
    return panel(plugin, key, content, _inp(inp, locked), ro)


def cat_q(key, question, cats, labels, placed=None, *, single=False, assess="noAssessment",
          grade=None, ro=None, locked=False):
    """placed: {category index: [label indexes]}, or None for the empty-collection shape."""
    content = {"question": question, "categoryItems": [{"title": c} for c in cats],
               "labelItems": [{"label": lb} for lb in labels], **_common(single, assess, grade)}
    items = []
    if placed is not None:
        items = [{"labelAnswerItems": [{"labelIndex": i} for i in placed.get(ci, [])]}
                 for ci in range(len(cats))]
    return panel("categories", key, content, _inp({"categoryAnswerItems": items}, locked), ro)


def subpage(key, *children):
    """A container. Its children sit at content.subpages[].rows[].cells[].panels[] and are
    raw Backbone models: the same fields, with no per-panel `isReadOnly`."""
    return panel("subpage", key, {"subpages": [{"rows": [{"cells": [{"panels": list(children)}]}]}]})


def unmapped_q(plugin, key, question, input, ro=None):
    return panel(plugin, key, {"question": question}, input, ro)


def notebook(key):
    return panel("notebook", key, {"question": "Your notes"}, {"notes": ""})


def ctext(key, html):
    return panel("kcTextArea", key, {"theText": html})


def cimage(key, image_key, caption, alt):
    return panel("image", key, {"imageResourceKey": image_key, "caption": caption,
                                "altTextDescription": alt})


def ctable(key, rows):
    return panel("table", key, {"rows": rows})


def cplain(key, plugin, content=None):
    return panel(plugin, key, content or {})


def spot(sid, parent, kind, colour, title, desc, image_key, points):
    return {"id": sid, "parentHotspot": parent, "hotspotType": kind, "hotspotColour": colour,
            "labelTitle": title, "labelDescription": desc, "imageResourceKey": image_key,
            "position": json.dumps({"points": [{"x": x, "y": y} for x, y in points]},
                                   separators=(",", ":"))}


def chotspot(key, spots):
    return panel("hotspot", key, {"hotspots": spots})


def content_page(title, *panels):
    return {"title": title, "allPanels": list(panels), "rows": []}


def report_page(title="Report"):
    return {"title": title, "reportInfoPanels": []}


def checkpoint_page(index, committed, title="Checkpoint"):
    return {"title": title, "index": index, "committed": committed, "commitLoading": False}


def module_spec(mid, title, pages, *, completion, locked=False, show_remaining=False,
                remaining=0, container=None):
    run = RUNS[mid][0]
    return {
        "module_id": mid,
        "location": {"origin": ORIGIN,
                     "pathname": f"/i/{INSTANCE}/student/courses/{COURSE}/runs/{run}/run/page/1"},
        "globals": {"pageLessonId": LESSON_ID, "pageCourseLongName": COURSE_TITLE},
        "root": {
            "lessonTitleDisplay": title, "hasCompletionPage": completion, "runLocked": locked,
            "showPageMenuPageRemaining": show_remaining,
            "remainingFlowPagesWithoutCheckpoints": remaining,
            "checkpointPageContainer": None if container is None else {"pages": container},
        },
        "pages": pages,
    }


# ---------------------------------------------------------------- capture port -----------
# A Python reading of capture.js. It turns a view-model spec into the module.json (and the
# reference.json items) that capture.js would send. tests/capture runs the real capture.js
# over the same specs, so a wrong reading shows up there as a mismatch.

def _models(x):
    return x if isinstance(x, list) else []


def _grid(coll):
    rows = []
    for r in _models(coll):
        cells = _models(r.get("cells")) or _models(r.get("columns"))
        rows.append({"header": r.get("headerRow") is True,
                     "cells": [{"text": S(c.get("content") if c.get("content") is not None
                                          else c.get("text", "")),
                                "header": c.get("headerColumn") is True} for c in cells]})
    return rows


def _body(g):
    return [r for r in g if not r["header"]]


def _grid_text(g):
    lines = [" | ".join(c["text"] for c in r["cells"]) for r in _body(g)]
    return "\n".join(s for s in lines if re.sub(r"[ |]", "", s))


def _author_grid(c, g):
    raw = c.get("rows")
    if raw is None:
        return {"status": "absent"}
    a = _grid(raw)
    if not a:
        return {"status": "absent"}
    fits = len(a) == len(g) and all(len(r["cells"]) == len(g[i]["cells"]) for i, r in enumerate(a))
    return {"status": "read", "grid": a} if fits else {"status": "shape-mismatch"}


def _media(key, alt, role=None):
    m = {"kind": "image"}
    if role:
        m["role"] = role
    m.update({"key": key, "alt": alt, "url": None, "width": None, "height": None,
              "local_path": None})
    return m


def _pool(texts):
    return [{"option_id": f"o{i + 1}", "text": t}
            for i, t in enumerate(sorted(t for t in texts if t))]


def h_text(c, inp):
    s = S(inp.get("answer"))
    return {"type": "text-response", "prompt": S(c.get("question")), "answered": len(s) > 0,
            "ref": {"student_answer": s}}


def h_select(c, inp):
    opts = [{"option_id": f"o{i + 1}", "text": S(o.get("answer"))}
            for i, o in enumerate(_models(c.get("answers")))]
    idx = [n for n in _models(inp.get("selectedAnswers"))
           if isinstance(n, int) and not isinstance(n, bool)]
    sel = [(opts[i]["text"] if i < len(opts) else "") or f"option {i + 1}" for i in idx]
    return {"type": "multiple-choice", "prompt": S(c.get("question")), "answered": len(idx) > 0,
            "options": opts,
            "constraints": {"options_count": len(opts),
                            "note": "multiselect" if c.get("multiselect") is True else "single-select"},
            "ref": {"student_answer": "; ".join(sel), "student_selection": sel}}


def h_table(c, inp):
    g = _grid(inp.get("rows"))
    body = _body(g)
    col_headers = [x["text"] for x in next((r for r in g if r["header"]), {"cells": []})["cells"]]
    row_headers = [[x["text"] for x in r["cells"] if x["header"]] for r in body]
    n_cols = len(g[0]["cells"]) if g else 0
    cons = {"rows": len(g), "cols": n_cols}
    if any(len(t) for t in col_headers):
        cons["column_headers"] = col_headers
    if any(any(len(t) for t in a) for a in row_headers):
        cons["row_headers"] = row_headers
    ag = _author_grid(c, g)

    def authored(ri, ci):
        if ag["status"] != "read":
            return False
        a, x = ag["grid"][ri]["cells"][ci], g[ri]["cells"][ci]
        return (not x["header"]) and len(a["text"]) > 0 and a["text"] == x["text"]

    body_idx = [ri for ri, r in enumerate(g) if not r["header"]]
    if ag["status"] == "read":
        ac = [{"row": ri, "col": ci, "text": a["text"]}
              for ri in body_idx for ci, a in enumerate(ag["grid"][ri]["cells"])
              if not g[ri]["cells"][ci]["header"] and len(a["text"])]
        if ac:
            cons["authored_cells"] = ac
    fmap = ["".join("h" if x["header"] else ("a" if authored(ri, ci) else ("#" if len(x["text"]) else "."))
                    for ci, x in enumerate(g[ri]["cells"])) for ri in body_idx]
    answered = any("#" in s for s in fmap)
    ev = {"filled_map": fmap,
          "author_grid": {"status": ag["status"], "source": "content.rows",
                          "authored_cells": sum(s.count("a") for s in fmap)}}
    if ag["status"] != "read":
        filled_cols = []
        for ci in range(n_cols):
            col = [r["cells"][ci] for r in body if ci < len(r["cells"])]
            if col and all((not x["header"]) and len(x["text"]) for x in col):
                filled_cols.append(ci)
        any_other = any((not x["header"]) and len(x["text"]) and ci not in filled_cols
                        for r in body for ci, x in enumerate(r["cells"]))
        if len(body) > 1 and n_cols > 1 and len(filled_cols) == 1 and not any_other:
            ev["answered_suspect"] = {
                "basis": f"Only body column {filled_cols[0]} contains values across "
                         f"{len(body)} rows. Those values could be authored labels or responses.",
                "note": "Keep answered as captured. Inspect the author grid before attributing "
                        "these values to a learner."}
        elif len(body) > 1 and n_cols > 1:
            filled = [(ri, ci) for ri, r in enumerate(body) for ci, x in enumerate(r["cells"])
                      if (not x["header"]) and len(x["text"])]
            only_l = bool(filled) and all(ri == 0 or ci == 0 for ri, ci in filled)
            top_full = all(ci == 0 or x["header"] or len(x["text"])
                           for ci, x in enumerate(body[0]["cells"]))
            left_full = all(ri == 0 or not r["cells"] or r["cells"][0]["header"]
                            or len(r["cells"][0]["text"]) for ri, r in enumerate(body))
            if only_l and top_full and left_full:
                ev["answered_suspect"] = {
                    "basis": f"Values occupy the first body row and column without header flags. "
                             f"The remaining {len(body) - 1}x{n_cols - 1} cells are blank, "
                             f"consistent with an authored L-shaped label border.",
                    "note": "Keep the captured answered value. Inspect filled_map for blank "
                            "interior cells before claiming that a learner completed the table."}
    return {"type": "table-completion", "prompt": S(c.get("question")), "answered": answered,
            "constraints": cons, "answered_evidence": ev,
            "ref": {"student_answer": _grid_text(g), "note": f"rows={len(g)} body_rows={len(body)}"}}


def h_annotation(c, inp):
    upload = c.get("inputMode") == UPLOAD
    typ = "upload-and-annotate" if upload else "image-annotation"
    ann = inp.get("annotations") or []
    geom = inp if upload else c
    raw = geom.get("presentedRatio") or 0
    known = raw > 0
    author_key = str(c.get("predefinedImageKey") or c.get("imageResourceKey") or "") or None
    student_key = str(inp.get("imageKey") or "") or None
    alt = S(c.get("altTextDescription")) or None
    media = []
    if author_key:
        media.append(_media(author_key, alt, "author-supplied"))
    if student_key:
        media.append(_media(student_key, None, "student-upload"))
    if not media:
        media.append(_media(None, alt, "student-upload" if upload else "author-supplied"))
    answered = (len(ann) > 0 or student_key is not None) if upload else len(ann) > 0

    def mark(a):
        pos = a.get("position") or {}
        text = S(a.get("text"))
        return (f"{S(a.get('type')) or 'mark'}{': ' + text if text else ''}"
                f" @({pos.get('x', 0):.3f},{pos.get('y', 0):.3f})")
    return {
        "type": typ, "prompt": S(c.get("questionText")), "answered": answered,
        "input_mode": UPLOAD if upload else ANNOTATE, "media": media,
        "target_geometry": {
            "presented_ratio": raw if known else None, "accepts_upload": upload,
            "geometry_source": "captured" if known else "unavailable-until-presented",
            "image_key": student_key if upload else author_key,
            "annotations_key": str(inp.get("annotationsKey") or "") or None},
        "automation_override": None if known else "degrade-todo",
        "ref": {"student_answer": "\n".join(mark(a) for a in ann),
                "note": f"annotations={len(ann)} student_image={'yes' if student_key else 'no'}"}}


def _labelling(typ):
    def handler(c, inp):
        labels = [S(x.get("label")) for x in _models(c.get("labelItems"))]
        links = [x for x in _models(inp.get("linkedLabels"))
                 if x.get("locationIndex") is not None and x.get("labelIndex") is not None]
        raw = c.get("presentedRatio") or 0
        n_loc = len(_models(c.get("labelLocationItems")))
        return {
            "type": typ, "prompt": S(c.get("question")), "answered": len(links) > 0,
            "media": [_media(str(c.get("imageResourceKey") or "") or None,
                             S(c.get("altTextDescription")) or None)],
            "constraints": {"options_count": len(labels),
                            "note": (f"presentedRatio={raw:.4f}" if raw > 0
                                     else "presentedRatio unavailable") + f" locations={n_loc}"},
            "options": _pool(labels),
            "ref": {"student_answer": "\n".join(
                        f"{labels[x['labelIndex']] if x['labelIndex'] < len(labels) else '?'}"
                        f" -> location {x['locationIndex']}" for x in links),
                    "note": f"linked={len(links)}/{len(labels)}"}}
    return handler


def h_categories(c, inp):
    cat_items, lab_items = _models(c.get("categoryItems")), _models(c.get("labelItems"))
    cats = [S(x.get("title")) for x in cat_items]
    labels = [S(x.get("label")) for x in lab_items]
    cans = _models(inp.get("categoryAnswerItems"))
    shape = ("empty-collection" if not cans else
             "nested-labelAnswerItems" if any("labelAnswerItems" in x for x in cans)
             else "flat-unmeasured")
    placed = [_models(x.get("labelAnswerItems")) for x in cans]
    n = sum(len(p) for p in placed)
    keys = ["title" if cat_items and S(cat_items[0].get("title")) else None,
            "label" if lab_items and S(lab_items[0].get("label")) else None]
    lines = []
    for i, x in enumerate(placed):
        got = [labels[lb["labelIndex"]] if lb.get("labelIndex", 0) < len(labels) else "?"
               for lb in x]
        if got:
            lines.append(f"{cats[i] if i < len(cats) else f'category {i}'}: {', '.join(got)}")
    return {
        "type": "categorization", "prompt": S(c.get("question")),
        "answered": n > 0 if shape == "nested-labelAnswerItems" else len(cans) > 0,
        "constraints": {"options_count": len(labels), "categories": [t for t in cats if t],
                        "note": f"Grouping fixture: {len(cats)} categories; {len(labels)} labels; "
                                f"input shape {shape}; {len(cans)} answer entries; "
                                f"label fields {'/'.join(k or 'none' for k in keys)}"},
        "options": _pool(labels),
        "ref": {"student_answer": "\n".join(lines), "note": f"placed={n}/{len(labels)} categories={len(cats)}"}}


HANDLERS = {
    "text-question": h_text, "select": h_select, "table-question": h_table,
    "image-annotation": h_annotation,
    "kcImageLabelling": _labelling("image-labeling"),
    "drag-and-drop": _labelling("drag-and-drop-labeling"),
    "categories": h_categories,
}
AUTOM = {"text-response": "structured-path", "multiple-choice": "structured-path",
         "table-completion": "resilient-selector", "image-annotation": "resilient-selector",
         "upload-and-annotate": "structured-path", "image-labeling": "structured-path",
         "drag-and-drop-labeling": "structured-path", "categorization": "structured-path",
         "notebook": "structured-path", "subpage": "structured-path", "unmapped": "degrade-todo"}
DND = {"image-labeling", "drag-and-drop-labeling", "categorization"}
NOTEBOOK_NOTE = ("Notebook content stays private to the learner. Capture records an unmapped "
                 "panel with a permanent manual task; it reads and writes no notebook text.")


def _read_hotspots(c):
    def points(v):
        try:
            p = json.loads(v) if isinstance(v, str) else v
        except ValueError:
            return None
        pts = p.get("points") if isinstance(p, dict) else None
        if not isinstance(pts, list):
            return None
        return [{"x": q["x"], "y": q["y"]} for q in pts if isinstance(q, dict)
                and isinstance(q.get("x"), (int, float)) and isinstance(q.get("y"), (int, float))]

    def text(v):
        return S(str("" if v is None else v).replace(" ", " "))
    out = []
    for h in _models(c.get("hotspots")):
        out.append({"id": h.get("id"), "parent_id": h.get("parentHotspot"),
                    "hotspot_type": S(h.get("hotspotType")) or None,
                    "colour": S(h.get("hotspotColour")) or None,
                    "title": text(h.get("labelTitle")), "text": text(h.get("labelDescription")),
                    "image_key": str(h.get("imageResourceKey") or "") or None,
                    "alt": S(h.get("altTextDescription")) or None,
                    "points": points(h.get("position"))})
    return out


def _walk(p, page_idx, out, parent_id, refs):
    pk = str(p.get("pluginKey"))
    key = str(p.get("panelKey"))
    st = p.get("panelState")
    if not st:
        return
    c = st.get("content") or {}
    pid = f"p{page_idx}-{key}"
    if pid in out["seen"]:
        return
    out["seen"].add(pid)
    if pk == "subpage":
        out["items"].append({
            "item_id": pid, "panel_key": key, "plugin_key": pk, "type": "subpage",
            "prompt_text": "", "attempts": "multi", "assessment_type": "noAssessment",
            "commit_state": "open", "automation": "structured-path", "answered": False,
            "parent_item_id": parent_id})
        for sp in _models(c.get("subpages")):
            for r in _models(sp.get("rows")):
                for cell in _models(r.get("cells")):
                    for child in _models(cell.get("panels")):
                        _walk(child, page_idx, out, pid, refs)
                    for col in _models(cell.get("columns")):
                        for child in _models(col.get("panels")):
                            _walk(child, page_idx, out, pid, refs)
        return
    if "input" not in st:
        if pk == "kcTextArea":
            t = S(c.get("theText"))
            if t and not t.startswith("Copyright ©"):
                out["content"].append({"panel_key": key, "plugin_key": pk, "kind": "text", "text": t})
        elif pk == "image":
            out["content"].append({
                "panel_key": key, "plugin_key": pk, "kind": "image", "text": S(c.get("caption")),
                "media": [_media(str(c.get("imageResourceKey") or "") or None,
                                 S(c.get("altTextDescription")) or None)]})
        elif pk == "table":
            t = "\n".join(s for s in (" | ".join(x["text"] for x in r["cells"])
                                      for r in _grid(c.get("rows"))) if re.sub(r"[ |]", "", s))
            if t:
                out["content"].append({"panel_key": key, "plugin_key": pk, "kind": "other", "text": t})
        elif pk in ("divider", "whitespace", "button", "video", "audio"):
            out["content"].append({"panel_key": key, "plugin_key": pk, "kind": pk})
        elif pk == "hotspot":
            hs = _read_hotspots(c)
            keys = list(dict.fromkeys(h["image_key"] for h in hs if h["image_key"]))
            popups = [h for h in hs if h["title"] or h["text"]]
            out["content"].append({
                "panel_key": key, "plugin_key": pk, "kind": "hotspot",
                "text": "\n\n".join(f"{h['title']}: {h['text']}" if h["title"] and h["text"]
                                    else (h["title"] or h["text"]) for h in popups),
                "media": [_media(k, next((h["alt"] for h in hs if h["image_key"] == k), None))
                          for k in keys],
                "hotspots": hs,
                "capture_note": f"hotspot reader: {len(hs)} hotspot(s), {len(popups)} with popup "
                                f"text, {len(keys)} image key(s)."})
        else:
            names = list(c.keys())[:20]
            out["content"].append({
                "panel_key": key, "plugin_key": pk, "kind": "other",
                "capture_note": f"Unsupported content plugin {pk}. No input key exists, and "
                                f"no content was extracted. Available fields: "
                                f"{', '.join(names) if names else '(none)'}."})
        return
    inp = st.get("input") or {}
    h = HANDLERS.get(pk)
    r = h(c, inp) if h else None
    typ = r["type"] if r else "unmapped"
    attempts = "single" if c.get("singleAttempt") is True else "multi"
    q = c.get("question") if c.get("question") is not None else c.get("questionText")
    ro = next((v for v in (p.get("isReadOnly"), st.get("isReadOnly"), c.get("isReadOnly"))
               if v is True or v is False), None)
    item = {
        "item_id": pid, "panel_key": key, "plugin_key": pk, "type": typ,
        "prompt_text": r["prompt"] if r else S(q), "attempts": attempts,
        "assessment_type": str(c.get("assessmentType") or "noAssessment"),
        "maximum_grade": c.get("maximumGrade"),
        "commit_state": "locked" if inp.get("locked") is True else "open",
        "read_only": ro, "automation": "degrade-todo",
        "answered": r["answered"] if r else False, "parent_item_id": parent_id}
    if r:
        for k in ("input_mode", "media", "options", "constraints", "answered_evidence",
                  "target_geometry"):
            if r.get(k) is not None:
                item[k] = r[k]
        item["automation"] = r.get("automation_override") or (
            "degrade-todo" if attempts == "single" and typ in DND else AUTOM.get(typ, "degrade-todo"))
    elif pk == "notebook":
        item["capture_note"] = NOTEBOOK_NOTE
    else:
        item["capture_note"] = f"Unsupported question plugin {pk}; manual completion is required."
    if item["automation"] == "degrade-todo":
        tg = item.get("target_geometry") or {}
        deg = None
        if typ == "unmapped" and pk == "notebook":
            deg = {"class": "permanent", "reason": "notebook-never-written",
                   "note": "Notebook editing always remains a manual task."}
        elif typ == "unmapped":
            deg = {"class": "lifts-on-capture-support", "reason": "unmapped-type",
                   "note": "A supported plugin handler is required before automatic entry becomes available."}
        elif attempts == "single" and typ in DND:
            deg = {"class": "permanent", "reason": "single-attempt-drag",
                   "note": "Single-attempt drag placement always requires manual interaction."}
        elif tg.get("accepts_upload") is False:
            deg = {"class": "permanent", "reason": "panel-accepts-no-upload",
                   "note": "allowAnnotation permits marking the provided image; replacement uploads are unsupported."}
        elif tg.get("accepts_upload") is True and tg.get("presented_ratio") is None:
            deg = {"class": "lifts-at-fill", "reason": "geometry-unknown-until-presented",
                   "note": "An empty upload slot has no geometry. Read presentedRatio after "
                           "saving the first image to obtain its stored proportions."}
        if deg:
            item["degrade"] = deg
    out["items"].append(item)
    if item["answered"] and r:
        refs.append({"item_id": pid, "maximum_grade": item["maximum_grade"], **r["ref"]})


def _mark_mirrors(pages):
    first = {}
    j = lambda v: json.dumps(v, separators=(",", ":"))  # noqa: E731
    for pg in pages:
        for it in pg["items"]:
            if it["type"] not in ("table-completion", "text-response") or not it["prompt_text"]:
                continue
            k = j([it["type"], it["prompt_text"], it.get("constraints")])
            src = first.get(k)
            if src is None:
                first[k] = (pg, it)
                continue
            if src[0]["page_index"] >= pg["page_index"]:
                continue
            basis = ["duplicate-prompt-and-constraints"]
            if j(it.get("answered_evidence")) == j(src[1].get("answered_evidence")):
                basis.append("identical-content" if it["answered"] else "both-empty")
            if it["read_only"] is None and isinstance(src[1]["read_only"], bool):
                basis.append("read-only-flag-absent-here-present-on-source")
            it["mirror_of"] = {
                "item_id": src[1]["item_id"], "page_index": src[0]["page_index"], "basis": basis,
                "note": "Possible redisplay of an earlier question. Preserve the captured "
                        "read_only state and inspect editability before entering another answer."}


def derive(spec):
    """(module, reference, ids of items that carry mirror_of) for one view-model spec."""
    root, loc = spec["root"], spec["location"]
    m = re.search(r"/i/([^/]+)/student/courses/(\d+)/runs/(\d+)", loc["pathname"])
    instance, course, run = m.group(1), int(m.group(2)), int(m.group(3))
    base_path = re.sub(r"/(page|checkpoint)/\d+$", "", loc["pathname"])
    refs: list = []
    pages = []
    pno = 0
    for pi, pg in enumerate(spec["pages"]):
        is_report = "reportInfoPanels" in pg
        is_cp = (not is_report) and "committed" in pg and "allPanels" not in pg
        is_content = not is_report and not is_cp
        if is_content:
            pno += 1
        out = {"content": [], "items": [], "seen": set()}
        if is_content:
            for p in pg.get("allPanels") or []:
                _walk(p, pi + 1, out, None, refs)
        page = {
            "page_index": pi + 1, "page_no": pno if is_content else None,
            "route": "complete" if is_report else ("checkpoint" if is_cp else None),
            "url": f"{loc['origin']}{base_path}/page/{pno}" if is_content else None,
            "title": S(pg.get("title")), "is_report_page": is_report, "is_checkpoint": is_cp,
            "content": out["content"], "items": out["items"]}
        if is_cp:
            page["checkpoint"] = {"index": pg.get("index"), "committed": pg.get("committed") is True}
        pages.append(page)

    mcq_pages = [p for p in pages if len(p["items"]) >= 2
                 and all(i["type"] == "multiple-choice" for i in p["items"])]
    if len(mcq_pages) == 1:
        p = mcq_pages[0]
        p["rat_candidate"] = {
            "basis": f"One page contains only multiple-choice panels: this page, with "
                     f"{len(p['items'])} questions.",
            "note": "Question types alone cannot distinguish team and individual tests. "
                    "Inspect the page instructions. Team entry requires user-supplied answers "
                    "in rat.answers_dir; individual tests remain excluded. Keep the capture immutable."}

    by_type, captured = {}, 0
    for p in pages:
        for i in p["items"]:
            captured += 1
            by_type[i["type"]] = by_type.get(i["type"], 0) + 1
    single = [i["item_id"] for p in pages for i in p["items"] if i["attempts"] == "single"]
    has_completion = root.get("hasCompletionPage") is True

    cpc = root.get("checkpointPageContainer")
    container_present = isinstance(cpc, dict) and "pages" in cpc
    checkpoints, by_index = [], {}
    for p in pages:
        if p["is_checkpoint"]:
            e = {"index": p["checkpoint"]["index"], "page_index": p["page_index"],
                 "committed": p["checkpoint"]["committed"], "in_flow": True,
                 "source": "lessonFlowPages"}
            checkpoints.append(e)
            if e["index"] is not None:
                by_index[e["index"]] = e
    for cp in (cpc or {}).get("pages", []) if container_present else []:
        idx = cp.get("index")
        seen = by_index.get(idx) if idx is not None else None
        if seen:
            seen["source"] = "both"
            continue
        e = {"index": idx, "page_index": None, "committed": cp.get("committed") is True,
             "in_flow": False, "source": "checkpointPageContainer"}
        checkpoints.append(e)
        if idx is not None:
            by_index[idx] = e
    checkpoints.sort(key=lambda e: e["index"] if e["index"] is not None else 10 ** 9)

    _mark_mirrors(pages)

    module = {
        "schema_version": 1, "module_id": f"c{course}-r{run}", "instance": instance,
        "course_id": course, "run_id": run, "lesson_id": spec["globals"]["pageLessonId"],
        "source_url": f"{loc['origin']}{loc['pathname']}", "captured_at": TS,
        "capture_method": "view-model",
        "lesson": {
            "title": S(root.get("lessonTitleDisplay")),
            "course_title": spec["globals"]["pageCourseLongName"], "page_count": len(pages),
            "has_completion_page": has_completion, "run_locked": root.get("runLocked") is True,
            "show_page_menu_page_remaining": root.get("showPageMenuPageRemaining") is True,
            "remaining_flow_pages_without_checkpoints":
                root.get("remainingFlowPagesWithoutCheckpoints") or 0,
            "completion_route": "complete" if has_completion else None},
        "commit_boundaries": {
            "checkpoint_pages": [p["page_index"] for p in pages if p["is_checkpoint"]],
            "checkpoints": checkpoints, "checkpoints_container_present": container_present,
            "single_attempt_items": single,
            "run_level_commit": {
                "present": has_completion, "route": "complete" if has_completion else None,
                "note": "hasCompletionPage exposes the final route; capture leaves that route unopened."},
            "unmapped": [{"page_index": p["page_index"], "item_id": i["item_id"],
                          "reason": i["capture_note"]}
                         for p in pages for i in p["items"] if i["type"] == "unmapped"]},
        "census": {
            "pages_total": len(pages), "captured_total": captured,
            "answerable_total": captured - by_type.get("subpage", 0), "by_type": by_type,
            "confirmed_by": "panel walk over lessonFlowPages with subpage recursion; answerable "
                            "= panelState carries an input key"},
        "pages": pages}
    reference = {
        "schema_version": 1, "module_id": module["module_id"], "run_id": run, "captured_at": TS,
        "provenance_of_reference": "student-submitted", "never_load_into_drafting_context": True,
        "items": refs}
    mirrors = [i["item_id"] for p in pages for i in p["items"] if i.get("mirror_of")]
    return module, reference, mirrors


# ---------------------------------------------------------------- the seven runs ---------
# Invented course content. Each run is chosen to cover a shape the tests need; the table in
# tests/fixtures/README.md lists them. Names such as vorlase, tessin and the quillen gland
# are made up.

def _cue_rows(filled):
    body = [row("polyuria", "osmotic loss") if filled else row("", ""),
            row("headache", "raised pressure") if filled else row("", "")]
    return [row("Cue", "Why it matters", header=True)] + body


def spec_r101():
    """Locked and completed: run_locked, every item read-only and answered, completion page."""
    author = [row("Time", "Level", "Meaning", header=True),
              row(("Fasting",), "", ""), row(("After load",), "", ""), row(("Next morning",), "", "")]
    student = [row("Time", "Level", "Meaning", header=True),
               row(("Fasting",), "low", "normal baseline"),
               row(("After load",), "high", "tessin builds up"),
               row(("Next morning",), "moderate", "slow clearance")]
    pages = [
        content_page(
            "Introduction",
            ctext(1, "<p>This module follows a fictional patient, Alex Example, through the "
                     "vorlase pathway.</p><p>Read the case, then answer each question &amp; "
                     "check the key terms.</p>"),
            cimage(2, "31", "Figure 1. The fictional vorlase pathway.",
                   "Four labelled boxes joined by arrows"),
            ctext(3, "Copyright © 2000 Example Publishing"),
            cplain(4, "divider")),
        content_page(
            "Case",
            text_q(11, "<p>Describe the first sign of vorlase deficiency in Alex Example.</p>",
                   "The first sign was a delayed response to a tessin load.",
                   assess="graded", grade=4, ro=True, locked=True),
            text_q(12, "Explain why tessin builds up when vorlase is missing.",
                   "Without vorlase the step from tessin to quillic acid stops, so tessin "
                   "collects behind it.", assess="graded", grade=4, ro=True, locked=True),
            mcq(13, "Which product does vorlase form?",
                ["Tessin", "Quillic acid", "Orm salt", "Pell resin"], [1], single=True,
                assess="instantFeedback", ro=True, locked=True),
            mcq(14, "Select every organ that stores tessin.",
                ["Quillen gland", "Pell duct", "Orm lobe", "Zeta fold", "None of these"], [0, 2],
                multi=True, ro=True, locked=True)),
        content_page(
            "Data",
            table_q(21, "Complete the table of tessin levels.", student, author, ro=True,
                    locked=True),
            text_q(22, "State one limit of the data above.", "Only one patient is shown.",
                   assess="graded", grade=2, ro=True, locked=True)),
        content_page(
            "Structures",
            label_q("kcImageLabelling", 31, "Label the four structures on the Zeta section.",
                    ["Zeta fold", "Orm lobe", "Pell duct", "Quillen cap"],
                    [(0.2, 0.3), (0.6, 0.25), (0.35, 0.7), (0.8, 0.65)],
                    [(0, 1), (1, 0), (2, 3), (3, 2)], image_key="32", ratio=0.75,
                    alt="Zeta section with four marked sites", ro=True, locked=True),
            cat_q(32, "Sort each term into its phase.", ["Phase A", "Phase B"],
                  ["tessin loading", "quillic clearance", "vorlase binding", "orm release"],
                  {0: [0, 2], 1: [1, 3]}, single=True, assess="instantFeedback", ro=True,
                  locked=True)),
        report_page(),
    ]
    return module_spec("c1-r101", "1.1 Demo: Vorlase deficiency", pages, completion=True,
                       locked=True, container=[])


def spec_r102():
    """Writable, one unmapped item, an unapproved single-attempt item, content-panel kinds
    (hotspot, unknown plugin, author table), a table with an authored L, a table with an
    author grid, and a completion page."""
    gland_author = [row("Region", "Cell", header=True), row("Cap", "Oram"), row("Duct", "")]
    gland_student = [row("Region", "Cell", header=True), row("Cap", "Oram"), row("Duct", "Pell")]
    schedule = [row("Step", "Signal", "Result"), row("Dawn", "", ""), row("Noon", "", ""),
                row("Dusk", "", ""), row("Night", "", "")]
    pages = [
        content_page(
            "Introduction",
            ctext(1, "<p>Key points:</p><ul><li>The quillen gland releases tessin.</li>"
                     "<li>Release is paced by oram cells.</li></ul>"),
            chotspot(2, [
                spot(1, None, "CUSTOM", "BLUE", "", "", "41", []),
                spot(2, 1, "POINT", "BLUE", "Stain Q", "Colours oram cells deep blue.", "",
                     [(0.25, 0.5)]),
                spot(3, 1, "POINT", "RED", "Stain R",
                     "Colours the duct wall rose. \n \nBest on thin sections.", "",
                     [(0.7, 0.4), (0.72, 0.45)])]),
            cplain(3, "kcMystery", {"widgetText": "not read", "height": 3}),
            ctable(4, [row("Gland", "Product", header=True), row("Quillen", "Tessin"),
                       row("Pell", "Resin")]),
            cplain(5, "divider")),
        content_page(
            "Warm-up",
            text_q(11, "Name the gland that releases tessin.", "", ro=False),
            mcq(12, "How many oram cell types are described?", ["One", "Two", "Three"], [],
                ro=False),
            table_q(13, "Complete the release schedule.", schedule, ro=False)),
        content_page(
            "Decision",
            text_q(21, "Commit to a single best explanation for the gland's output.", "",
                   single=True, ro=False),
            text_q(22, "List one factor that changes release.", "Dusk.", ro=False),
            unmapped_q("rating-scale", 23, "Rate your confidence from 1 to 5.", {"value": None},
                       ro=False)),
        content_page(
            "Review",
            mcq(31, "Which cell paces release?", ["Oram cell", "Pell cell", "Zeta cell"], [0]),
            table_q(32, "Complete the gland map.", gland_student, gland_author),
            text_q(33, "Summarise the release pathway.", "Oram cells pace tessin release at dusk.")),
        report_page(),
    ]
    return module_spec("c1-r102", "1.2 Demo: Quillen gland function", pages, completion=True,
                       container=[])


def spec_r103():
    """No completion page. Upload-and-annotate (known and empty slot), annotate-only (known
    and unknown ratio), the three labelling types, a label pool larger than its zones, a
    single-attempt drag item, and an author image and a student upload sharing key 23."""
    pages = [
        content_page(
            "Overview",
            ctext(1, "<p>Study the Zeta section before you annotate it.</p>"),
            cimage(2, "23", "Figure 1. The supplied Zeta section.", "Zeta section, author figure")),
        content_page(
            "Annotate",
            annot_q(11, "Upload your annotated sketch of the Zeta section.", UPLOAD,
                    student_key="23", ratio=0.625, ann_key="a1",
                    annotations=[{"type": "arrow", "text": "fold", "position": {"x": 0.25, "y": 0.5}}]),
            annot_q(12, "Upload a sketch of the duct layout.", UPLOAD, ratio=0),
            annot_q(13, "Mark the fold on the supplied section.", ANNOTATE, author_key="52",
                    ratio=0.8125, alt="Supplied Zeta section",
                    annotations=[{"type": "pin", "text": "fold", "position": {"x": 0.4, "y": 0.3}},
                                 {"type": "pin", "text": "", "position": {"x": 0.6, "y": 0.7}}]),
            annot_q(14, "Mark the duct on the supplied section.", ANNOTATE, author_key="53",
                    ratio=0, alt="Supplied duct section")),
        content_page(
            "Labelling",
            label_q("kcImageLabelling", 21, "Label the structures.",
                    ["Fold", "Lobe", "Duct", "Cap", "Stalk"],
                    [(0.2, 0.2), (0.7, 0.25), (0.3, 0.75), (0.8, 0.7)], [(0, 0), (1, 1)],
                    image_key="54", ratio=0.75, alt="Zeta section with four drop zones"),
            label_q("drag-and-drop", 22, "Drag each term onto its place.",
                    ["Inlet", "Outlet", "Valve"], [(0.15, 0.5), (0.5, 0.2), (0.85, 0.5)], [],
                    image_key="55", ratio=0.5, alt="Valve diagram with three zones"),
            cat_q(23, "Sort the terms.", ["Early", "Late"],
                  ["warm-up", "peak", "recovery", "rest", "burst"], None)),
        content_page(
            "Final labelling",
            label_q("kcImageLabelling", 31, "Final labelling: place each name.",
                    ["North", "South", "East"], [(0.5, 0.15), (0.5, 0.85), (0.9, 0.5)], [],
                    image_key="56", ratio=0.5, alt="Compass diagram", single=True,
                    assess="graded", grade=3)),
    ]
    return module_spec("c1-r103", "2.1 Demo: Reading a Zeta section", pages, completion=False,
                       container=[])


TRAT_SUBSTANCES = ["tessin", "quillic acid", "orm salt", "pell resin", "zeta wax", "oram gel",
                   "vorlan dust", "quill oil", "fold lime", "cap mist", "duct silt", "lobe tar",
                   "stalk dew", "inlet ash", "valve foam"]
TRAT_OPTIONS = ["Store A", "Store B", "Store C", "Store D"]


def spec_r104():
    """A committed checkpoint (out of the flow, in the container), pages 3 to 8 read-only, a
    submitted single-attempt item (p3-12) on a locked page, subpage children with null
    read_only on a page with lock evidence (page 4) and on one without (page 6), a table
    with a single filled column and one with a mismatched author grid, and a team test of
    15 multiple-choice items on page 9."""
    def ro_text(key, q, a, **kw):
        return text_q(key, q, a, ro=True, **kw)

    def tbl(first, second):
        return [row("Item", "Finding", header=True), row(first, "noted"), row(second, "noted")]

    one_col = [row("Item", "Finding", header=True), row("Rate", ""), row("Rhythm", ""),
               row("Tone", "")]
    mismatch_student = [row("Probe", "Reading", header=True), row("A", "high"), row("B", "low")]
    mismatch_author = [row("Probe", "Reading", header=True), row("A", ""), row("B", ""),
                       row("C", "")]
    ok_student = [row("Probe", "Reading", header=True), row("A", "high"), row("B", "")]
    ok_author = [row("Probe", "Reading", header=True), row("A", ""), row("B", "")]
    trat = [mcq(71 + n, f"Team question {n + 1}: which store holds {sub} in the fictional model?",
                TRAT_OPTIONS, [], ro=False)
            for n, sub in enumerate(TRAT_SUBSTANCES)]
    pages = [
        content_page("Orientation", ctext(1, "<p>Team case: tessin storage.</p>")),
        content_page("Background", ctext(1, "<p>Stores A to D hold different substances.</p>"),
                     cimage(2, "61", "Figure 1. The four stores.", "Four boxes labelled A to D")),
        content_page(
            "Case history",
            ro_text(12, "Give the history in one sentence.", "Tessin was stored in Store B.",
                    single=True, assess="graded", grade=2),
            ro_text(13, "Name the store involved.", "Store B."),
            mcq(14, "Which store was involved?", TRAT_OPTIONS, [1], ro=True)),
        content_page(
            "Examination",
            ro_text(20, "State the main finding.", "A raised level."),
            subpage(21,
                    table_q(22, "Record the three findings.", one_col),
                    text_q(23, "Add a note on the findings.", "Noted."))),
        content_page(
            "Investigations",
            table_q(31, "List the tests.", tbl("Level", "Trend"), ro=True),
            mcq(32, "Which test is first?", ["Level", "Trend", "Rate"], [0], ro=True)),
        content_page(
            "Results",
            subpage(41,
                    table_q(42, "Record the probe readings.", mismatch_student, mismatch_author),
                    table_q(43, "Record the second set.", ok_student, ok_author))),
        content_page(
            "Reasoning",
            ro_text(51, "Explain the finding.", "Store B holds the tessin."),
            ro_text(52, "State the limit of the case.", "One store was sampled.")),
        content_page(
            "Plan",
            table_q(61, "Write the plan.", tbl("Store", "Review"), ro=True),
            ro_text(62, "State the first step.", "Review Store B.")),
        content_page("Team quiz", *trat),
        report_page(),
    ]
    return module_spec("c1-r104", "1.3 Demo: Team case on tessin storage", pages, completion=True,
                       container=[{"index": 8, "committed": True, "title": "Checkpoint"}])


def spec_r105():
    """A report page, three mirror pairs and one near-duplicate on a decision
    page, as the diagnostic-reasoning page of a team case lays them out."""
    def tab(a, b):
        return [row("Cue", "Why it matters", header=True), row(*a), row(*b)]

    def author():
        return [row("Cue", "Why it matters", header=True), row("", ""), row("", "")]
    t1 = tab(("tessin surplus", "blocked conversion"), ("low quillic acid", "reduced output"))
    t2 = tab(("age", "narrows the list"), ("onset", "sets the pace"))
    t3 = tab(("level", "confirms the cause"), ("scan", "shows the site"))
    t4 = tab(("rest", "first step"), ("review", "second step"))
    t4b = tab(("fluids", "first step"), ("recheck", ""))
    q1, q2, q3, q4 = ("Pair fictional observations with explanations.", "Describe the invented case features.",
                      "Choose tests for the fictional pathway.", "Arrange the fictional follow-up steps.")
    pages = [
        content_page("Starting observations", ctext(1, "<p>A fictional reaction produces little quillic acid.</p>"),
                     table_q(11, q1, t1, author(), ro=False)),
        content_page("Findings", table_q(21, q2, t2, author(), ro=False),
                     text_q(22, "State your provisional diagnosis.", "Vorlase deficiency.",
                            ro=False)),
        content_page("Tests", table_q(31, q3, t3, author(), ro=False),
                     table_q(32, q4, t4, author(), ro=False)),
        content_page("Review observations",
                     table_q(41, q1, t1, author()), table_q(42, q2, t2, author()),
                     table_q(43, q3, t3, author()), table_q(44, q4, t4b, author(), ro=False),
                     text_q(46, "Write the final diagnosis.", "", ro=False)),
        report_page(),
    ]
    return module_spec("c1-r105", "2.2 Demo: Diagnostic reasoning", pages, completion=True,
                       container=[])


def spec_r106():
    """An uncommitted checkpoint in the flow (so no report page is captured), two tables of
    17 and 6 rows with header columns, and a lesson that reports pages remaining."""
    names17 = [f"Minute {m}" for m in range(0, 80, 5)]
    head = row("Interval", "Goal", "Option", header=True)
    big = [head] + [row((n,), "hold steady" if i < 4 else "", "") for i, n in enumerate(names17)]
    names6 = ["Short term", "Medium term", "Long term", "Review", "Exit"]
    small = [row("Horizon", "Goal", "Option", header=True)] + [row((n,), "", "") for n in names6]
    assert len(big) == 17 and len(small) == 6
    pages = [
        content_page("Brief", ctext(1, "<p>Plan the fictional patient's care in two grids.</p>")),
        content_page("Grids",
                     table_q(11, "Complete the timed plan.", big, ro=False),
                     table_q(12, "Complete the horizon plan.", small, ro=False)),
        checkpoint_page(7, False),
    ]
    return module_spec("c1-r106", "2.3 Demo: Tables and plans", pages, completion=True,
                       show_remaining=True, remaining=2,
                       container=[{"index": 7, "committed": False, "title": "Checkpoint"}])


def spec_r107():
    """The demo run: two pages, a subpage with a child, an upload-and-annotate item with no
    prompt, a notebook, and a page of three multiple-choice items flagged rat_candidate."""
    pages = [
        content_page(
            "Start",
            ctext(1, "<p>A short tour of the question types.</p>"),
            text_q(11, "Why does the tour exist?", ""),
            annot_q(12, None, UPLOAD, ratio=0),
            subpage(13, text_q(14, "Add one detail inside the subpage.", "")),
            notebook(16)),
        content_page(
            "Check",
            mcq(21, "Which colour is used for stage one?", ["Blue", "Red", "Green"], []),
            mcq(22, "Which colour is used for stage two?", ["Blue", "Red", "Green"], []),
            mcq(23, "Which colour is used for stage three?", ["Blue", "Red", "Green"], [])),
    ]
    return module_spec("c1-r107", "Demo: Quick tour", pages, completion=False, container=[])


SPEC_BUILDERS = {"c1-r101": spec_r101, "c1-r102": spec_r102, "c1-r103": spec_r103,
                 "c1-r104": spec_r104, "c1-r105": spec_r105, "c1-r106": spec_r106,
                 "c1-r107": spec_r107}

# What the mirror detector must find, written from the design of each page rather than read
# back from the derived module: (source item, mirror item, basis).
FULL_BASIS = ["duplicate-prompt-and-constraints", "identical-content",
              "read-only-flag-absent-here-present-on-source"]
EXPECTED_MIRRORS = {
    "c1-r105": {
        "pairs": [("p1-11", "p4-41", FULL_BASIS), ("p2-21", "p4-42", FULL_BASIS),
                  ("p3-31", "p4-43", FULL_BASIS)],
        "near_duplicates": [("p3-32", "p4-44", ["duplicate-prompt-and-constraints"])]},
}


# ---------------------------------------------------------------- course material -------
# A small invented course folder, shaped like the tree rat.* and curriculum.* settings point
# at: blocks containing week folders, in both naming styles, and one ILO file.

SLIDES_11 = """# Slides: Vorlase deficiency (invented)

## Slide 1
Vorlase is an enzyme of the fictional quillen pathway.

## Slide 2
Vorlase turns tessin into quillic acid.

## Slide 3
Without vorlase, tessin builds up and quillic acid falls.

## Slide 4
A tessin load test shows a high peak that clears slowly in deficiency.

## Slide 5
Tessin is stored in the quillen gland and the orm lobe.
"""

TRANSCRIPT_11 = """WEBVTT

00:00:05.000 --> 00:00:12.000
Today we follow one fictional patient through the vorlase pathway.

00:03:10.000 --> 00:03:18.000
Vorlase turns tessin into quillic acid, and nothing else in this pathway can.

00:05:40.000 --> 00:05:49.000
When vorlase is missing, tessin collects behind the blocked step.
"""

NOTES_11 = """# Practical notes: reading a tessin load test (invented)

- Take a fasting sample first.
- Give the load, then sample again after the stated interval.
- A high peak with slow clearance fits vorlase deficiency in this fictional course.
"""

SLIDES_12 = """# Slides: Quillen gland function (invented)

## Slide 1
The quillen gland releases tessin in short bursts.

## Slide 2
Oram cells pace the release.

## Slide 3
Release is highest at dusk and lowest at night.

## Slide 4
The cap holds the oram cells and the duct carries the output.
"""

SLIDES_13A = """# Slides: Team case on tessin storage, edition A (invented)

## Slide 1
Four stores, A to D, hold different substances in the fictional model.

## Slide 2
Store B holds tessin.

## Slide 3
Store C holds quillic acid and orm salt.

## Slide 4
Store A and Store D hold the remaining substances.
"""

SLIDES_13B = """# Slides: Team case on tessin storage, edition B (invented)

## Slide 1
A second edition of the same fictional storage case, with the stores in another order.
"""

ILO_MD = """# Example Course 2000: learning outcomes

## Week 1.1 Demo: Vorlase deficiency

### Learning outcomes

1. Describe how vorlase turns tessin into quillic acid.
2. Explain why tessin builds up when vorlase is missing.
3. Interpret a tessin load test.

## 1.2 Demo: Quillen gland function

### Learning outcomes

1. Describe how the quillen gland releases tessin.
2. Name the cell that paces release.

## Week 1.3 Alpha: Team case on tessin storage

### Learning outcomes

1. Name the store that holds each substance in the fictional model.
"""

CURRICULUM = {
    "ILO.md": ILO_MD,
    "Block A/Week 1.1 Demo/Lectures/slides.md": SLIDES_11,
    "Block A/Week 1.1 Demo/Lectures/transcript.vtt": TRANSCRIPT_11,
    "Block A/Week 1.1 Demo/Practical/notes.md": NOTES_11,
    "Block A/1.2 Demo/Lectures/slides.md": SLIDES_12,
    "Block A/Week 1.3 Alpha/Lectures/slides.md": SLIDES_13A,
    "Block B/1.3 Beta/Lectures/slides.md": SLIDES_13B,
}
CUR = "tests/fixtures/curriculum"

PAGE_MD = """title:: Lt/1.1 Demo: Vorlase deficiency
type:: **Lt Module Output**
lt-module:: c1-r101
lt-run:: 101
lt-source:: https://lt.example.invalid/i/inst0001/student/courses/1/runs/101/run/page/1
tags:: [[Lt]], [[module output]]

-
- # Page 2 - Case
\t- ## Explain why tessin builds up when vorlase is missing.
\t  *p2-12 · high confidence*
\t\t- Without vorlase the tessin to quillic acid step stops, so tessin collects behind it. [T1:DEMO-1-1-SLIDES:slide 3]
\t\t- ![Four labelled boxes joined by arrows](../assets/lt-c1-r101-31.png)
\t\t- Fill: **filled** (verified)
"""


# ---------------------------------------------------------------- answers ---------------

def claim(text, *refs):
    return {"kind": "claim", "text": text, "provenance_refs": list(refs)}


def conn(text):
    return {"kind": "connective", "text": text}


def t1(ref, source, locator):
    return {"ref": ref, "tier": 1, "source_id": source, "locator": locator,
            "tag": f"[T1:{source}:{locator}]"}


def t3(ref, source, locator, title):
    return {"ref": ref, "tier": 3, "source_id": source, "locator": locator, "title": title,
            "tag": f"[T3:{source}:{locator}]"}


def t4(ref, doi, locator):
    return {"ref": ref, "tier": 4, "source_id": doi, "locator": locator, "tag": f"[T4:{doi}]"}


DOI = "10.5555/fixture.0001"


def drafted(item_id, segments, provs, *, confidence="high", reasons=None, **extra):
    """A drafted answer. Descent records are filled in for every tier above the deepest
    tier cited; `reasons` overrides the default 'absent' per step."""
    deepest = max(p["tier"] for p in provs)
    reasons = reasons or {}
    out = {"item_id": item_id, "status": "drafted", "segments": segments, "provenance": provs,
           "confidence": confidence}
    if deepest > 1:
        out["tier_descent"] = [{"from_tier": t, "to_tier": t + 1,
                                "reason": reasons.get(t, "absent")} for t in range(1, deepest)]
    out.update(extra)
    return out


def skipped(item_id, reason, **extra):
    return {"item_id": item_id, "status": "skipped", "skip_reason": reason, **extra}


def blocked(item_id, blocker, placement, **extra):
    return {"item_id": item_id, "status": "blocked",
            "todo": {"blocker": blocker, "placement": placement}, **extra}


def cells(rows, cols, texts):
    """table_cells for a block of rows x cols, row-major."""
    it = iter(texts)
    return [{"row": r, "col": c, "text": next(it)} for r in rows for c in cols]


class Ctx:
    """What the per-module answer builders read: the spec and the derived module."""

    def __init__(self, spec, module, rng):
        self.spec, self.module, self.rng = spec, module, rng
        self.panels = {}
        for pi, pg in enumerate(spec["pages"]):
            for p in pg.get("allPanels") or []:
                self._index(p, pi + 1)

    def _index(self, p, page):
        self.panels[f"p{page}-{p['panelKey']}"] = p
        c = p["panelState"].get("content") or {}
        for sp in c.get("subpages") or []:
            for r in sp.get("rows") or []:
                for cell in r.get("cells") or []:
                    for ch in cell.get("panels") or []:
                        self._index(ch, page)

    def item(self, item_id):
        return next(i for p in self.module["pages"] for i in p["items"] if i["item_id"] == item_id)

    def where(self, item_id, loc, desc):
        """Target prose for a labelling placement: the described spot plus the anchor
        position, as a drafter reads it off the image."""
        c = self.panels[item_id]["panelState"]["content"]
        z = c["labelLocationItems"][loc]
        return (f"{desc}, anchor {loc} at {z['anchorX'] * 100:.1f}% across, "
                f"{z['anchorY'] * 100:.1f}% down")


def src(stem):
    return f"tests/fixtures/curriculum/{stem}"


def corpus_11():
    return [{"source_id": "DEMO-1-1-SLIDES", "path": f"{src('Block A/Week 1.1 Demo/Lectures/slides.md')}", "kind": "slides"},
            {"source_id": "DEMO-1-1-TRANSCRIPT", "path": f"{src('Block A/Week 1.1 Demo/Lectures/transcript.vtt')}", "kind": "transcript"},
            {"source_id": "DEMO-1-1-ILO", "path": f"{src('ILO.md')}", "kind": "ilo"}]


S11 = lambda ref, n: t1(ref, "DEMO-1-1-SLIDES", f"slide {n}")  # noqa: E731


def answers_r101(cx):
    items = [
        drafted("p2-11", [claim("The first sign is a delayed response to a tessin load.", "p1")],
                [S11("p1", 4)]),
        drafted("p2-12",
                [claim("Without vorlase the step from tessin to quillic acid stops.", "p1"),
                 conn("So"),
                 claim("tessin collects behind the blocked step.", "p2")],
                [S11("p1", 3), t1("p2", "DEMO-1-1-TRANSCRIPT", "00:05:40")]),
        skipped("p2-13", "already-answered"),
        drafted("p2-14", [claim("Tessin is stored in the quillen gland and the orm lobe.", "p1")],
                [S11("p1", 5)], proposed_selection=["o1", "o3"]),
        drafted("p3-21",
                [claim("A load test shows a high peak that clears slowly in deficiency.", "p1")],
                [S11("p1", 4)],
                table_cells=cells(range(1, 4), range(1, 3),
                                  ["low", "normal baseline", "high", "tessin builds up",
                                   "moderate", "slow clearance"])),
        drafted("p3-22", [claim("The data come from one patient only.", "p1")], [S11("p1", 4)],
                confidence="medium"),
        drafted("p4-31",
                [claim("Each named structure sits at the matching marked site on the section.", "p1")],
                [S11("p1", 5)],
                label_placements=[
                    {"label": "Zeta fold", "target": cx.where("p4-31", 1, "the upper right fold"), "confidence": "high"},
                    {"label": "Orm lobe", "target": cx.where("p4-31", 0, "the upper left lobe"), "confidence": "high"},
                    {"label": "Pell duct", "target": cx.where("p4-31", 3, "the tube at lower right"), "confidence": "high"},
                    {"label": "Quillen cap", "target": cx.where("p4-31", 2, "the cap at lower left"), "confidence": "high"}]),
        skipped("p4-32", "single-attempt-drag"),
    ]
    return corpus_11(), items


def answers_r102(cx):
    s12 = lambda ref, n: t1(ref, "DEMO-1-2-SLIDES", f"slide {n}")  # noqa: E731
    corpus = [{"source_id": "DEMO-1-2-SLIDES", "path": src("Block A/1.2 Demo/Lectures/slides.md"), "kind": "slides"},
              {"source_id": "DEMO-1-1-ILO", "path": src("ILO.md"), "kind": "ilo"}]
    items = [
        drafted("p2-11", [claim("The quillen gland releases tessin.", "p1")], [s12("p1", 1)],
                confidence="low",
                uncertainties=[{"question": "Does the question mean the gland or the cell type?",
                                "why_it_matters": "The answer names the gland, not the cell."}]),
        drafted("p2-12", [claim("Oram cells pace the release, and one cell type is described.", "p1")],
                [s12("p1", 2)], proposed_selection=["o1"]),
        drafted("p2-13",
                [claim("Release is highest at dusk and lowest at night.", "p1")], [s12("p1", 3)],
                table_cells=cells(range(1, 5), range(1, 3),
                                  ["first light", "low", "midday", "rising", "highest", "peak",
                                   "none", "lowest"])),
        drafted("p3-21", [claim("Oram cells pace the release of tessin.", "p1")], [s12("p1", 2)]),
        drafted("p3-22",
                [claim("Release changes with the time of day.", "p1"),
                 claim("A longer fast shifts the peak later.", "p2")],
                [s12("p1", 3), t4("p2", DOI, "table 2")],
                reasons={1: "absent", 2: "absent", 3: "insufficient-depth"},
                conflicts=[{"higher_ref": "p1", "lower_ref": "p2",
                            "higher_claim": "Release is highest at dusk.",
                            "lower_claim": "A longer fast shifts the peak later.",
                            "note": "The slide gives one peak time; the paper describes a shift."}]),
        skipped("p3-23", "unmapped-boundary"),
        drafted("p4-31", [claim("Oram cells pace the release.", "p1")], [s12("p1", 2)],
                proposed_selection=["o1"]),
        skipped("p4-32", "already-answered"),
        skipped("p4-33", "already-answered"),
    ]
    return corpus, items


def answers_r103(cx):
    brief = {"intent": "Show the duct layout of the Zeta section.",
             "nodes": ["cap", "duct", "fold"],
             "edges": [{"from": "cap", "to": "duct", "role": "stimulation"},
                       {"from": "fold", "to": "duct", "role": "modulation"}]}
    d4 = lambda ref, loc: t4(ref, DOI, loc)  # noqa: E731
    items = [
        drafted("p2-11", [claim("The fold sits above the duct in the Zeta section.", "p1")],
                [d4("p1", "figure 1")], needs_diagram=True, diagram_brief=brief,
                asset_refs=["diagram-p2-11.png"],
                asset_captions={"diagram-p2-11.png": "Fold above duct, drawn for the fixture."}),
        drafted("p2-12", [claim("The duct runs from the cap to the fold.", "p1")],
                [d4("p1", "figure 2")], needs_diagram=True, diagram_brief=brief,
                asset_refs=["diagram-p2-12.png"]),
        blocked("p2-13", "Marking this provided image requires manual interaction; an annotation "
                         "writer is unavailable.",
                "Page 2, panel 13. Mark the fold on the supplied section by hand."),
        blocked("p2-14", "Marking this provided image requires manual interaction; an annotation "
                         "writer is unavailable.",
                "Page 2, panel 14. Mark the duct on the supplied section by hand."),
        drafted("p3-21", [claim("Four of the five names mark a zone; the stalk is a distractor.", "p1")],
                [d4("p1", "figure 3")], confidence="medium",
                label_placements=[
                    {"label": "Fold", "target": cx.where("p3-21", 0, "the upper left fold"), "confidence": "high"},
                    {"label": "Lobe", "target": cx.where("p3-21", 1, "the upper right lobe"), "confidence": "high"},
                    {"label": "Duct", "target": cx.where("p3-21", 2, "the tube at lower left"), "confidence": "medium",
                     "alternative": "the stalk at lower left"},
                    {"label": "Cap", "target": cx.where("p3-21", 3, "the cap at lower right"), "confidence": "high"}]),
        drafted("p3-22", [claim("Fluid enters at the inlet, passes the valve and leaves at the outlet.", "p1")],
                [d4("p1", "figure 4")],
                label_placements=[
                    {"label": "Inlet", "target": cx.where("p3-22", 0, "the left opening"), "confidence": "high"},
                    {"label": "Valve", "target": cx.where("p3-22", 1, "the gate at the top"), "confidence": "high"},
                    {"label": "Outlet", "target": cx.where("p3-22", 2, "the right opening"), "confidence": "high"}]),
        drafted("p3-23", [claim("Warm-up and burst are early; recovery and rest are late.", "p1")],
                [d4("p1", "table 1")],
                label_placements=[
                    {"label": "warm-up", "target": "Early", "target_index": 0, "confidence": "high"},
                    {"label": "burst", "target": "Early", "target_index": 0, "confidence": "high"},
                    {"label": "peak", "target": "Early", "target_index": 0, "confidence": "high"},
                    {"label": "recovery", "target": "Late", "target_index": 1, "confidence": "high"},
                    {"label": "rest", "target": "Late", "target_index": 1, "confidence": "high"}]),
        skipped("p4-31", "single-attempt-drag"),
    ]
    return [], items


def answers_r104(cx, approval_id):
    corpus = [{"source_id": "DEMO-1-3A-SLIDES", "path": src("Block A/Week 1.3 Alpha/Lectures/slides.md"), "kind": "slides"}]
    s = lambda ref, n: t1(ref, "DEMO-1-3A-SLIDES", f"slide {n}")  # noqa: E731
    rng = cx.rng.fork("c1-r104-trat")
    items = [
        drafted("p3-12", [claim("Tessin was stored in Store B.", "p1")], [s("p1", 2)],
                single_attempt_approval={"approved_by": "user", "approval_id": approval_id}),
        skipped("p3-13", "already-answered"),
        skipped("p3-14", "already-answered"),
    ]
    subs = TRAT_SUBSTANCES
    holds = {"tessin": 1, "quillic acid": 2, "orm salt": 2}
    for n, sub in enumerate(subs):
        pick = holds.get(sub, rng.below(4))
        items.append(drafted(
            f"p9-{71 + n}",
            [claim(f"{TRAT_OPTIONS[pick]} holds {sub} in the fictional model.", "p1")],
            [s("p1", 2 + (n % 3))], proposed_selection=[f"o{pick + 1}"],
            confidence="medium" if sub not in holds else "high"))
    return corpus, items


def answers_r105(cx):
    g3 = lambda ref, loc: t3(ref, "EXAMPLE-HEALTH-GUIDE", loc, "Invented guidance note")  # noqa: E731

    def tab(prefix_rows, texts):
        return cells(prefix_rows, range(0, 2), texts)
    items = [
        drafted("p1-11", [claim("Thirst and frequent urine are the two cues.", "p1")], [g3("p1", "section 1")],
                table_cells=tab(range(1, 3), ["polyuria", "osmotic loss", "thirst", "dehydration"])),
        drafted("p2-21", [claim("Age and onset narrow the list.", "p1")], [g3("p1", "section 2")],
                table_cells=tab(range(1, 3), ["age", "narrows the list", "onset", "sets the pace"])),
        drafted("p2-22", [claim("The provisional diagnosis is vorlase deficiency.", "p1")], [g3("p1", "section 3")]),
        drafted("p3-31", [claim("A level and a scan confirm the cause.", "p1")], [g3("p1", "section 4")],
                table_cells=tab(range(1, 3), ["level", "confirms the cause", "scan", "shows the site"])),
        drafted("p3-32", [claim("Rest and review are the first two steps.", "p1")], [g3("p1", "section 5")],
                table_cells=tab(range(1, 3), ["rest", "first step", "review", "second step"])),
        skipped("p4-41", "read-only-mirror"),
        skipped("p4-42", "read-only-mirror"),
        skipped("p4-43", "read-only-mirror"),
        skipped("p4-44", "read-only-mirror"),
        drafted("p4-46", [claim("The final diagnosis is vorlase deficiency.", "p1")], [g3("p1", "section 3")]),
    ]
    return [], items


def answers_r106(cx):
    d4 = lambda ref, loc: t4(ref, DOI, loc)  # noqa: E731
    big = cx.item("p2-11")["constraints"]["row_headers"]
    goals = [f"hold at {h[0].lower()}" for h in big]
    items = [
        drafted("p2-11", [claim("Check the level every five minutes for the first hour.", "p1")],
                [d4("p1", "table 3")],
                table_cells=[c for i, g in enumerate(goals)
                             for c in ({"row": i + 1, "col": 1, "text": g},
                                       {"row": i + 1, "col": 2, "text": "repeat"})]),
        drafted("p2-12", [claim("Short-term care holds steady; long-term care needs a review.", "p1")],
                [d4("p1", "table 4")],
                table_cells=cells(range(1, 6), range(1, 3),
                                  ["hold steady", "observe", "reassess", "adjust", "plan review",
                                   "set date", "audit", "repeat", "close", "hand over"])),
    ]
    return [], items


def answers_r107(cx):
    d4 = lambda ref, loc: t4(ref, DOI, loc)  # noqa: E731
    brief = {"intent": "Sketch the tour order.", "nodes": ["start", "check"],
             "edges": [{"from": "start", "to": "check", "role": "stimulation"}]}
    items = [
        drafted("p1-11", [claim("The tour shows each question type once.", "p1")], [d4("p1", "section 1")]),
        drafted("p1-12", [claim("The sketch shows the start step leading to the check step.", "p1")],
                [d4("p1", "section 2")], needs_diagram=True, diagram_brief=brief,
                asset_refs=["diagram-p1-12.png"]),
        drafted("p1-14", [claim("The detail inside the subpage is the order of steps.", "p1")],
                [d4("p1", "section 3")]),
        skipped("p1-16", "unmapped-boundary"),
        skipped("p2-21", "trat-answers-unavailable"),
        skipped("p2-22", "trat-answers-unavailable"),
        skipped("p2-23", "trat-answers-unavailable"),
    ]
    return [], items


# ---------------------------------------------------------------- approvals --------------
# tests/fixtures/approvals/approvals-c1-r104.json belongs to the schema package. The draft
# of p3-12 cites its id. The generated run also stores the two synthetic waivers used by
# its fill report, without changing the schema package's single-approval fixture.

SINGLE_ITEM = "p3-12"
APPROVAL_QUOTES = {
    "single": "Yes, write p3-12 as drafted.",
    "manual": "Run it on auto, I accept that it ends at the completion page.",
    "passed": "Go on past page 3, I know p3-12 is already submitted.",
}


def approval_id(module_id, item_id, quote, recorded_at, **scope):
    """Use the shared capture-bound approval contract for synthetic records."""
    sys.path.insert(0, str(SHARED))
    try:
        from ltcore.approvals import approval_id as make_id
        return make_id(module_id, item_id, quote, recorded_at, **scope)
    finally:
        sys.path.pop(0)


def approvals_for_r104(module):
    """{'single', 'manual', 'passed'} approval ids plus which of them the file holds."""
    qdigest = load_digest()(module)
    ids = {
        "single": approval_id("c1-r104", SINGLE_ITEM, APPROVAL_QUOTES["single"], TS,
                              question_digest=qdigest),
        "manual": approval_id("c1-r104", None, APPROVAL_QUOTES["manual"], TS,
                              kind="waiver", rail="approval-manual-at-boundary", question_digest=qdigest),
        "passed": approval_id("c1-r104", None, APPROVAL_QUOTES["passed"], TS,
                              kind="waiver", rail="passed-single-approval", item_ids=[SINGLE_ITEM],
                              question_digest=qdigest)}
    found = {"single": False, "manual": False, "passed": False}
    path = FIXTURES / "approvals" / "approvals-c1-r104.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        recs = doc.get("approvals", []) if doc.get("module_id") == "c1-r104" else []
    except (OSError, ValueError):
        recs = []
    for r in recs:
        if r.get("kind") == "single-attempt" and r.get("item_id") == SINGLE_ITEM:
            ids["single"], found["single"] = r["approval_id"], True
        elif r.get("kind") == "waiver" and r.get("rail") == "approval-manual-at-boundary":
            ids["manual"], found["manual"] = r["approval_id"], True
        elif (r.get("kind") == "waiver" and r.get("rail") == "passed-single-approval"
              and SINGLE_ITEM in (r.get("item_ids") or [SINGLE_ITEM])):
            ids["passed"], found["passed"] = r["approval_id"], True
    return ids, found


def run_approvals(ids, module):
    """Complete synthetic approvals for the run's draft and its two report waivers."""
    path = FIXTURES / "approvals" / "approvals-c1-r104.json"
    originals = json.loads(path.read_text(encoding="utf-8"))["approvals"] if path.is_file() else []
    records = []
    for key, fields in (
        ("single", {"kind": "single-attempt", "item_id": SINGLE_ITEM}),
        ("manual", {"kind": "waiver", "rail": "approval-manual-at-boundary"}),
        ("passed", {"kind": "waiver", "rail": "passed-single-approval", "item_ids": [SINGLE_ITEM]}),
    ):
        original = next((r for r in originals if r["approval_id"] == ids[key]), None)
        records.append(copy.deepcopy(original) if original else {
            "approval_id": ids[key], **fields, "approved_by": "user",
            "quote": APPROVAL_QUOTES[key], "recorded_at": TS,
            "question_digest": load_digest()(module)})
    return {"schema_version": 1, "module_id": "c1-r104", "approvals": records}


# ---------------------------------------------------------------- fill reports ----------

# Why a save wait proves nothing alone: the fresh-load receipt decides. Items filled on such a
# page carry the sentence in stopped_reason, ahead of the receipt sentence.
WEAK_WAIT_TEXT = {
    "fallback": "Saved under the fallback wait (no calibrated save signal for this origin); "
                "the fresh-load receipt is the proof of the save.",
    "unconfirmed": "The save wait ended unconfirmed; the fresh-load receipt decides.",
    "error": "The save wait reported a server error on this call; the fresh-load receipt decides.",
}


def save_wait_rows(mid, waits):
    """Rows shaped like the fill script's save-wait records. Each `receipt` names a browser
    receipt that is not shipped; its digest is taken over the invented name."""
    rows = []
    for n, w in enumerate(waits, 1):
        name = f"fill-result-{n}.json"
        rows.append({
            "receipt": name, "receipt_sha256": sha256(f"fixture receipt {mid} {name}".encode()),
            "pages": w["pages"], "save_signal": w["signal"], "waited_ms": w["waited_ms"],
            "keys_total": w["total"], "keys_saved": w["saved"], "keys_pending": w["pending"],
            "save_errors": w["errors"], "calibrated": w["calibrated"], "ok": w["ok"],
            "fatal": w.get("fatal")})
    return rows


def fill_report(mid, cx, answers_bytes, plan, *, approval_mode, boundary, pages_filled,
                never_navigated, rng, user_decisions=None, selector_events=None,
                connection_events=None, save_waits=None):
    """(report, receipts, notes, wait_rows) for one run. `plan` is a list of dicts:
    {item, kind: filled|skipped|todo, method?, selector?, reason?, todo?, lt_conflict?}.
    `save_waits` is a list of dicts {pages, signal, waited_ms, total, saved, pending, errors,
    calibrated, ok, fatal?}."""
    module = cx.module
    run = RUNS[mid][0]
    wait_rows = save_wait_rows(mid, save_waits or [])
    weak_pages = {}
    for w in save_waits or []:
        if w["signal"] in WEAK_WAIT_TEXT:
            for pg in w["pages"]:
                weak_pages.setdefault(pg, w["signal"])
    page_of = {i["item_id"]: pg["page_index"] for pg in module["pages"] for i in pg["items"]}
    items, receipts, notes_items = [], [], {}
    for e in plan:
        it = cx.item(e["item"])
        base = {"item_id": e["item"], "item_type": it["type"], "attempts": it["attempts"]}
        if e["kind"] == "filled":
            h = rng.fork(f"receipt:{mid}:{e['item']}").hash32()
            rec = {"item_id": e["item"], "expected_hash": h, "observed_hash": h, "observed_at": TS}
            receipts.append(rec)
            d = dict(base, attempted=True, method=e.get("method", "view-model"))
            if e.get("selector"):
                d["selector_used"] = e["selector"]
            weak = weak_pages.get(page_of[e["item"]])
            if weak:
                d["stopped_reason"] = (f"{WEAK_WAIT_TEXT[weak]} Verified on a fresh load at {TS}: "
                                       f"hash {h} equals the drafted {h}.")
            d.update(result="filled", verified=True,
                     receipt={k: rec[k] for k in ("expected_hash", "observed_hash", "observed_at")})
            if e.get("lt_conflict"):
                d["lt_conflict"] = e["lt_conflict"]
            if e.get("approval_id"):
                d["approval_id"] = e["approval_id"]
            note = {k: d[k] for k in ("method", "selector_used", "lt_conflict") if k in d}
            if note:
                notes_items[e["item"]] = note
        elif e["kind"] == "skipped":
            d = dict(base, attempted=False, method="not-attempted", result="skipped",
                     verified=False, stopped_reason=e["reason"])
            notes_items[e["item"]] = {"result": "skipped", "attempted": False,
                                      "stopped_reason": e["reason"]}
        else:
            d = dict(base, attempted=False, method="manual-todo", result="todo", verified=False,
                     todo=e["todo"])
            notes_items[e["item"]] = {"result": "todo", "attempted": False, "method": "manual-todo",
                                      "stopped_reason": e["todo"]["blocker"], "todo": e["todo"]}
        items.append(d)
    receipts_bytes = dumps(receipts)
    report = {
        "schema_version": 1, "module_id": module["module_id"], "run_id": run, "started_at": TS,
        "ended_at": TS, "approval_mode": approval_mode,
        "source_answers": {"path": f"../answers/answers-{RUNS[mid][1]}.json",
                           "sha256": sha256(answers_bytes)}}
    if receipts:
        report["source_receipts"] = {"path": f"../runs/{mid}/receipts.json",
                                     "sha256": sha256(receipts_bytes)}
    if user_decisions:
        report["user_decisions"] = user_decisions
    report.update(boundary_reached=boundary, never_navigated=never_navigated,
                  pages_filled=pages_filled, items=items)
    if selector_events:
        report["selector_events"] = selector_events
    if connection_events:
        report["connection_events"] = connection_events
    if wait_rows:
        report["save_waits"] = wait_rows
    notes = {"started_at": TS, "ended_at": TS, "approval_mode": approval_mode,
             "boundary_reached": boundary, "pages_filled": pages_filled,
             "never_navigated": never_navigated}
    if user_decisions:
        notes["user_decisions"] = user_decisions
    notes["items"] = notes_items
    if selector_events:
        notes["selector_events"] = selector_events
    if connection_events:
        notes["connection_events"] = connection_events
    return report, receipts, notes, wait_rows


COMPLETE_ROUTE = [{"route": "complete",
                   "reason": "Final completion controls remain outside automatic navigation."}]
NO_STOP = {"stopped": False, "class": None, "page_index": None, "item_id": None,
           "detail": "Nothing stopped the run; every fillable item was written or listed."}
SINGLE_NO_APPROVAL = {
    "blocker": "Single-attempt item with no recorded approval. The suite writes one only with the "
               "user's approval and never submits it.",
    "placement": "{where}. Type the draft by hand, or record an approval and fill again."}


def wait(pages, signal, waited_ms, total, saved, pending, errors=0, *, calibrated, ok=True,
         fatal=None):
    """One save-wait spec; save_wait_rows() turns it into a record."""
    return dict(pages=pages, signal=signal, waited_ms=waited_ms, total=total, saved=saved,
                pending=pending, errors=errors, calibrated=calibrated, ok=ok, fatal=fatal)


def plans(cx_by_mid, ids):
    """The run plans, one per fill report."""
    def f(item, **kw):
        return dict(item=item, kind="filled", **kw)

    def sk(item, reason):
        return dict(item=item, kind="skipped", reason=reason)

    def td(item, blocker, placement):
        return dict(item=item, kind="todo", todo={"blocker": blocker, "placement": placement})

    out = {}
    out["c1-r101"] = dict(
        plan=[sk(i, "The run is locked, so Lt accepts no writes.")
              for i in ("p2-11", "p2-12", "p2-13", "p2-14", "p3-21", "p3-22", "p4-31", "p4-32")],
        approval_mode="manual", boundary=NO_STOP, pages_filled=[], never_navigated=COMPLETE_ROUTE)
    out["c1-r102"] = dict(
        plan=[f("p2-11"), f("p2-12"), f("p2-13"),
              td("p3-21", SINGLE_NO_APPROVAL["blocker"],
                 SINGLE_NO_APPROVAL["placement"].format(where="Page 3, panel 21")),
              f("p3-22", lt_conflict={
                  "observed_via": "model-answer", "held_answer": "Release changes with the time of day.",
                  "lt_indicated": "Release changes with the cell count.", "draft_ref": "p1",
                  "note": "The invented slide describes daily timing. Keep that sourced claim "
                          "and record the model-answer discrepancy separately."}),
              td("p3-23", "No supported writer handles this rating-scale panel. It requires "
                          "manual entry.",
                 "Enter the draft in panel 23 on page 3 using the browser."),
              f("p4-31"), sk("p4-32", "Captured response already present."),
              sk("p4-33", "Captured response already present.")],
        approval_mode="manual", pages_filled=[2, 3, 4], never_navigated=COMPLETE_ROUTE,
        boundary={"stopped": True, "class": "run-level-commit", "page_index": None, "item_id": None,
                  "detail": "Writable panels are complete. Navigation stops before the final "
                            "completion route.",},
        save_waits=[wait([2], "fallback", 4200, 3, None, None, calibrated=False),
                    wait([3, 4], "model", 520, 2, 2, 0, calibrated=True)])
    out["c1-r103"] = dict(
        plan=[f("p2-11", method="selector", selector="input[type=file][accept*='image/png']"),
              td("p2-12", "The slot is empty, so its image geometry is unknown until a first "
                          "upload is read back.",
                 "Page 2, panel 12. Upload the prepared diagram by hand."),
              td("p2-13", "Annotation on an image Lt supplies has no tested write path.",
                 "Page 2, panel 13. Mark the fold by hand."),
              td("p2-14", "Annotation on an image Lt supplies has no tested write path.",
                 "Page 2, panel 14. Mark the duct by hand."),
              f("p3-21"), f("p3-22"), f("p3-23"),
              td("p4-31", "This drag question allows one attempt. Partial placement risks an "
                          "irreversible change, so manual placement is required.",
                 "Page 4, panel 31. Place the three names by hand.")],
        approval_mode="manual", pages_filled=[2, 3], never_navigated=[], boundary=NO_STOP,
        selector_events=[{"selector": "input[type=file][accept*='image/png']",
                          "item_type": "upload-and-annotate", "outcome": "hit", "recorded_at": TS}],
        connection_events=[{"at": TS, "kind": "screenshot-timeout",
                            "detail": "A screenshot timed out on an annotation page; the run fell "
                                      "through to the view model."}],
        save_waits=[wait([2], "unconfirmed", 8000, 1, 0, 1, calibrated=True),
                    wait([3], "model", 410, 3, 3, 0, calibrated=True)])
    out["c1-r104"] = dict(
        plan=[sk("p3-12", "Submission is already recorded and this panel is locked."),
              sk("p3-13", "The page is locked after the committed checkpoint."),
              sk("p3-14", "The page is locked after the committed checkpoint.")]
             + [f(f"p9-{71 + n}") for n in range(15)],
        approval_mode="auto", pages_filled=[9], never_navigated=COMPLETE_ROUTE,
        boundary={"stopped": True, "class": "run-level-commit", "page_index": None, "item_id": None,
                  "detail": "Team selections are saved. Navigation stops before the final "
                            "completion route.",},
        user_decisions=[
            {"rail": "approval-manual-at-boundary", "who": "the user", "when": TS,
             "why": "Synthetic approval permits automatic entry of team selections before "
                    "the completion route.",
             "approval_id": ids["manual"], "quote": APPROVAL_QUOTES["manual"]},
            {"rail": "passed-single-approval", "item_ids": ["p3-12"], "who": "the user", "when": TS,
             "why": "The submitted item on page 3 is already locked; the user accepted a run "
                    "that reaches page 9 without manual approval.",
             "approval_id": ids["passed"], "quote": APPROVAL_QUOTES["passed"]}],
        save_waits=[wait([9], "net", 910, 15, 15, 0, calibrated=True)])
    out["c1-r106"] = dict(
        plan=[f("p2-11"), f("p2-12")], approval_mode="manual", pages_filled=[2],
        never_navigated=COMPLETE_ROUTE,
        boundary={"stopped": True, "class": "checkpoint-page", "page_index": 3, "item_id": None,
                  "detail": "Page 3 is an uncommitted checkpoint, a one-way door. The run stopped "
                            "before it."},
        save_waits=[wait([2], "error", 1500, 2, 1, 0, 1, calibrated=True, ok=False,
                         fatal="One save request failed; the page was filled again."),
                    wait([2], "model", 300, 2, 2, 0, calibrated=True)])
    out["c1-r107"] = dict(
        plan=[f("p1-11"),
              td("p1-12", "The slot is empty, so its image geometry is unknown until a first "
                          "upload is read back.",
                 "Page 1, panel 12. Upload the prepared sketch by hand."),
              f("p1-14"), sk("p1-16", "Notebook panel: the suite never writes to it."),
              sk("p2-21", "Team readiness test; the answers have not been supplied."),
              sk("p2-22", "Team readiness test; the answers have not been supplied."),
              sk("p2-23", "Team readiness test; the answers have not been supplied.")],
        approval_mode="manual", pages_filled=[1], never_navigated=[], boundary=NO_STOP,
        save_waits=[wait([1], "net", 450, 2, 2, 0, calibrated=True)])
    return out


# ---------------------------------------------------------------- run records -----------

RUN_FACTS = {
    "c1-r101": (["lt-module-capture", "lt-module-answer", "lt-module-export"], "complete", []),
    "c1-r102": (["lt-module-capture", "lt-module-answer", "lt-module-fill"], "stopped-at-boundary",
                [{"item_id": "p3-22", "class": "style",
                  "before": "Release changes with the time of day.",
                  "after": "Release changes with time of day.", "steered_run_wrong": False,
                  "note": "Wording only. Proposes no skill edit."}]),
    "c1-r103": (["lt-module-capture", "lt-module-answer", "mechanistic-diagram", "lt-module-fill"],
                "degraded",
                [{"item_id": "p2-11", "class": "provenance", "before": f"[T4:{DOI}]",
                  "after": "removed", "steered_run_wrong": True,
                  "note": "The draft cited a paper for a point the figure already shows. "
                          "Cite deeper tiers only when the prompt asks for the depth."}]),
    "c1-r104": (["lt-module-capture", "lt-module-answer", "lt-module-fill"], "stopped-at-boundary",
                [{"item_id": "p9-72", "class": "factual", "before": "Store C holds quillic acid.",
                  "after": "Store C holds quillic acid and orm salt.", "steered_run_wrong": True,
                  "note": "The draft named one substance where the slide names two."}]),
    "c1-r106": (["lt-module-capture", "lt-module-answer", "lt-module-fill"], "stopped-at-boundary", []),
    "c1-r107": (["lt-module-capture", "lt-module-answer", "lt-module-fill"], "degraded", []),
}


def run_record(mid, module):
    skills, outcome, diffs = RUN_FACTS[mid]
    n = len(skills)
    rec = {"schema_version": 1, "run_ts": TS, "module_id": mid, "tool_version": TOOL_VERSION,
           "config_digest": CONFIG_DIGEST, "institution_profile": "generic", "unit_code": UNIT,
           "skills_invoked": skills,
           "timings": {s: 10000 * (i + 1) for i, s in enumerate(skills)},
           "token_cost": {s: 1000 * (i + 2) for i, s in enumerate(skills)},
           "question_type_census": module["census"]["by_type"]}
    if mid == "c1-r103":
        rec["selector_events"] = [{"selector": "input[type=file][accept*='image/png']",
                                   "item_type": "upload-and-annotate", "outcome": "hit"}]
    if diffs:
        rec["diff_classification"] = diffs
    rec["outcome"] = outcome
    rec["notes"] = "Synthetic fixture record."
    assert n >= 3
    return rec


# ---------------------------------------------------------------- media ------------------

def media_names(mid, module):
    names = {}
    for pg in module["pages"]:
        for holder in pg["content"] + pg["items"]:
            for m in holder.get("media") or []:
                if m.get("key"):
                    prefix = "u" if m.get("role") == "student-upload" else ""
                    names[f"lt-{mid}-{prefix}{m['key']}.png"] = (mid, prefix, m["key"])
    return names


# ---------------------------------------------------------------- the whole tree ---------

def build_all(seed: int):
    """{relative path under tests/fixtures: bytes} for every generated file."""
    rng = Rng(seed)
    files: dict[str, bytes] = {}
    specs = {mid: b() for mid, b in SPEC_BUILDERS.items()}
    derived = {mid: derive(sp) for mid, sp in specs.items()}
    cxs = {mid: Ctx(specs[mid], derived[mid][0], rng) for mid in specs}
    ids, found = approvals_for_r104(cxs["c1-r104"].module)
    files["runs/c1-r104/approvals.json"] = dumps(run_approvals(ids, cxs["c1-r104"].module))

    # modules, view models, references, the module index
    index = {}
    for mid, (module, reference, mirrors) in derived.items():
        stem = RUNS[mid][1]
        index[mid] = f"module-{stem}.json"
        files[f"modules/module-{stem}.json"] = dumps(module)
        files[f"viewmodels/{mid}.json"] = dumps(specs[mid])
        if mid == "c1-r101":
            for it in reference["items"]:
                if it["item_id"] in ("p2-11", "p2-12"):
                    it.update(given_grade=3, grading_feedback="Accept either mechanism.")
        files[f"references/reference-{stem}.json"] = dumps(reference)
    files["modules/index.json"] = dumps({
        "note": "module id -> module file. c1-r107 is the demo run, stored as module-demo.json.",
        "modules": index})

    # mirror expectations: written from the page design, then checked against the derivation
    exp = {}
    for mid, (module, _ref, mirrors) in derived.items():
        e = EXPECTED_MIRRORS.get(mid, {"pairs": [], "near_duplicates": []})
        want = sorted([m for _s, m, _b in e["pairs"] + e["near_duplicates"]])
        assert sorted(mirrors) == want, (mid, mirrors, want)
        for s, m, basis in e["pairs"] + e["near_duplicates"]:
            mo = cxs[mid].item(m)["mirror_of"]
            assert mo["item_id"] == s and mo["basis"] == basis, (mid, m, mo)
        exp[mid] = {k: [{"source": s, "mirror": m, "basis": b} for s, m, b in e[k]]
                    for k in ("pairs", "near_duplicates")}
    files["modules/expected-mirrors.json"] = dumps({"modules": exp})

    # answers
    builders = {"c1-r101": answers_r101, "c1-r102": answers_r102, "c1-r103": answers_r103,
                "c1-r105": answers_r105, "c1-r106": answers_r106, "c1-r107": answers_r107}
    digest = load_digest()
    answers_bytes = {}
    for mid in specs:
        stem = RUNS[mid][1]
        cx = cxs[mid]
        corpus, items = (answers_r104(cx, ids["single"]) if mid == "c1-r104"
                         else builders[mid](cx))
        module_bytes = files[f"modules/module-{stem}.json"]
        doc = {"schema_version": 2, "module_id": mid, "run_id": RUNS[mid][0], "drafted_at": TS,
               "source_module": {"path": f"../modules/module-{stem}.json",
                                 "sha256": sha256(module_bytes),
                                 "question_digest": digest(cx.module)},
               "tier1_corpus": corpus, "items": items}
        b = dumps(doc)
        answers_bytes[mid] = b
        files[f"answers/answers-{stem}.json"] = b

    # fill reports, receipts, notes, run records
    for mid, p in plans(cxs, ids).items():
        stem = RUNS[mid][1]
        report, receipts, notes, wait_rows = fill_report(
            mid, cxs[mid], answers_bytes[mid], p["plan"], approval_mode=p["approval_mode"],
            boundary=p["boundary"], pages_filled=p["pages_filled"],
            never_navigated=p["never_navigated"], rng=rng, user_decisions=p.get("user_decisions"),
            selector_events=p.get("selector_events"), connection_events=p.get("connection_events"),
            save_waits=p.get("save_waits"))
        files[f"fill-reports/fill-report-{stem}.json"] = dumps(report)
        if receipts:
            files[f"runs/{mid}/receipts.json"] = dumps(receipts)
        if wait_rows:
            files[f"runs/{mid}/save-waits.json"] = dumps({"save_waits": wait_rows})
        files[f"runs/{mid}/fill-notes.json"] = dumps(notes)
    for mid in RUN_RECORD_MODULES:
        files[f"runs/{mid}/run.json"] = dumps(run_record(mid, derived[mid][0]))

    # media
    for mid, (module, _r, _m) in derived.items():
        for name, (_mid, _prefix, _key) in sorted(media_names(mid, module).items()):
            files[f"media/{name}"] = make_png(rng.fork(f"png:{name}"))

    # curriculum and the exported page
    for rel, text in CURRICULUM.items():
        files[f"curriculum/{rel}"] = text.encode("utf-8")
    files["pages/page.md"] = PAGE_MD.encode("utf-8")
    assert PAGE_MD.count("\n") == 14, PAGE_MD.count("\n")
    return files, {"approval_ids": ids, "approvals_in_file": found}


def load_digest():
    """question_digest from ltcore.digest (the one definition), so the pins match the validator."""
    sys.path.insert(0, str(SHARED))
    try:
        from ltcore.digest import question_digest
        return question_digest
    except ImportError as e:
        raise SystemExit(f"error: ltcore.digest is needed for the question_digest pins: {e}")
    finally:
        sys.path.pop(0)


# ---------------------------------------------------------------- checks -----------------

FORBIDDEN = [
    (re.compile(r"/home/|~/|C:\\\\Users"), "a personal path"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "an email address"),
    (re.compile(r"\b(?!2000-01-01\b)\d{4}-\d{2}-\d{2}\b"), "a date other than 2000-01-01"),
    (re.compile(r"\b[DF]\d+(\.\d+)?\b|\bPhase \d|PROMPT|SPEC\b"), "a private-history reference"),
]


def scan_text(files: dict[str, bytes]) -> list[str]:
    """Every text file must hold only the invented values."""
    out = []
    for rel, data in sorted(files.items()):
        if rel.endswith(".png"):
            continue
        text = data.decode("utf-8")
        for rx, what in FORBIDDEN:
            m = rx.search(text)
            if m:
                out.append(f"{rel}: {what}: {m.group(0)!r}")
    return out


def write_tree(files: dict[str, bytes], root: Path) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def owned_files(root: Path) -> set[str]:
    out = set()
    for d in OWNED_DIRS:
        base = root / d
        if base.is_dir():
            for p in base.rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store":
                    out.add(p.relative_to(root).as_posix())
    return out


def diff_tree(files: dict[str, bytes], root: Path) -> list[str]:
    out = []
    for rel, data in sorted(files.items()):
        p = root / rel
        if not p.is_file():
            out.append(f"missing  {rel}")
        elif p.read_bytes() != data:
            out.append(f"changed  {rel}")
    for rel in sorted(owned_files(root) - set(files) - HAND_WRITTEN):
        out.append(f"orphan   {rel}")
    return out


def load_validator():
    sys.path.insert(0, str(SHARED))
    try:
        from ltcore import validate
        return validate
    except ImportError:
        return None
    finally:
        sys.path.pop(0)


def validate_tree(root: Path, approvals_found: dict) -> list[str]:
    """Run ltcore.validate over every generated document. Answers and fill reports are
    checked against their module and the generated run's complete approval records."""
    v = load_validator()
    if v is None:
        return ["ltcore.validate is not importable; validation could not run"]
    appr = root / "runs" / "c1-r104" / "approvals.json"
    fails = []
    kinds = (("modules", "module-*.json", None), ("references", "reference-*.json", None),
             ("answers", "answers-*.json", "module"), ("fill-reports", "fill-report-*.json", "module"),
             ("runs", "*/run.json", None), ("runs", "*/approvals.json", None))
    for d, pat, needs_module in kinds:
        for p in sorted((root / d).glob(pat)):
            kw = {"model_answer_read": True}
            stem = None
            if d == "answers":
                stem = p.name[len("answers-"):-len(".json")]
            elif d == "fill-reports":
                stem = p.name[len("fill-report-"):-len(".json")]
            if needs_module:
                kw["module"] = root / "modules" / f"module-{stem}.json"
            if appr.is_file() and stem == "c1-r104":
                kw["approvals"] = appr
            res = v.validate_file(p, **kw)
            if res.exit_code != 0:
                detail = res.fatal or "; ".join(res.schema_errors + res.rail_errors)
                fails.append(f"{p.relative_to(root).as_posix()}: exit {res.exit_code}: {detail[:400]}")
    # Schema examples and the original approval fixture are inputs owned elsewhere.
    # Check them in place without repinning, copying back or changing their contents.
    for directory in ("examples", "approvals"):
        for p in sorted((FIXTURES / directory).glob("*.json")):
            kw = {"model_answer_read": True}
            if p.name in ("answers.example.json", "fill-report.example.json"):
                kw["module"] = FIXTURES / "examples" / "module.example.json"
            res = v.validate_file(p, **kw)
            if res.exit_code != 0:
                detail = res.fatal or "; ".join(res.schema_errors + res.rail_errors)
                fails.append(f"{directory}/{p.name}: exit {res.exit_code}: {detail[:400]}")
    return fails


def reading_notes(info: dict) -> list[str]:
    ids, found = info["approval_ids"], info["approvals_in_file"]
    missing = [k for k, ok in found.items() if k == "single" and not ok]
    if not missing:
        return []
    return [f"note: tests/fixtures/approvals/approvals-c1-r104.json has no record for "
            f"{', '.join(missing)}; computed ids are used ({', '.join(f'{k}={ids[k]}' for k in missing)})"]


# ---------------------------------------------------------------- main -------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write or check the synthetic fixtures under tests/fixtures.")
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT,
                    help=f"fixture seed (default {SEED_DEFAULT}; --check compares against it)")
    ap.add_argument("--out", help="write the tree here instead of tests/fixtures")
    ap.add_argument("--check", action="store_true",
                    help="regenerate into a temp folder and compare with tests/fixtures")
    ap.add_argument("--no-validate", action="store_true", help="skip ltcore.validate")
    ap.add_argument("--list", action="store_true", help="print the generated paths and exit")
    args = ap.parse_args(argv)

    files, info = build_all(args.seed)
    bad = scan_text(files)
    if bad:
        print("error: generated text holds a value that must not ship:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    if args.list:
        print("\n".join(sorted(files)))
        return 0
    for n in reading_notes(info):
        print(n, file=sys.stderr)

    if args.check:
        with tempfile.TemporaryDirectory(prefix="gen-fixtures-") as td:
            tmp = Path(td)
            write_tree(files, tmp)
            fails = [] if args.no_validate else validate_tree(tmp, info["approvals_in_file"])
        drift = diff_tree(files, FIXTURES)
        for line in drift:
            print(line)
        for line in fails:
            print(f"invalid  {line}")
        if drift or fails:
            print(f"FAIL: {len(drift)} drifted path(s), {len(fails)} invalid file(s). "
                  f"Run python3 tools/gen_fixtures.py to rewrite tests/fixtures.")
            return 1
        print(f"OK: {len(files)} generated files match tests/fixtures"
              + ("" if args.no_validate else " and validate"))
        return 0

    root = Path(args.out) if args.out else FIXTURES
    if args.out:
        root.mkdir(parents=True, exist_ok=True)
    orphans = sorted(owned_files(root) - set(files) - HAND_WRITTEN)
    if orphans:
        for rel in orphans:
            print(f"orphan   {rel}")
        print("FAIL: unrecognised files remain unchanged; review them before regenerating.")
        return 1
    write_tree(files, root)
    fails = [] if args.no_validate else validate_tree(root, info["approvals_in_file"])
    for line in fails:
        print(f"invalid  {line}")
    print(f"wrote {len(files)} files under {root}" + (f"; {len(fails)} invalid" if fails else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
