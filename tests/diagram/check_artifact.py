#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Structural checks on a built interactive page (no browser):

    python3 tests/diagram/check_artifact.py <page.html> [--epoch SOURCE_DATE_EPOCH] [--palettes N]

- a complete standalone document: doctype, charset and viewport meta tags, a title;
- the HTML parses with html.parser and every non-void element closes in order (SVG included);
- no id appears twice;
- the figure data (#fig-data) parses, and in each inline SVG every data-node, every edge end and every stage
  resolves against it (and back): boxes, arrows in edge-I order, stage containers;
- every element id the page script asks for (artifact_ui.js: $('...')) exists;
- scripts: inline only (the page loads no external script), no alert/confirm/prompt, no host-specific save
  interface, and each parses (node --check, when node is installed);
- one figure per palette, and the downloads are plain <a download> links with data URIs: one PDF per palette and a
  zip holding one .drawio per palette; with --epoch, the zip entries carry that date.
Exits non-zero with a list of problems.
"""
import argparse, base64, html.parser, io, json, pathlib, re, subprocess, sys, tempfile, time, zipfile

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
ENGINE = pathlib.Path(__file__).resolve().parent.parent.parent / "skills" / "mechanistic-diagram" / "engine"
DATA_URI = re.compile(r"data:([a-z/+.-]+);base64,([A-Za-z0-9+/=]*)")


class Strict(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.problems, self.ids, self.elements = [], [], {}, []
        self.scripts, self._script = [], None

    def _attrs(self, tag, attrs):
        d = dict(attrs)
        self.elements.append((tag, d, list(self.stack)))
        if "id" in d:
            self.ids[d["id"]] = self.ids.get(d["id"], 0) + 1
        return d

    def handle_starttag(self, tag, attrs):
        d = self._attrs(tag, attrs)
        if tag == "script":
            self._script = [d, ""]
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self._attrs(tag, attrs)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(f"line {self.getpos()[0]}: </{tag}> closes <{self.stack[-1] if self.stack else 'nothing'}>")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
            return
        self.stack.pop()
        if tag == "script" and self._script is not None:
            self.scripts.append(tuple(self._script))
            self._script = None

    def handle_data(self, data):
        if self._script is not None:
            self._script[1] += data


def check(page, epoch=None, palettes=None):
    """Problems found in a built page (empty: it passes). palettes: the expected number of palettes."""
    import shutil
    text = pathlib.Path(page).read_text(encoding="utf-8")
    head = text[:3000].lower()
    bad = []
    if not head.lstrip().startswith("<!doctype html>"):
        bad.append("not a complete document: no <!doctype html> at the start")
    for what, pat in (("charset", r"<meta charset=\"?utf-8"), ("viewport", r"<meta name=\"viewport\""),
                      ("title", r"<title>[^<]+</title>")):
        if not re.search(pat, head):
            bad.append(f"no {what} in the document head")
    if "window.claude" in text:
        bad.append("the page calls a host-specific save interface")
    P = Strict()
    P.feed(text)
    P.close()
    bad += list(P.problems)
    if P.stack:
        bad.append(f"unclosed at end: {P.stack}")
    bad += [f"id {i!r} appears {n} times" for i, n in P.ids.items() if n > 1]

    data_scripts = [s for d, s in P.scripts if d.get("id") == "fig-data"]
    if len(data_scripts) != 1:
        return bad + ["no single #fig-data script"]
    D = json.loads(data_scripts[0])
    nodes, edges, stages = D["nodes"], D["edges"], {s["id"] for s in D["stages"]}
    svgs = [i for i, (tag, d, _) in enumerate(P.elements) if tag == "svg" and "fig" in d.get("class", "").split()]
    if palettes is not None and len(svgs) != palettes:
        bad.append(f"expected {palettes} figures for the configured palettes (style.palettes), found {len(svgs)}")
    if not svgs:
        bad.append("no figure")
    for k, start in enumerate(svgs):
        end = svgs[k + 1] if k + 1 < len(svgs) else len(P.elements)
        inside = [(t, d) for t, d, stack in P.elements[start + 1:end] if "svg" in stack]
        seen_nodes, seen_edges, seen_stages = [], [], []
        for tag, d in inside:
            cls = d.get("class", "").split()
            if tag == "g" and "node" in cls:
                seen_nodes.append(d.get("data-node"))
                n = nodes.get(d.get("data-node"))
                if n is None:
                    bad.append(f"figure {k}: data-node {d.get('data-node')!r} is not in the figure data")
                elif n["r"] != d.get("data-role") or n["s"] != d.get("data-stage"):
                    bad.append(f"figure {k}: {d.get('data-node')} role/stage differ from the figure data")
            elif tag == "g" and "edge" in cls:
                seen_edges.append((d.get("data-a"), d.get("data-b"), d.get("data-kind")))
            elif tag == "g" and "stage" in cls:
                seen_stages.append(d.get("data-stage"))
            for att in ("data-node", "data-a", "data-b"):
                if att in d and d[att] not in nodes:
                    bad.append(f"figure {k}: <{tag}> {att}={d[att]!r} does not resolve")
            if "data-stage" in d and d["data-stage"] not in stages:
                bad.append(f"figure {k}: <{tag}> data-stage={d['data-stage']!r} does not resolve")
        if sorted(seen_nodes) != sorted(nodes):
            bad.append(f"figure {k}: boxes differ from the figure data")
        if seen_edges != [(e["a"], e["b"], e["k"]) for e in edges]:
            bad.append(f"figure {k}: arrows differ from the figure data (order matters: edge-I is edges[I])")
        if set(seen_stages) != stages:
            bad.append(f"figure {k}: stage containers differ from the figure data")

    ui = (ENGINE / "artifact_ui.js").read_text(encoding="utf-8")
    wanted = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", ui)) | set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", ui))
    made = set(re.findall(r"\.id = '([A-Za-z0-9_-]+)'", ui))          # ids the script gives elements it creates
    bad += [f"the page script asks for #{i}, which the page lacks" for i in sorted(wanted - set(P.ids) - made)]

    for d, src in P.scripts:
        if "src" in d:
            bad.append(f"external script {d['src']}")
        if d.get("type") == "application/json":
            continue
        if re.search(r"\b(alert|confirm|prompt)\s*\(", src):
            bad.append("a script calls alert/confirm/prompt")
        if shutil.which("node"):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8", newline="\n") as f:
                f.write(src)
            r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True, encoding="utf-8")
            pathlib.Path(f.name).unlink()
            if r.returncode:
                bad.append("a script does not parse: " + r.stderr.strip().splitlines()[-1])
    for tag, d, _ in P.elements:
        for att in ("src", "href"):
            v = d.get(att) or ""
            if re.match(r"(?i)https?:", v) and not v.startswith("https://fonts.googleapis.com/"):
                bad.append(f"<{tag} {att}={v[:60]!r}>: the page loads nothing from the network")

    links = [d for tag, d, _ in P.elements if tag == "a" and "dl" in (d.get("class") or "").split()]
    pdfs, zips = [], []
    for d in links:
        m = DATA_URI.fullmatch(d.get("href") or "")
        if not m or not d.get("download"):
            bad.append(f"download link {d.get('id')!r} is not a plain <a download> with a data URI")
            continue
        (pdfs if m.group(1) == "application/pdf" else zips).append((d, base64.b64decode(m.group(2))))
    n = len(svgs) if palettes is None else palettes
    if len(pdfs) != n or len(zips) != 1:
        bad.append(f"expected {n} PDF downloads and one zip, found {len(pdfs)} and {len(zips)}")
    for d, data in pdfs:
        if not data.startswith(b"%PDF"):
            bad.append(f"download {d.get('download')!r} is not a PDF")
    for d, data in zips[:1]:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        if len(names) != n or not all(x.endswith(".drawio") for x in names):
            bad.append(f"zip payload holds {names}")
        elif epoch is not None:
            want = time.gmtime(max(int(epoch), 315532800))[:6]
            for info in z.infolist():
                if tuple(info.date_time) != tuple(want[:5]) + (want[5] // 2 * 2,):
                    bad.append(f"zip entry {info.filename} date {info.date_time}, expected {want} from SOURCE_DATE_EPOCH")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("page")
    ap.add_argument("--epoch")
    ap.add_argument("--palettes", type=int, help="the number of configured palettes (default: as many as figures)")
    a = ap.parse_args()
    bad = check(a.page, a.epoch, a.palettes)
    for b in bad:
        print("PROBLEM:", b)
    print("ARTIFACT CHECK", "FAILED" if bad else "PASSED")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
