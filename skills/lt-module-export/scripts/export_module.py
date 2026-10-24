# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Export captured Lt modules into notes, assets, handoffs, and optional PDFs.

Usage:
    export_module.py --module <path-or-id> [--answers <path>] [--install] [--replace]

Outputs include a module page, assets, optional handoffs, and PDFs.
A run record stores the export outcome and configuration digest.

Every installed notes page carries a configurable module ownership stamp.
Existing graph targets require proven ownership and explicit replacement before changes.
Installation places only pages, whiteboards, and assets inside the notes graph.
Replacement preserves unified diffs that show changes against existing text.

Exit codes:
    0  Built successfully, and installed when installation was requested.
    1  A binding, week, identifier, page path, or PDF rail failed.
    2  An input or run record failed schema or rail validation.
    3  Usage, file, configuration, dependency, or validator availability failed.
       Installation also requires graph output and a configured graph path.
    4  A target blocked installation; nothing outside the output directory changed.
    5  Installation was refused because LT_SUITE_NO_INSTALL was set.
"""
from __future__ import annotations

import sys
if sys.version_info < (3, 11):
    sys.exit("lt-suite needs Python 3.11 or newer. Run the setup skill's bootstrap step: python3 <setup skill>/scripts/setup.py bootstrap")
from pathlib import Path
SELF = Path(__file__).resolve().parent
sys.path.insert(0, str(SELF / "_shared"))
sys.path.insert(0, str(SELF))
import ltcore; _MISSING = ltcore.bootstrap()

import argparse
import contextlib
import datetime as dt
import difflib
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import unicodedata
import urllib.parse
import uuid

from ltcore import config as config_mod, store as store_mod, page_kinds, rat
from pdf_base import SELF_REFERENCE_TEXT


RESERVED = '<>:"\\|?*'
AUTOLINK_MIN_LENGTH = 5
AUTOLINK_FIRST_OCCURRENCE_ONLY = True
AUTOLINK_STOPLIST = [
    "contents", "notes", "todo", "ocr", "title", "testing", "index", "home",
]
WHITEBOARD_PORTAL_SIZE = [400, 320]
WHITEBOARD_GRID_COLS = 4
WHITEBOARD_GRID_GAP = [80, 80]
WHITEBOARD_ORIGIN = [100, 100]
WHITEBOARD_SCALE_LEVEL = "md"
CITATION_URL_TEMPLATES = {
    "pmid": "https://pubmed.ncbi.nlm.nih.gov/{id}/",
    "doi": "https://doi.org/{id}",
}
OUTPUT_NAMES = (
    "module_page", "pdf", "notes_graph", "whiteboard",
    "ilo_map", "flashcard_source", "tracker_handoff",
)
RESERVED_STAMP_PROPERTIES = {
    "title", "type", "tags", "lt-run", "lt-source", "lt-captured",
    "lt-exported", "lt-exported-by",
}
PDF_DEPENDENCIES = {"reportlab", "PIL", "fontTools", "pypdfium2"}
PMID_RE = re.compile(r"^PMID:\s*(\d+)$", re.I)
DOI_RE = re.compile(r"^(10\.\d{4,9}/\S+)$")
VOLATILE_RE = re.compile(r"^lt-exported(?:::|:).*$", re.MULTILINE)
# A learning outcome is a bullet or a numbered line at the start of the line.
OUTCOME_LINE_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.+?)\s*$")


def expand(p) -> Path:
    return Path(os.path.expanduser(str(p)))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def now_iso() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is not None:
        return dt.datetime.fromtimestamp(int(epoch), dt.timezone.utc).isoformat(timespec="seconds")
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def triple_lowbar(name: str) -> str:
    out = name.replace("/", "___")
    return "".join(
        f"%{ord(c):02X}" if c in RESERVED or ord(c) < 32 else c
        for c in out
    )


def legacy_name(name: str) -> str:
    out = name.replace("/", ".")
    return "".join(
        f"%{ord(c):02X}" if c in RESERVED or ord(c) < 32 else c
        for c in out
    )


def from_triple_lowbar(stem: str) -> str:
    return urllib.parse.unquote(stem.replace("___", "/"))


def sanitise_part(part: str) -> str:
    part = "".join(
        "-" if c in RESERVED or ord(c) < 32 else c
        for c in part
    )
    part = part.strip(" .")
    return "_" if not part or part == ".." else part


def output_filename(value: str) -> str:
    """Require a single filename before any normalization can hide separators."""
    if (not value or value in {".", ".."} or Path(value).is_absolute()
            or "/" in value or "\\" in value or any(ord(c) < 32 for c in value)):
        raise ValueError("configured output name must be a single filename without path separators")
    return value


def staging_path(root: Path, path: Path) -> Path:
    """Check resolved containment immediately before using a staging target."""
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("output target escapes --out-dir")
    return path


def literal_caption(value: str) -> str:
    """Keep captured captions on one line and encode Markdown delimiters literally."""
    text = html.escape(" ".join(str(value).split()), quote=True)
    return "".join(f"&#{ord(c)};" if c in "\\[]()!`*_{}#|" else c for c in text)


def verify_page_stamp(body: str, prop: str, module_id: str) -> None:
    """Require the exact property line recognized by the shared quarantine scanner."""
    pattern = rf"^{re.escape(prop)}::[^\S\n]*{re.escape(module_id)}[^\S\n]*$"
    if not re.search(pattern, body, re.MULTILINE):
        raise ValueError("generated Markdown page lacks a recognizable quarantine stamp")


def safe_page_path(pages_dir, title) -> Path:
    pages_dir = Path(pages_dir)
    parts = [sanitise_part(part) for part in str(title).split("/")]
    path = pages_dir.joinpath(*parts[:-1], parts[-1] + ".md")
    if not path.resolve().is_relative_to(pages_dir.resolve()):
        raise ValueError("page title escapes the pages folder")
    return path


def page_file(graph, cfg, page_title) -> Path:
    pages_dir = Path(graph) / str(cfg.get("notes.pages_dir"))
    if cfg.get("notes.format") == "logseq":
        naming = cfg.get("notes.file_name_format")
        name = legacy_name(page_title) if naming == "legacy" else triple_lowbar(page_title)
        return pages_dir / f"{name}.md"
    return safe_page_path(pages_dir, page_title)


def graph_directory(graph: Path, value) -> Path:
    path = graph / str(value)
    if not path.resolve().is_relative_to(graph.resolve()):
        raise ValueError("configured notes directory escapes the notes graph")
    return path


def heading(text: str | None) -> str:
    # Heading whitespace collapses because nonbreaking spaces otherwise create false edit differences.
    return re.sub(r"\s+", " ", text or "").strip()


def norm(s: str) -> str:
    """Normalise Unicode, punctuation, and whitespace for consistent title comparisons."""
    s = unicodedata.normalize("NFKD", s or "")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip().casefold()


def question_digest_of(module_doc):
    """Use the shared question digest when its implementation can be loaded."""
    try:
        from ltcore import digest as digest_mod
    except ImportError:
        return None
    try:
        return digest_mod.question_digest(module_doc)
    except Exception:
        return None


def stamp_regexes(prop):
    prop = re.escape(prop)
    return (
        re.compile(rf"^{prop}::\s*(\S+)\s*$", re.MULTILINE),
        re.compile(rf'^{prop}:\s*"?([^"\s]+)"?\s*$', re.MULTILINE),
        re.compile(rf"^<!--\s*{prop}:\s*(\S+)\s*-->\s*$", re.MULTILINE),
    )


def make_probe_markdown(prop):
    from ltcore.stamp import stamp_regexes
    regexes = stamp_regexes(prop)

    def probe_markdown(path: Path, blob: bytes) -> str | None:
        text = blob.decode("utf-8", "replace")
        for rx in regexes:
            match = rx.search(text)
            if match:
                return match.group(1)
        return None

    return probe_markdown


def make_probe_whiteboard(namespace):
    rx = re.compile(
        rf':block/original-name\s+"{re.escape(namespace)}-([^"]+)"'
    )

    def probe_whiteboard(path: Path, blob: bytes) -> str | None:
        match = rx.search(blob.decode("utf-8", "replace"))
        return match.group(1) if match else None

    return probe_whiteboard


def make_probe_asset(prefix: str, module_id: str):
    """Recognise asset ownership through the module prefix in its filename."""
    def probe(path: Path, blob: bytes) -> str | None:
        return module_id if path.name.startswith(prefix + "-") else None

    return probe


def lesson_week_title(module, cfg) -> tuple[str | None, str]:
    raw = (module.get("lesson") or {}).get("title") or ""
    match = re.compile(cfg.get("curriculum.week_number_pattern")).search(raw)
    if match:
        week = ".".join(group for group in match.groups() if group)
        title = raw[:match.start()] + raw[match.end():]
        title = re.sub(r"^[\s:\-.|–—]+|[\s:\-.|–—]+$", "", title)
        title = title or raw.strip()
    else:
        week = None
        title = re.sub(
            r"^\s*(?:module\s+)?\d{1,2}\s*[.:–—-]\s*",
            "",
            raw,
            count=1,
            flags=re.I,
        ).strip() or raw.strip()

    label = re.compile(cfg.get("curriculum.patterns.lesson")).match(title)
    if label:
        title = title[label.end():].strip()
    if re.fullmatch(cfg.get("curriculum.patterns.bare_number"), title):
        title = f"Module {title}"
    return week, title


def excluded_page(page, rat_verdict=None) -> str | None:
    """Exclude report pages and assessment pages lacking an exportable classification."""
    if page.get("is_report_page"):
        return "report-page"
    if page.get("policy_skip"):
        return f"policy-skip:{page['policy_skip'].get('reason')}"
    status = page.get("_rat_status")
    if status:
        decision = status["decision"]
        if decision == "fill-from-sheet":
            return "rat-sheet"
        if status["rat"] is not False and decision != "fill-from-draft":
            if not (page.get("_rat_verdict") == "trat" and status["rat"] is None):
                return "irat-policy" if status["kind"] == "irat" else "rat-unclassified"
    if page.get("rat_candidate") and not (status and status["rat"] is False):
        rat_verdict = rat_verdict if rat_verdict is not None else page.get("_rat_verdict")
        if rat_verdict == "irat":
            return "irat-policy"
        if rat_verdict == "trat-answers-unavailable":
            return "policy-skip:trat-answers-unavailable"
        if rat_verdict != "trat":
            return "rat-unclassified"
    return None


def rat_verdict_from_answers(page: dict, ai: dict, override: str | None) -> str | None:
    """Derive assessment classification from drafted answers, skip reasons, or an override."""
    if override:
        return override
    ids = [it["item_id"] for it in page.get("items", [])]
    seen = [ai[i] for i in ids if i in ai]
    if not seen:
        return None
    if any(a.get("status") == "drafted" for a in seen):
        return "trat"
    reasons = {a.get("skip_reason") for a in seen if a.get("status") == "skipped"}
    if reasons == {"irat-policy"}:
        return "irat"
    if reasons == {"trat-answers-unavailable"}:
        return "trat-answers-unavailable"
    return None


def module_items(module) -> list[dict]:
    """Return included items in capture order with their page context attached."""
    out = []
    for page in module.get("pages", []):
        if excluded_page(page):
            continue
        for it in page.get("items", []):
            row = dict(it)
            row["_page_index"] = page.get("page_index")
            row["_page_title"] = page.get("title") or ""
            out.append(row)
    return out


def parse_ilo_section(ilo_path, week, title, cfg) -> dict | None:
    """Choose an outcome section only when title matching identifies one unambiguously."""
    ilo_path = Path(ilo_path)
    if not ilo_path.exists():
        return None
    if any(rat.within(ilo_path, root) for _, root in rat.effective_roots(rat.policy_from_config(cfg))):
        raise ValueError("learning_outcomes_file is inside a quarantined RAT root")
    lines = ilo_path.read_text(encoding="utf-8", errors="replace").splitlines()
    h_re = re.compile(cfg.get("curriculum.patterns.ilo_heading"))
    kind_pattern = cfg.get("curriculum.patterns.ilo_kind")
    kind_re = re.compile(kind_pattern) if kind_pattern else None
    sections = []
    for index, line in enumerate(lines):
        match = h_re.match(line)
        if not match:
            continue
        groups = match.groups()
        section_week = groups[0] if len(groups) >= 2 else None
        if len(groups) >= 2:
            section_title = groups[1] or ""
        elif groups:
            section_title = groups[0] or ""
        else:
            section_title = line.lstrip("#").strip()
        if len(groups) >= 2 and section_week != week:
            continue
        level = len(line) - len(line.lstrip("#"))
        sections.append((index, section_title, level))
    if not sections:
        return None

    want = set(norm(title).split())
    exact = [section for section in sections if norm(section[1]) == norm(title)]
    if len(exact) == 1:
        chosen = exact[0]
    elif h_re.groups == 0 and len(sections) == 1:
        chosen = sections[0]      # the heading carries no week or title, and there is one
    else:
        scored = sorted(
            sections,
            key=lambda section: len(set(norm(section[1]).split()) & want),
            reverse=True,
        )
        best = len(set(norm(scored[0][1]).split()) & want)
        runner = (
            len(set(norm(scored[1][1]).split()) & want)
            if len(scored) > 1 else -1
        )
        if best <= 0 or best == runner:
            return None
        chosen = scored[0]

    start, section_title, level = chosen
    kind = discipline = None
    outcomes = []
    for line in lines[start + 1:]:
        if line.startswith("#"):
            next_level = len(line) - len(line.lstrip("#"))
            if next_level <= level:
                break
        item = OUTCOME_LINE_RE.match(line)
        if item:
            outcomes.append(item.group(1))
            continue
        if kind_re is not None and kind is None:
            match = kind_re.match(line.strip())
            if match and kind_re.groups:
                kind = (match.group(1) or "").strip()
                if kind_re.groups >= 2:
                    discipline = (match.group(2) or "").strip() or None
    return {
        "heading": section_title,
        "line_no": start + 1,
        "kind": kind,
        "discipline": discipline,
        "outcomes": outcomes,
    }


def graph_page_names(pages_dir, fmt) -> dict[str, Path]:
    """Collect existing page names using each notes format's naming convention."""
    pages_dir = Path(pages_dir)
    names = {}
    if not pages_dir.is_dir():
        return names
    files = (
        pages_dir.glob("*.md") if fmt == "logseq"
        else pages_dir.rglob("*.md")
    )
    for path in sorted(files):
        if fmt != "logseq":
            names.setdefault(path.stem, path)
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            continue
        match = re.search(r"^title::\s*(.+?)\s*$", head, re.MULTILINE)
        name = match.group(1) if match else from_triple_lowbar(path.stem)
        names.setdefault(name, path)
    return names


def build_autolinker(names: dict[str, Path], cfg=None):
    stop = {s.casefold() for s in AUTOLINK_STOPLIST}
    candidates = [
        name for name in names
        if len(name) >= AUTOLINK_MIN_LENGTH
        and name.casefold() not in stop
        and "|" not in name
        and not re.match(r"^\d", name)
    ]
    if not candidates:
        return lambda text, used: (text, [])
    candidates.sort(key=len, reverse=True)
    canonical = {candidate.casefold(): candidate for candidate in candidates}
    rx = re.compile(
        r"(?<![\w\[])("
        + "|".join(re.escape(candidate) for candidate in candidates)
        + r")(?![\w\]])",
        re.IGNORECASE,
    )

    def link(text: str, used: set[str]) -> tuple[str, list[str]]:
        made = []

        def substitute(match):
            hit = match.group(1)
            name = canonical.get(hit.casefold(), hit)
            if AUTOLINK_FIRST_OCCURRENCE_ONLY and name in used:
                return hit
            used.add(name)
            made.append(name)
            return f"[[{name}]]"

        # Existing links and code spans retain their original text during linking.
        parts = re.split(r"(`[^`]*`|\[\[[^\]]*\]\])", text)
        for index in range(0, len(parts), 2):
            parts[index] = rx.sub(substitute, parts[index])
        return "".join(parts), made

    return link


def answer_index(answers) -> dict[str, dict]:
    return {answer["item_id"]: answer for answer in (answers or {}).get("items", [])}


def fill_index(fill) -> dict[str, dict]:
    return {item["item_id"]: item for item in (fill or {}).get("items", [])}


def source_display(source_id: str, corpus: dict, self_ids: set, cfg):
    """Resolve a citation source into a URL, page reference, or plain text."""
    if source_id in self_ids:
        return "plain", SELF_REFERENCE_TEXT

    match = PMID_RE.match(source_id)
    if match:
        return "link", CITATION_URL_TEMPLATES["pmid"].format(id=match.group(1))
    match = DOI_RE.match(source_id)
    if match:
        return "link", CITATION_URL_TEMPLATES["doi"].format(id=match.group(1))

    url_aliases = cfg.get("export.citations.url_aliases") if cfg is not None else None
    url = (url_aliases or {}).get(source_id)
    if url:
        return "link", url

    page_aliases = cfg.get("export.citations.page_aliases") if cfg is not None else None
    alias = (page_aliases or {}).get(source_id)
    if alias:
        return "ref", alias

    entry = corpus.get(source_id) or {}
    kind_pages = cfg.get("export.citations.kind_pages") if cfg is not None else None
    by_kind = (kind_pages or {}).get(entry.get("kind"))
    if by_kind:
        return "ref", by_kind
    return "plain", source_id


def render_citation(pv: dict, corpus: dict, self_ids: set, cfg) -> str:
    """Render one provenance entry with its source and optional locator."""
    kind, value = source_display(pv["source_id"], corpus, self_ids, cfg)
    locator = (pv.get("locator") or "").strip()
    title = (pv.get("title") or "").strip()
    if kind == "link":
        inner = f"[{title or pv['source_id']}]({value})"
    elif kind == "ref":
        inner = f"[[{value}]]"
    else:
        inner = f"**{value}**" if value != SELF_REFERENCE_TEXT else value
    return f"({inner}, {locator})" if locator else f"({inner})"


def provenance_tags(ans: dict, corpus: dict, self_ids: set, cfg) -> dict[str, str]:
    return {
        provenance["ref"]: render_citation(provenance, corpus, self_ids, cfg)
        for provenance in ans.get("provenance", [])
    }


def render_table_cells(ans, item, tags=None) -> list[str]:
    """Render drafted cells using captured column labels and body row labels."""
    cells = ans.get("table_cells") or []
    if not cells:
        return []
    constraints = item.get("constraints") or {}
    column_headers = constraints.get("column_headers") or []
    row_headers = constraints.get("row_headers") or []
    header_rows = max(
        0, (constraints.get("rows") or len(row_headers)) - len(row_headers)
    )

    def col_name(column):
        return (
            column_headers[column]
            if column < len(column_headers) and column_headers[column]
            else f"column {column}"
        )

    def row_name(row):
        # Captured row labels exclude leading header rows from the absolute grid.
        body_row = row - header_rows
        if 0 <= body_row < len(row_headers):
            named = [text for text in row_headers[body_row] if text]
            if named:
                return named[0]
        return f"row {row + 1}"

    by_row = {}
    for cell in cells:
        by_row.setdefault(cell["row"], []).append(cell)
    out = [f"**Table as drafted** — {len(cells)} cell(s)."]
    for row in sorted(by_row):
        for cell in sorted(by_row[row], key=lambda value: value["col"]):
            # Flatten label line breaks so bold delimiters remain valid during rendering.
            label = " ".join(f"{row_name(row)} · {col_name(cell['col'])}".split())
            refs = cell.get("provenance_refs")
            if refs is None:
                refs = list(tags or {})
            citations = " ".join((tags or {}).get(ref, f"(unresolved source {ref})") for ref in refs)
            out.append(f"**{label}** — {cell['text']} {citations}".rstrip())
    return out


def render_answer(
    ans: dict, link, used: set[str], corpus=None, self_ids=None, cfg=None
) -> tuple[list[str], list[str]]:
    """Render answer segments, citations, conflicts, uncertainties, and recorded tier changes."""
    tags = provenance_tags(ans, corpus or {}, self_ids or set(), cfg)
    blocks = []
    links = []
    for segment in ans.get("segments", []):
        text, made = link(segment.get("text", ""), used)
        links += made
        refs = [
            tags.get(ref, f"(unresolved source {ref})")
            for ref in segment.get("provenance_refs", [])
        ]
        blocks.append(
            f"*{text}*" if segment.get("kind") == "connective"
            else f"{text} {' '.join(refs)}".rstrip()
        )
    for conflict in ans.get("conflicts", []):
        blocks.append(
            f"⚠ **Tier conflict.** {tags.get(conflict['higher_ref'], conflict['higher_ref'])} says "
            f"“{conflict['higher_claim']}”; {tags.get(conflict['lower_ref'], conflict['lower_ref'])} "
            f"says “{conflict['lower_claim']}”."
            + (f" {conflict['note']}" if conflict.get("note") else "")
        )
    for uncertainty in ans.get("uncertainties", []):
        why = (
            f" — {uncertainty['why_it_matters']}"
            if uncertainty.get("why_it_matters") else ""
        )
        blocks.append(f"⚠ **Uncertain.** {uncertainty['question']}{why}")
    for descent in ans.get("tier_descent", []):
        note = f" {descent['note']}" if descent.get("note") else ""
        blocks.append(
            f"Tier {descent['from_tier']} → {descent['to_tier']}: "
            f"{descent['reason']}.{note}"
        )
    return blocks, links


def check_state_label(state, item):
    if 'proposed_selection' in state:
        options={o['option_id']:o['text'] for o in item.get('options',[])}
        return '; '.join(options.get(k,k) for k in state['proposed_selection'])
    return '; '.join(p['label']+' → '+p['target'] for p in state.get('label_placements',[]))


def render_check_answer(item, draft, filled, reconciled, corpus, self_ids, cfg, link, used):
    """Lt evidence gets no tier; source tags belong to draft/research segments only."""
    c=(filled or {}).get('check_answer') or {}
    if not c.get('presses'):
        return None
    final=c['final']; state=check_state_label(final['state'],item);n=len(c['presses'])
    if reconciled:
        record=reconciled
        answer=dict(draft,segments=record['segments'],provenance=draft.get('provenance',[])+record['provenance'],conflicts=record['conflicts'])
        blocks,made=render_answer(answer,link,used,corpus,self_ids,cfg)
        blocks.insert(0,f"Answer: {state} (accepted by Lt's Check Answer after {n} presses)")
        tags=provenance_tags(answer,corpus,self_ids,cfg)
        if record.get('note'):blocks.append('Note: '+record['note'])
        dispute=record.get('dispute')
        if record['outcome']=='disputed':
            evidence=' '.join('“'+e['quote']+'” '+tags.get(e['ref'],e['ref']) for e in dispute['evidence'])
            blocks.insert(0,"⚠ **Lt's key contradicts the cited sources.** "+dispute['lt_part']+'. Evidence: '+evidence+" The page holds Lt's key.")
        blocks.append('Cited draft: '+check_state_label(c['draft']['state'],item))
        for drop in record['dropped']:
            seg=draft['segments'][drop['draft_segment']]
            refs=' '.join(tags.get(r,r) for r in seg.get('provenance_refs',[]))
            blocks.append('Dropped draft segment: '+seg['text']+' '+refs+' Reason: '+drop['reason'])
        return blocks,made
    if not final['moved']:
        return None
    if final['verdict']=='correct':
        headline=f"Answer in Lt: {state} (accepted by Lt's Check Answer after {n} presses)"
    else:
        attempt=next((p for p in c['presses'] if p['hash']==final['hash']),None)
        headline=f"Answer in Lt: {state}. Lt did not mark this correct; the page holds attempt {attempt['n'] if attempt else '?'}, with {len(attempt['right']) if attempt else 0} parts marked right and {len(attempt['wrong']) if attempt else 0} wrong."
    if not filled.get('verified'):headline+=' Persistence is unconfirmed; re-run check-verify.'
    blocks,made=render_answer(draft,link,used,corpus,self_ids,cfg)
    blocks.insert(0,headline)
    blocks.insert(1,'Cited draft: '+check_state_label(c['draft']['state'],item))
    if c.get('lt_feedback'):blocks.append('Lt feedback (data): '+c['lt_feedback'])
    return blocks,made


def build_module_page(
    module, answers, fill, ilo, page_title, stamp_props, cfg, link, asset_rows,
    media_rows=None, page_figures=None, reconciled=None,
) -> tuple[str, list[str]]:
    ai, fi = answer_index(answers), fill_index(fill)
    ri = {i['item_id']:i for i in (reconciled or {}).get('items',[])}
    media_rows = media_rows or {}
    page_figures = page_figures or {}
    corpus = {
        entry["source_id"]: entry
        for entry in (answers or {}).get("tier1_corpus", [])
    }
    mid = module.get("module_id") or ""
    self_ids = {
        source_id for source_id, entry in corpus.items()
        if mid and mid in str(entry.get("path", ""))
    }
    assets_dir = str(cfg.get("notes.assets_dir")).replace("\\", "/")
    namespace = cfg.get("notes.page_namespace")
    stamp_property = cfg.get("notes.stamp_property")

    def embed(row):
        destination = urllib.parse.quote(f"../{assets_dir}/{row['name']}", safe="/.-_")
        return f"![{literal_caption(row['alt'])}]({destination})"

    used = set()
    all_links = []
    lines = [f"title:: {page_title}", f"type:: **{namespace} Module Output**"]
    lines += [f"{key}:: {value}" for key, value in stamp_props.items()]
    lines.append(f"tags:: [[{namespace}]], [[module output]]")
    lines += [
        "",
        "-",
        f"- ⚠ This page is your own module output, written by lt-module-export. "
        f"lt-module-answer skips every page stamped with {stamp_property}::, "
        f"so it is never read back as a source.",
        f"- Source: [{module['module_id']}]({module['source_url']}) · captured "
        f"{module['captured_at'][:10]} · exported {stamp_props['lt-exported'][:10]}",
        "-",
    ]

    if ilo:
        lines.append("- # Intended Learning Outcomes")
        for number, outcome in enumerate(ilo["outcomes"], 1):
            text, made = link(outcome, used)
            all_links += made
            lines.append(f"- {number} :: {text}")
        lines += ["- ---", "-"]

    for page in module.get("pages", []):
        if page.get("_rat_summary"):
            lines.append(f"- # Page {page['page_index']} - {page.get('title') or 'Readiness test'}")
            lines.append("\t- " + page["_rat_summary"])
            continue
        if excluded_page(page):
            continue
        items = [
            item for item in page.get("items", [])
            if ai.get(item["item_id"], {}).get("status") == "drafted"
        ]
        # Unattached page figures remain assets because only item figures enter notes.
        if not items:
            continue
        lines.append(f"- # Page {page['page_index']} — {page.get('title') or 'Untitled'}")
        for item in items:
            ans = ai[item["item_id"]]
            prompt = heading(item.get("prompt_text"))
            if not prompt:
                first = next(
                    (
                        segment.get("text", "")
                        for segment in ans.get("segments", [])
                        if segment.get("kind") != "connective" and segment.get("text")
                    ),
                    "",
                )
                clause = re.split(r",\s+(?:captured|from)\b|\.\s", first)[0].strip()
                prompt = (clause[:70] + "…") if len(clause) > 70 else clause
                prompt = prompt or item["item_id"]
            lines.append(f"\t- ## {prompt}")
            meta = [item["item_id"]]
            if ans.get("confidence"):
                meta.append(f"{ans['confidence']} confidence")
            lines.append("\t  *" + " · ".join(meta) + "*")

            checked = render_check_answer(item,ans,fi.get(item['item_id']),ri.get(item['item_id']),corpus,self_ids,cfg,link,used)
            blocks, made = checked if checked else render_answer(ans, link, used, corpus, self_ids, cfg)
            all_links += made
            for block in blocks:
                lines.append("\t\t- " + block)
            for block in render_table_cells(ans, item, provenance_tags(ans, corpus, self_ids, cfg)):
                lines.append("\t\t- " + block)
            for row in media_rows.get(item["item_id"], []):
                role = f" ({row['role']})" if row.get("role") else ""
                lines.append(f"\t\t- {embed(row)}{role}")
            for asset in asset_rows.get(item["item_id"], []):
                lines.append("\t\t- " + embed(asset))

            filled = fi.get(item["item_id"])
            if not filled:
                continue
            verified = " (verified)" if filled.get("verified") else ""
            lines.append(f"\t\t- Fill: **{filled.get('result')}**{verified}")
            todo = filled.get("todo")
            if todo:
                lines.append(f"\t\t\t- {todo['blocker']}")
                lines.append(f"\t\t\t- Placement: {todo['placement']}")
                if todo.get("artefact_path"):
                    lines.append(f"\t\t\t- Artefact: {todo['artefact_path']}")
            conflict = filled.get("lt_conflict")
            if conflict:
                tags = provenance_tags(ans, corpus, self_ids, cfg)
                ref = tags.get(
                    conflict["draft_ref"],
                    f"(unresolved source {conflict['draft_ref']})",
                )
                lines.append(
                    f"\t\t\t- ⚠ **Lt marks this wrong and the answer stands.** The page "
                    f"holds “{conflict['held_answer']}”; Lt's marking indicates "
                    f"“{conflict['lt_indicated']}”, seen via {conflict['observed_via']}. The "
                    f"researched answer stands — {ref} — and Lt's marking is evidence "
                    f"about what the unit will accept, not a tier."
                    + (f" {conflict['note']}" if conflict.get("note") else "")
                )
        lines.append("-")

    hidden = set(cfg.get("export.citations.hidden_skip_reasons") or [])
    skipped = [
        (item, ai[item["item_id"]])
        for item in module_items(module)
        if item["item_id"] in ai
        and ai[item["item_id"]].get("status") != "drafted"
        and ai[item["item_id"]].get("skip_reason") not in hidden
    ]
    if skipped:
        lines.append("- # Not drafted")
        for item, ans in skipped:
            todo = ans.get("todo") if ans.get("status") == "blocked" else None
            if todo:
                lines.append(
                    f"\t- ## TODO **{heading(item.get('prompt_text')) or item['item_id']}**"
                )
                lines.append(f"\t  *{item['item_id']}*")
                lines.append(f"\t\t- {todo['blocker']}")
                lines.append(f"\t\t- Placement: {todo['placement']}")
                if todo.get("artefact_path"):
                    lines.append(f"\t\t- Artefact: {todo['artefact_path']}")
            else:
                lines.append(
                    f"\t- {item['item_id']} — "
                    f"{ans.get('skip_reason', 'no reason recorded')}"
                )
        lines.append("-")
    return "\n".join(lines) + "\n", sorted(set(all_links))


def outcome_coverage(module, answers):
    ai = answer_index(answers)
    covered = {}
    corpus = {
        entry["source_id"]: entry
        for entry in (answers or {}).get("tier1_corpus", [])
    }
    ilo_ids = {
        source_id for source_id, entry in corpus.items()
        if entry.get("kind") == "ilo"
    }
    for item in module_items(module):
        ans = ai.get(item["item_id"])
        if not ans or ans.get("status") != "drafted":
            continue
        for provenance in ans.get("provenance", []):
            if provenance["source_id"] not in ilo_ids:
                continue
            match = re.search(r"(\d+)", provenance.get("locator") or "")
            if match:
                covered.setdefault(int(match.group(1)), []).append(
                    (item["item_id"], heading(item.get("prompt_text")))
                )
    return covered


def no_ilo_message(week):
    return (
        f"⚠ No learning-outcomes section matched week {week} and this title. "
        "Select the outcome section before calculating coverage for this export."
    )


def build_ilo_map(module, answers, ilo, week, title, stamp_property="lt-module") -> str:
    covered = outcome_coverage(module, answers)
    lines = [f"# {week} {title}: learning outcome map", "",
             f"{stamp_property}:: {module['module_id']}", ""]
    if not ilo:
        lines.append(no_ilo_message(week))
        return "\n".join(lines) + "\n"
    known = [ilo.get(key) for key in ("kind", "discipline") if ilo.get(key)]
    if known:
        lines += ["*" + " · ".join(known) + "*", ""]
    for number, outcome in enumerate(ilo["outcomes"], 1):
        lines.append(f"{number}. {outcome}")
        hits = covered.get(number, [])
        if hits:
            for item_id, prompt in hits:
                lines.append(f"   - {item_id}: {prompt[:120]}")
        else:
            lines.append(
                "   - ⚠ No item in this module cites this outcome. Either the "
                "module does not address it, or the draft did not tag it."
            )
    return "\n".join(lines) + "\n"


def build_tracker_handoff(module, answers, ilo, week, title, fmt, page_title=None,
                          stamp_property="lt-module") -> str:
    covered = outcome_coverage(module, answers)
    if fmt == "logseq":
        page_title = page_title or f"{week} {title}"
        lines = [
            f"title:: {page_title} Tracker",
            "type:: **Tracker handoff**",
            f"{stamp_property}:: {module['module_id']}",
            "",
            "-",
            f"- ## TODO **Week {week} | {title}**",
        ]
        if not ilo:
            lines.append("\t- " + no_ilo_message(week))
        else:
            for number, outcome in enumerate(ilo["outcomes"], 1):
                lines.append(f"\t- TODO {outcome}")
                hits = covered.get(number, [])
                if hits:
                    lines += [
                        f"\t\t- {item_id}: {prompt[:120]}"
                        for item_id, prompt in hits
                    ]
                else:
                    lines.append("\t\t- ⚠ No item cites this outcome")
    else:
        lines = [f"# Week {week} | {title}", "",
                 f"{stamp_property}:: {module['module_id']}", ""]
        if not ilo:
            lines.append(no_ilo_message(week))
        else:
            for number, outcome in enumerate(ilo["outcomes"], 1):
                lines.append(f"- [ ] {outcome}")
                hits = covered.get(number, [])
                if hits:
                    lines += [
                        f"  - {item_id}: {prompt[:120]}"
                        for item_id, prompt in hits
                    ]
                else:
                    lines.append("  - ⚠ No item cites this outcome")
    return "\n".join(lines) + "\n"


def build_whiteboard(name: str, page_names: list[str], cfg, seed: str) -> str:
    """Build deterministic portal shapes without inventing relationships between notes pages."""
    width, height = WHITEBOARD_PORTAL_SIZE
    gap_x, gap_y = WHITEBOARD_GRID_GAP
    origin_x, origin_y = WHITEBOARD_ORIGIN
    columns = WHITEBOARD_GRID_COLS
    namespace = uuid.UUID("00000000-0000-0000-0000-000000000000")
    page_uuid = str(uuid.uuid5(namespace, f"{seed}:page"))
    stamp_ms = int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16)
    shapes, ids = [], []
    for index, page_name in enumerate(page_names):
        shape_id = str(uuid.uuid5(namespace, f"{seed}:{index}:{page_name}"))
        ids.append(shape_id)
        x = origin_x + (index % columns) * (width + gap_x)
        y = origin_y + (index // columns) * (height + gap_y)
        shapes.append(
            "{:block/created-at %d\n:block/properties \n{:ls-type :whiteboard-shape\n"
            ":logseq.tldraw.shape \n{:blockType \"P\"\n:stroke \"\"\n:rotation 0\n"
            ":collapsed false\n:borderRadius 8\n:index %d\n:scale [1 1]\n:pageId %s\n"
            ":scaleLevel \"%s\"\n:fill \"\"\n:compact false\n:isAutoResizing true\n"
            ":type \"logseq-portal\"\n:size [%d %d]\n:strokeType \"line\"\n:strokeWidth 2\n"
            ":opacity 1\n:id \"%s\"\n:noFill false\n:point [%d %d]\n:parentId \"%s\"\n"
            ":nonce %d}}\n:block/updated-at %d}"
            % (
                stamp_ms, index, json.dumps(page_name), WHITEBOARD_SCALE_LEVEL,
                width, height, shape_id, x, y, page_uuid, stamp_ms, stamp_ms,
            )
        )
    shape_index = " ".join(f'"{shape_id}"' for shape_id in ids)
    folded_name = json.dumps(name.casefold(), ensure_ascii=False)
    original_name = json.dumps(name, ensure_ascii=False)
    return (
        "{:blocks (\n" + " \n".join(shapes) + ")\n:pages (\n"
        f"{{:block/uuid #uuid \"{page_uuid}\"\n:block/properties \n"
        "{:ls-type :whiteboard-page\n:logseq.tldraw.page \n"
        f"{{:id \"{page_uuid}\"\n:name {folded_name}\n:bindings \n{{}}\n"
        f":nonce 1\n:assets []\n:shapes-index ({shape_index})}}}}\n"
        f":block/updated-at {stamp_ms}\n:block/created-at {stamp_ms}\n"
        f":block/type \"whiteboard\"\n:block/name {folded_name}\n"
        f":block/original-name {original_name}}})}}\n"
    )


def build_flashcard_source(module, answers, ilo, week, title, cfg) -> str:
    """Preserve worked answers and citations as source material for later flashcards."""
    ai = answer_index(answers)
    stamp_property = cfg.get("notes.stamp_property")
    tag_prefix = cfg.get("export.flashcard_source.tag_prefix")
    lines = [
        f"<!-- {stamp_property}: {module['module_id']} -->",
        f"# {week} {title}",
        "",
        f"{stamp_property}:: {module['module_id']}",
        "",
        f"Tag: {tag_prefix}{week}",
        "",
        f"Source: Lt module {module['module_id']}, captured {module['captured_at'][:10]}. "
        "Worked answers with their provenance tags, so a card's source field can "
        "be filled from them and is never empty.",
        "",
    ]
    if ilo:
        lines += ["## Intended learning outcomes", ""]
        lines += [
            f"{number}. {outcome}"
            for number, outcome in enumerate(ilo["outcomes"], 1)
        ]
        lines.append("")
    corpus = {
        entry["source_id"]: entry
        for entry in (answers or {}).get("tier1_corpus", [])
    }
    mid = module.get("module_id") or ""
    self_ids = {
        source_id for source_id, entry in corpus.items()
        if mid and mid in str(entry.get("path", ""))
    }
    for page in module.get("pages", []):
        if excluded_page(page):
            continue
        items = [
            item for item in page.get("items", [])
            if ai.get(item["item_id"], {}).get("status") == "drafted"
        ]
        if not items:
            continue
        lines += [
            f"## {page.get('title') or ('Page %s' % page['page_index'])}",
            "",
        ]
        for item in items:
            ans = ai[item["item_id"]]
            tags = provenance_tags(ans, corpus, self_ids, cfg)
            lines += [f"### {heading(item.get('prompt_text'))}", ""]
            for segment in ans.get("segments", []):
                refs = " ".join(
                    tags.get(ref, "") for ref in segment.get("provenance_refs", [])
                )
                lines.append(f"{segment.get('text', '')} {refs}".rstrip())
            lines.extend(render_table_cells(ans, item, tags))
            lines.append("")
    return "\n".join(lines) + "\n"


def convert_outline(page_md, fmt, assets_rel, module_id, stamp_property):
    """Convert outline blocks while preserving stamps, hierarchy, citations, and asset paths."""
    if fmt == "logseq":
        return page_md
    source = page_md.splitlines()
    properties = []
    body_start = len(source)
    for index, line in enumerate(source):
        if line.startswith("-"):
            body_start = index
            break
        match = re.match(r"^([^:\n]+)::\s*(.*)$", line)
        if match:
            key, value = match.groups()
            if key == "tags":
                value = re.findall(r"\[\[([^\]]+)\]\]", value)
            properties.append((key, value))

    lines = ["---"]
    lines += [
        f"{key}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in properties
    ]
    lines += [
        "---",
        f"{stamp_property}:: {module_id}",
        "",
    ]
    heading_depths = []
    block_positions = {}

    def blank():
        if lines and lines[-1] != "":
            lines.append("")

    for line in source[body_start:]:
        if re.fullmatch(r"\t*-(?: ---)?\s*", line):
            blank()
            continue
        match = re.match(r"^(\t*)- (.*)$", line)
        if match:
            depth = len(match.group(1))
            text = match.group(2)
            while heading_depths and heading_depths[-1] >= depth:
                heading_depths.pop()
            block_positions = {
                old_depth: position
                for old_depth, position in block_positions.items()
                if old_depth < depth
            }
            if text.startswith("# ") or text.startswith("## "):
                blank()
                lines.append(text)
                lines.append("")
                heading_depths.append(depth)
                block_positions[depth] = (0, True)
            else:
                relative_depth = (
                    max(0, depth - heading_depths[-1] - 1)
                    if heading_depths else depth
                )
                indent = 2 * relative_depth
                text = re.sub(r"^(\d+)\s+::\s*", r"\1. ", text)
                lines.append(" " * indent + "- " + text)
                block_positions[depth] = (indent, False)
            continue

        continuation = re.match(r"^(\t*)  (.*)$", line)
        if continuation:
            depth = len(continuation.group(1))
            position = block_positions.get(depth)
            if position is None:
                parents = [
                    old_depth for old_depth in block_positions
                    if old_depth <= depth
                ]
                position = block_positions[max(parents)] if parents else (0, True)
            indent, is_heading = position
            lines.append(
                " " * (indent if is_heading else indent + 2)
                + continuation.group(2)
            )
        else:
            lines.append(line)

    text = "\n".join(lines).rstrip() + "\n"
    assets_rel = str(assets_rel).replace("\\", "/").rstrip("/")
    text = re.sub(
        r"(!\[[^\]\n]*\]\()\.\./(?:[^/)\n]+/)+([^/)\n]+)(\))",
        lambda match: (
            match.group(1) + assets_rel + "/" + match.group(2) + match.group(3)
        ),
        text,
    )
    if fmt == "markdown":
        text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    return text


def build_run_record(module, answers, fill, diagrams, notes, cfg) -> dict:
    skills = ["lt-module-capture"]
    if answers:
        skills.append("lt-module-answer")
    if diagrams:
        skills.append("mechanistic-diagram")
    if fill:
        skills.append("lt-module-fill")
    skills.append("lt-module-export")
    outcome = "complete"
    if fill:
        if (fill.get("boundary_reached") or {}).get("stopped"):
            outcome = "stopped-at-boundary"
        elif any(item.get("result") == "todo" for item in fill.get("items", [])):
            outcome = "degraded"
    record = {
        "schema_version": 1,
        "run_ts": now_iso(),
        "module_id": module["module_id"],
        "skills_invoked": skills,
        "question_type_census": dict(module["census"]["by_type"]),
        "outcome": outcome,
        "tool_version": ltcore.__version__,
        "config_digest": cfg.digest(),
    }
    if fill and fill.get("selector_events"):
        # Run records retain only selector fields accepted by their own schema.
        record["selector_events"] = [
            {
                key: event[key]
                for key in ("selector", "item_type", "outcome")
                if key in event
            }
            for event in fill["selector_events"]
        ]
    if cfg.get("institution.profile"):
        record["institution_profile"] = cfg.get("institution.profile")
    if cfg.get("institution.unit_code"):
        record["unit_code"] = cfg.get("institution.unit_code")
    if notes:
        record["notes"] = notes
    return record


def settled(blob: bytes) -> bytes:
    """Ignore export timestamps so unchanged content never creates replacement conflicts."""
    try:
        return VOLATILE_RE.sub("", blob.decode("utf-8")).encode()
    except UnicodeDecodeError:
        return blob


class Target:
    def __init__(self, path: Path, content, kind: str, probe):
        self.path, self.content, self.kind, self.probe = path, content, kind, probe
        self.action = "create"
        self.diff = ""

    def classify(self, module_id: str, replace: bool):
        if not self.path.exists():
            self.action = "create"
            return
        old = self.path.read_bytes()
        new = self.content if isinstance(self.content, bytes) else self.content.encode()
        if old == new or settled(old) == settled(new):
            self.action = "identical"
            return
        if self.probe is None or self.probe(self.path, old) != module_id:
            self.action = "blocked-foreign"
            return
        try:
            self.diff = "".join(difflib.unified_diff(
                old.decode("utf-8").splitlines(True),
                new.decode("utf-8").splitlines(True),
                fromfile=f"{self.path} (on disk)",
                tofile=f"{self.path} (this run)",
            ))
        except UnicodeDecodeError:
            self.diff = ""
        self.action = "replace" if replace else "blocked-ours"


class ExportArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(3, f"FATAL: {message}\n")


def print_validation_errors(result):
    for line in result.schema_errors:
        print(line, file=sys.stderr)
    for line in result.rail_errors:
        print(line, file=sys.stderr)


def validated_input(path, schema, validate_mod, scratch, module=None, **kwargs):
    doc = load_json(path)
    dropped = validate_mod.drop_legacy_fields(doc)
    validation_path = path
    if dropped:
        print(
            f"note: dropped legacy field(s): {', '.join(dropped)} from {path.name}",
            file=sys.stderr,
        )
        cleaned_dir = scratch / schema
        cleaned_dir.mkdir(parents=True, exist_ok=True)
        validation_path = cleaned_dir / path.name
        validation_path.write_text(
            json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    if module is not None:
        kwargs["module"] = module
    result = validate_mod.validate_file(validation_path, schema=schema, **kwargs)
    if not result.ok:
        print_validation_errors(result)
        print(
            f"FATAL: {path.name} did not validate; nothing was exported.",
            file=sys.stderr,
        )
        return doc, validation_path, 2 if result.exit_code in (1, 2) else 3
    return doc, validation_path, 0


def main(argv=None) -> int:
    parser = ExportArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--module", help="module.json path, or a module id in the captures folder")
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--fill-report", type=Path)
    parser.add_argument("--reconciled", type=Path)
    parser.add_argument("--diagram-dir", type=Path, action="append", default=[])
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--media-dir", type=Path)
    parser.add_argument("--week")
    parser.add_argument("--page-kinds", type=Path)
    parser.add_argument("--rat", choices=["trat", "irat"])
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--overwrite", action="store_true",
                        help="allow replacement of existing staging files under --out-dir")
    parser.add_argument("--pdf", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--list-stamped", action="store_true",
                        help="list pages in the notes graph that carry the module-output stamp")
    parser.add_argument("--check-page", type=Path, metavar="PAGE",
                        help="exit 1 when this page in the notes graph carries the stamp")
    args = parser.parse_args(argv)

    if args.list_stamped or args.check_page:
        from ltcore import quarantine
        scan_args = (["--config", str(args.config)] if args.config else []) + (
            ["--check", str(args.check_page)] if args.check_page else []) + (
            ["--json"] if args.json else [])
        return quarantine.main(scan_args)
    if not args.module:
        parser.error("--module is required")

    try:
        cfg = config_mod.load(args.config)
    except config_mod.ConfigError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 3

    outputs = {
        name: bool(cfg.get("export.outputs." + name))
        for name in OUTPUT_NAMES
    }
    want_pdf = outputs["pdf"] if args.pdf is None else args.pdf
    missing_pdf = PDF_DEPENDENCIES.intersection(_MISSING)
    if want_pdf and missing_pdf:
        print(ltcore.missing_hint(sorted(missing_pdf)), file=sys.stderr)
        return 3

    if args.install:
        if config_mod.no_install():
            print(
                "FATAL: --install is refused while LT_SUITE_NO_INSTALL is set.",
                file=sys.stderr,
            )
            return 5
        if not outputs["notes_graph"]:
            print(
                "FATAL: --install needs export.outputs.notes_graph = true.",
                file=sys.stderr,
            )
            return 3
        if not cfg.get("notes.graph_path"):
            print(f"FATAL: notes.graph_path is not set. {cfg.setting_hint()}", file=sys.stderr)
            return 3

    try:
        from ltcore import validate as validate_mod
    except ImportError:
        print(
            "FATAL: ltcore.validate is not available, so the handoff rails cannot run; "
            "nothing was exported.",
            file=sys.stderr,
        )
        return 3

    try:
        st = store_mod.Store(cfg, data_dir=args.data_dir)
        module_path = expand(args.module)
        if not module_path.is_file():
            try:
                store_mod.check_id(args.module)
            except ValueError:
                print(f"FATAL: {module_path} does not exist", file=sys.stderr)
                return 3
            module_path = Path(st.capture_path(args.module))
        args.module = module_path
        args.answers = expand(args.answers) if args.answers else None
        args.fill_report = expand(args.fill_report) if args.fill_report else None
        args.reconciled = expand(args.reconciled) if args.reconciled else None
        for path in (args.module, args.answers, args.fill_report, args.reconciled):
            if path is not None and not path.is_file():
                print(f"FATAL: {path} does not exist", file=sys.stderr)
                return 3
        with tempfile.TemporaryDirectory(prefix="lt-export-") as temporary:
            return export_run(
                args, cfg, st, validate_mod, Path(temporary), outputs, want_pdf
            )
    except (OSError, UnicodeError, ValueError, re.error, config_mod.ConfigError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 3


def export_run(args, cfg, st, validate_mod, scratch, outputs, want_pdf) -> int:
    report = {
        "module": str(args.module),
        "wrote": [],
        "blocked": [],
        "links": [],
        "warnings": [],
        "pages_skipped": [],
    }
    module, validated_module, rc = validated_input(
        args.module, "module", validate_mod, scratch
    )
    if rc:
        return rc
    module_id = module["module_id"]
    try:
        store_mod.check_id(module_id)
    except ValueError:
        print(f"FATAL: unsafe module id {module_id!r}", file=sys.stderr)
        return 1

    answers = fill = None
    if args.answers:
        answers, _, rc = validated_input(
            args.answers,
            "answers",
            validate_mod,
            scratch,
            module=validated_module,
            model_answer_read=cfg.get("model_answer.read"),
            config_path=args.config,
        )
        if rc:
            return rc
    if args.fill_report:
        approvals_path = Path(st.approvals_path(module_id))
        kwargs = {"model_answer_read": cfg.get("model_answer.read"), "config_path": args.config}
        if approvals_path.is_file():
            kwargs["approvals"] = str(approvals_path)
        fill, _, rc = validated_input(
            args.fill_report,
            "fill-report",
            validate_mod,
            scratch,
            module=validated_module,
            **kwargs,
        )
        if rc:
            return rc

    reconciled = None
    if args.reconciled:
        if not args.answers or not args.fill_report:
            print('FATAL: --reconciled requires --answers and --fill-report; re-run reconcile',file=sys.stderr)
            return 1
        try:
            reconciled = json.loads(args.reconciled.read_text(encoding='utf-8'))
            for field,path in (('source_answers',args.answers),('source_fill_report',args.fill_report)):
                if reconciled.get(field,{}).get('sha256')!=sha256_of(path):
                    print('FATAL: stale '+field+' pin; re-run reconcile',file=sys.stderr)
                    return 1
        except (OSError,ValueError):
            print('FATAL: invalid reconciled file; re-run reconcile',file=sys.stderr)
            return 1
        reconciled,_,rc=validated_input(args.reconciled,'reconciled',validate_mod,scratch,module=validated_module,
                                       model_answer_read=cfg.get('model_answer.read'),config_path=args.config)
        if rc:return rc

    if answers:
        source = answers.get("source_module") or {}
        want, want_q = source.get("sha256"), source.get("question_digest")
        got = sha256_of(args.module)
        got_q = question_digest_of(module) if want_q else None
        if want_q and got_q and want_q != got_q:
            print(
                "FATAL: answers.json answers different questions than this capture asks.\n"
                f"  answers.source_module.question_digest = {want_q}\n"
                f"  question_digest({args.module.name})    = {got_q}\n"
                "A prompt, type, option or authored constraint changed. "
                "Re-draft rather than re-point; python3 -m ltcore.digest <old> "
                "--diff <new> names the items.",
                file=sys.stderr,
            )
            return 1
        if want_q and not got_q:
            report["warnings"].append(
                "answers.json records a question_digest but the shared digest helper "
                "was not readable, so the question binding could not be checked; "
                "falling back to the whole-file sha256."
            )
        if want and want != got:
            if not (want_q and got_q):
                if want_q:
                    why = (
                        f"This draft records a question_digest ({want_q[:12]}…), "
                        "but the shared digest helper was not readable. "
                        "The stricter file-hash rule applied instead. "
                        "Restore the shared digest helper and re-run."
                    )
                else:
                    why = (
                        "This draft records no question_digest, so an edited question "
                        "cannot be distinguished from an answered question."
                    )
                print(
                    "FATAL: answers.json is bound to a different capture.\n"
                    f"  answers.source_module.sha256 = {want}\n"
                    f"  sha256({args.module.name})    = {got}\n"
                    "Re-draft, or point --module at the capture used for drafting. "
                    + why,
                    file=sys.stderr,
                )
                return 1
            report["warnings"].append(
                f"{args.module.name} has changed since the draft was written "
                f"(sha256 {want[:12]}… -> {got[:12]}…) but no question-bearing field did "
                f"(question_digest {want_q[:12]}… holds). Expected after a fill: "
                "answer state, media and geometry can change without changing questions."
            )
        if answers.get("module_id") != module_id:
            print(
                "FATAL: answers.json module_id does not match the capture",
                file=sys.stderr,
            )
            return 1
    if fill and fill.get("module_id") != module_id:
        print("FATAL: fill-report module_id does not match the capture", file=sys.stderr)
        return 1

    # Export consumes safe recorded evidence only. It never opens answer sheets.
    ai = answer_index(answers)
    pkpath = args.page_kinds or st.run_dir(module_id) / "page-kinds.json"
    kind_record = page_kinds.load_current(module, cfg, answers, path=pkpath, answers_sha256=sha256_of(args.answers) if args.answers else None)
    statuses = {r["page_index"]: r for r in page_kinds.page_status(module, kind_record, policy=rat.policy_from_config(cfg))}
    for page in module.get("pages", []):
        idx = page["page_index"]
        if statuses[idx]["candidate"]:
            status = rat.decide(page, rat.policy_from_config(cfg), kind_record, answers)
            page["_rat_status"] = status
            if status["decision"] == "fill-from-sheet":
                sheet = status["sheet"]
                filled = sum(i.get("result") == "filled" and i.get("answer_source", {}).get("kind") == "rat-sheet"
                             for i in (fill or {}).get("items", []) if i["item_id"] in {it["item_id"] for it in page["items"]})
                total = len(page["items"])
                page["_rat_summary"] = (f"Filled from your answer sheet {sheet['name']}: "
                                        f"{filled} of {total} questions; {total-filled} left for you.")
            page["_rat_verdict"] = status["kind"] if status["rat"] is not None else args.rat
            if page["_rat_verdict"] is None and args.rat:
                page["_rat_verdict"] = args.rat
        elif page.get("rat_candidate"):
            page["_rat_verdict"] = rat_verdict_from_answers(page, ai, args.rat)

    week, title = lesson_week_title(module, cfg)
    if args.week:
        if week and args.week != week:
            report["warnings"].append(
                f"--week {args.week} overrides the {week!r} parsed from lesson.title "
                f"{(module.get('lesson') or {}).get('title')!r}."
            )
        week = args.week
    if not week:
        raw_title = (module.get("lesson") or {}).get("title")
        print(
            f"FATAL: cannot read a week number out of lesson.title {raw_title!r}; "
            "pass --week <number> to say which week this belongs to. "
            "It is not inferred, because the week names the page.",
            file=sys.stderr,
        )
        return 1

    namespace = cfg.get("notes.page_namespace")
    try:
        page_title = cfg.get("notes.page_title").format(
            namespace=namespace, week=week, title=title, module_id=module_id
        )
    except (KeyError, IndexError) as exc:
        name = exc.args[0] if exc.args else str(exc)
        print(
            f"FATAL: notes.page_title uses an unknown placeholder ({name})",
            file=sys.stderr,
        )
        return 3

    fmt = cfg.get("notes.format")
    graph_path = cfg.get("notes.graph_path")
    graph = expand(graph_path) if graph_path else None
    names = {}
    if outputs["notes_graph"]:
        if graph is None:
            report["warnings"].append(
                "export.outputs.notes_graph is on but notes.graph_path is empty; "
                "no links were made"
            )
        elif fmt in {"logseq", "obsidian"}:
            pages_dir = graph / str(cfg.get("notes.pages_dir"))
            names = graph_page_names(pages_dir, fmt)
    link = build_autolinker(names)

    installed_page_path = None
    if args.install:
        graph_directory(graph, cfg.get("notes.pages_dir"))
        graph_directory(graph, cfg.get("notes.assets_dir"))
        try:
            installed_page_path = page_file(graph, cfg, page_title)
        except ValueError:
            print("FATAL: page title escapes the pages folder", file=sys.stderr)
            return 1

    ilo = None
    ilo_setting = cfg.get("curriculum.learning_outcomes_file")
    if ilo_setting:
        ilo_path = expand(ilo_setting)
        if any(rat.within(ilo_path, root) for _, root in rat.effective_roots(rat.policy_from_config(cfg))):
            print("FATAL: learning_outcomes_file is inside a quarantined RAT root", file=sys.stderr)
            return 3
        if ilo_path.is_file():
            ilo = parse_ilo_section(ilo_path, week, title, cfg)
    if not ilo and ilo_setting:
        report["warnings"].append(
            f"No unambiguous learning-outcomes section for week {week} and {title!r}. "
            "Coverage is not reported and nothing is guessed."
        )
    elif not ilo and (outputs["ilo_map"] or outputs["tracker_handoff"]):
        report["warnings"].append(
            "curriculum.learning_outcomes_file is not set, so the outcome map and "
            "tracker handoff list no outcomes."
        )

    stamp_property = cfg.get("notes.stamp_property")
    if stamp_property.casefold() in RESERVED_STAMP_PROPERTIES:
        raise ValueError("notes.stamp_property uses a reserved page metadata property")
    stamp_props = {
        stamp_property: module_id,
        "lt-run": module["run_id"],
        "lt-source": module["source_url"],
        "lt-captured": module["captured_at"],
        "lt-exported": now_iso(),
        "lt-exported-by": f"lt-suite/lt-module-export {ltcore.__version__}",
    }
    out_dir = (expand(args.out_dir) if args.out_dir else Path(st.run_dir(module_id)) / "export").resolve()
    curriculum_root = cfg.get("curriculum.root")
    if curriculum_root and out_dir.is_relative_to(expand(curriculum_root).resolve()):
        print("FATAL: --out-dir is inside curriculum.root; choose a staging folder outside it.", file=sys.stderr)
        return 1
    flashcard_name = (output_filename(str(cfg.get("export.flashcard_source.file")))
                      if outputs["flashcard_source"] else None)
    whiteboard_name = f"{namespace}-{module_id}"
    whiteboard_filename = output_filename(sanitise_part(whiteboard_name.replace("/", "-")) + ".edn")
    planned = []
    if outputs["module_page"] or want_pdf or args.install:
        planned.append("module-page.md")
    planned += [name for switch, name in (("ilo_map", "ilo-map.md"),
                 ("tracker_handoff", "tracker-handoff.md"), ("flashcard_source", flashcard_name))
                if outputs[switch]]
    if outputs["whiteboard"] and outputs["notes_graph"] and fmt == "logseq":
        planned.append(whiteboard_filename)
    if len(set(planned)) != len(planned):
        raise ValueError("configured output filenames collide")
    for name in planned:
        target = staging_path(out_dir, out_dir / name)
        if target.exists() and not args.overwrite:
            raise FileExistsError("staging target exists; pass --overwrite to replace it")
    staging_path(out_dir, out_dir / "assets")
    if want_pdf:
        pdf_target = staging_path(out_dir, out_dir / "pdf")
        if pdf_target.exists() and any(pdf_target.iterdir()) and not args.overwrite:
            raise FileExistsError("PDF staging targets exist; pass --overwrite to replace them")
        if pdf_target.exists():
            for target in pdf_target.rglob("*"):
                staging_path(out_dir, target)
    out_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = out_dir / "assets"
    asset_dir.mkdir(exist_ok=True)
    prefix = f"lt-{module_id}"
    asset_files = []

    def copy_asset(source, destination):
        staging_path(out_dir, destination)
        if source.resolve() == destination.resolve():
            return
        if destination.exists() and not args.overwrite:
            raise FileExistsError("asset staging target exists; pass --overwrite to replace it")
        with source.open("rb") as reader, destination.open("wb" if args.overwrite else "xb") as writer:
            shutil.copyfileobj(reader, writer)

    for directory in args.diagram_dir:
        directory = expand(directory)
        if not directory.is_dir():
            report["warnings"].append(
                f"--diagram-dir {directory} is not a directory; skipped."
            )
            continue
        for source in sorted(directory.iterdir()):
            if source.is_file() and source.suffix.lower() in {
                ".png", ".svg", ".pdf", ".drawio", ".json",
            }:
                destination = asset_dir / f"{prefix}-{source.name}"
                copy_asset(source, destination)
                if source.suffix.lower() == ".svg":
                    from ltcore import fonts
                    destination.write_text(fonts.svg_with_stacks(source.read_text(encoding="utf-8"), cfg), encoding="utf-8")
                if destination not in asset_files:
                    asset_files.append(destination)

    media_dir = (
        expand(args.media_dir)
        if args.media_dir else args.module.parent / "media"
    )
    media_rows = {}
    page_figures = {}
    media_missing = []
    if media_dir.is_dir():
        on_disk = {}
        for source in sorted(media_dir.iterdir()):
            if source.is_file() and source.name.startswith(prefix + "-"):
                on_disk[source.stem[len(prefix) + 1:]] = source

        # Upload keys need a prefix because author images use an independent counter.
        author_keys = set()
        for page in module.get("pages", []):
            entries = [
                media
                for item in page.get("items", [])
                for media in (item.get("media") or [])
            ]
            entries += [
                media
                for content in (page.get("content") or [])
                for media in (content.get("media") or [])
            ]
            author_keys |= {
                str(entry["key"]) for entry in entries
                if entry.get("key") and entry.get("role") != "student-upload"
            }

        def take(entry, bucket_key, bucket):
            key = entry.get("key")
            if not key:
                return
            if entry.get("role") == "student-upload":
                source = on_disk.get(f"u{key}")
                if source is None and str(key) not in author_keys:
                    source = on_disk.get(str(key))
            else:
                source = on_disk.get(str(key))
            if source is None:
                media_missing.append(str(key))
                return
            destination = asset_dir / source.name
            if destination not in asset_files:
                copy_asset(source, destination)
                asset_files.append(destination)
                from ltcore import palettes
                manifest_path = palettes.copies_manifest(source)
                if manifest_path.exists():
                    manifest_destination = asset_dir / manifest_path.name
                    copy_asset(manifest_path, manifest_destination)
                    asset_files.append(manifest_destination)
                    for pid in palettes.pdf_palette_ids(cfg):
                        sibling = palettes.bound_copy(source, pid)
                        if sibling is None:
                            report["warnings"].append(f"{source.name}: unbound {pid} palette copy; run --file-copies")
                            continue
                        target = asset_dir / sibling.name
                        copy_asset(sibling, target)
                        asset_files.append(target)
            rows = bucket.setdefault(bucket_key, [])
            if any(row["name"] == destination.name for row in rows):
                return
            rows.append({
                "name": destination.name,
                "alt": entry.get("alt") or f"Lt image {key}",
                "role": entry.get("role"),
            })

        for page in module.get("pages", []):
            if excluded_page(page):
                continue
            for item in page.get("items", []):
                for entry in item.get("media", []) or []:
                    take(entry, item["item_id"], media_rows)
            for content in page.get("content", []) or []:
                for entry in content.get("media", []) or []:
                    take(entry, page["page_index"], page_figures)

        if media_missing:
            report["warnings"].append(
                f"{len(media_missing)} image key(s) the capture records were not found in "
                f"{media_dir} and are not in the export: "
                f"{', '.join(sorted(set(media_missing))[:8])}"
                + (" …" if len(set(media_missing)) > 8 else "")
                + ". Run the capture skill's media step on the pages that hold them."
            )
    elif args.media_dir:
        report["warnings"].append(
            f"--media-dir {media_dir} is not a directory; no Lt images were exported."
        )

    report["assets"] = [asset.name for asset in asset_files]
    report["media"] = {
        "dir": str(media_dir),
        "available": (
            sum(len(rows) for rows in media_rows.values())
            + sum(len(rows) for rows in page_figures.values())
        ),
        "keys_not_found": sorted(set(media_missing)),
    }

    by_name = {asset.name: asset for asset in asset_files}
    asset_rows = {}
    for ans in (answers or {}).get("items", []):
        for ref in ans.get("asset_refs", []) or []:
            base = Path(str(ref)).name
            hit = by_name.get(f"{prefix}-{base}") or by_name.get(base)
            svg = Path(base).with_suffix(".svg").name
            vector = by_name.get(f"{prefix}-{svg}") or by_name.get(svg)
            # Raster references prefer matching vectors while document references retain their roles.
            if hit and vector and hit.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                hit = vector
            if hit and hit.suffix.lower() in {".png", ".svg"}:
                if any(
                    row["name"] == hit.name
                    for row in asset_rows.get(ans["item_id"], [])
                ):
                    continue
                alt = (ans.get("asset_captions") or {}).get(ref) or base
                asset_rows.setdefault(ans["item_id"], []).append({
                    "name": hit.name, "alt": alt,
                })
            elif not hit:
                report["warnings"].append(
                    f"{ans['item_id']}: asset_ref {ref!r} was not found in any "
                "diagram folder; the export omitted this asset."
                )

    page_md, links = build_module_page(
        module, answers, fill, ilo, page_title, stamp_props, cfg, link,
        asset_rows, media_rows, page_figures, reconciled,
    )
    report["links"] = links
    assets_setting = str(cfg.get("notes.assets_dir")).replace("\\", "/")
    report["media"]["embedded"] = page_md.count(f"](../{assets_setting}/")

    def write_output(name, body, *, markdown=True):
        path = staging_path(out_dir, out_dir / output_filename(name))
        if markdown:
            verify_page_stamp(body, stamp_property, module_id)
        with path.open("w" if args.overwrite else "x", encoding="utf-8") as stream:
            stream.write(body)
        report["wrote"].append(str(path))
        return path

    if outputs["module_page"] or want_pdf or args.install:
        write_output("module-page.md", page_md)
    if outputs["ilo_map"]:
        write_output("ilo-map.md", build_ilo_map(module, answers, ilo, week, title, stamp_property))
    if outputs["tracker_handoff"]:
        write_output(
            "tracker-handoff.md",
            build_tracker_handoff(module, answers, ilo, week, title, fmt, page_title, stamp_property),
        )
    if outputs["flashcard_source"]:
        write_output(
            flashcard_name,
            build_flashcard_source(module, answers, ilo, week, title, cfg),
        )

    whiteboard_edn = None
    if outputs["whiteboard"]:
        if outputs["notes_graph"] and fmt == "logseq":
            whiteboard_edn = build_whiteboard(
                whiteboard_name, [page_title] + links, cfg,
                seed=f"{module_id}|" + "|".join(links),
            )
            write_output(whiteboard_filename, whiteboard_edn, markdown=False)
        else:
            report["warnings"].append(
                'export.outputs.whiteboard needs export.outputs.notes_graph and '
                'notes.format = "logseq"; no whiteboard was written'
            )

    for page in module.get("pages", []):
        reason = excluded_page(page)
        if reason:
            report["pages_skipped"].append({
                "page_index": page.get("page_index"),
                "title": page.get("title"),
                "reason": reason,
                "items": len(page.get("items", [])),
            })

    def named(*reasons):
        return [
            skipped for skipped in report["pages_skipped"]
            if skipped["reason"] in reasons
        ]

    def describe(pages):
        return "; ".join(
            f"page {page['page_index']} {page['title']!r} ({page['items']} items)"
            for page in pages
        )

    irat = named("irat-policy", "policy-skip:irat")
    if irat:
        report["warnings"].append(
            "iRAT page(s) excluded as closed-book work: "
            + describe(irat)
            + ". If one of these is a tRAT, re-run with --rat trat "
            "(or draft it in answers.json) and it is exported."
        )
    unclassified = named("rat-unclassified")
    if unclassified:
        report["warnings"].append(
            "RAT page(s) held back because their kind is unclassified: "
            + describe(unclassified)
            + ". A tRAT is exported and an iRAT is excluded. "
            "The capture does not distinguish them. Read the page content, "
            "then classify it with --rat trat or --rat irat, "
            "or record the decision in answers.json."
        )
    unavailable = named("policy-skip:trat-answers-unavailable")
    if unavailable:
        report["warnings"].append(
            "tRAT page(s) in scope but not answered, so not exported: "
            + describe(unavailable)
            + ". The answers come from the folders in rat.answers_dir and rat.answers_dirs and are never "
            "invented here. This clears when that file exists."
        )

    if want_pdf:
        import module_pdf

        pdf_dir = out_dir / "pdf"
        if args.json:
            with contextlib.redirect_stdout(sys.stderr):
                rc = module_pdf.build_pdfs(
                    out_dir / "module-page.md", pdf_dir, cfg, assets=[asset_dir]
                )
        else:
            rc = module_pdf.build_pdfs(
                out_dir / "module-page.md", pdf_dir, cfg, assets=[asset_dir]
            )
        report["pdf"] = {
            "dir": str(pdf_dir),
            "passed": rc == 0,
            "files": sorted(str(path) for path in pdf_dir.glob("*.pdf")),
        }
        if rc != 0:
            print(
                f"FATAL: the PDFs did not pass QC or their final pass (exit {rc}). "
                f"Nothing was installed. The page itself is in {out_dir / 'module-page.md'}.",
                file=sys.stderr,
            )
            return 1
        report["wrote"] += report["pdf"]["files"]

    targets = []
    if args.install:
        installed_page = page_md
        if fmt != "logseq":
            graph_assets = graph_directory(graph, cfg.get("notes.assets_dir"))
            assets_rel = os.path.relpath(
                graph_assets, installed_page_path.parent
            ).replace("\\", "/")
            installed_page = convert_outline(
                page_md, fmt, assets_rel, module_id, stamp_property
            )
        verify_page_stamp(installed_page, stamp_property, module_id)
        targets.append(Target(
            installed_page_path,
            installed_page,
            "notes page",
            make_probe_markdown(stamp_property),
        ))
        if whiteboard_edn is not None:
            whiteboards_dir = graph_directory(
                graph, cfg.get("notes.whiteboards_dir")
            )
            targets.append(Target(
                whiteboards_dir / whiteboard_filename,
                whiteboard_edn,
                "whiteboard seed",
                make_probe_whiteboard(namespace),
            ))
        asset_probe = make_probe_asset(prefix, module_id)
        graph_assets = graph_directory(graph, cfg.get("notes.assets_dir"))
        for asset in asset_files:
            destination = graph_assets / asset.name
            if not destination.resolve().is_relative_to(graph.resolve()):
                print(
                    "FATAL: asset target escapes the notes graph",
                    file=sys.stderr,
                )
                return 3
            targets.append(Target(
                destination, asset.read_bytes(), "asset", asset_probe
            ))

    for target in targets:
        if not target.path.resolve().is_relative_to(graph.resolve()):
            print(f"FATAL: {target.kind} target escapes the notes graph", file=sys.stderr)
            return 3
        target.classify(module_id, args.replace)
    blocked = [target for target in targets if target.action.startswith("blocked")]
    if blocked:
        for target in blocked:
            reason = (
                "carries this module's stamp; pass --replace to overwrite it"
                if target.action == "blocked-ours"
                else "exists and ownership cannot be proved; "
                "this file cannot be overwritten, with or without --replace"
            )
            print(
                f"BLOCKED {target.kind}: {target.path}\n  {reason}",
                file=sys.stderr,
            )
            report["blocked"].append({
                "path": str(target.path),
                "kind": target.kind,
                "action": target.action,
            })
            if target.diff:
                diff_path = write_output(target.path.name + ".diff", target.diff, markdown=False)
                print(
                    f"  diff written to {diff_path}; it shows your hand edits, "
                    "so read it before you discard it.",
                    file=sys.stderr,
                )
        print(f"\nNothing was written outside {out_dir}.", file=sys.stderr)
        if args.json:
            print(json.dumps(report, indent=1))
        return 4

    # Blocked targets stop before the run record writes outside export staging.
    record = build_run_record(
        module, answers, fill, asset_files,
        "; ".join(report["warnings"]) or None, cfg,
    )
    run_path = Path(st.run_path(module_id))
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(
        json.dumps(record, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result = validate_mod.validate_file(run_path, schema="run")
    if not result.ok:
        print_validation_errors(result)
        print(
            f"FATAL: the run record failed run.schema.json: {run_path}",
            file=sys.stderr,
        )
        return 2
    report["run_record"] = str(run_path)

    for target in targets:
        if target.action == "identical":
            continue
        target.path.parent.mkdir(parents=True, exist_ok=True)
        if target.action == "replace" and target.diff:
            write_output(target.path.name + ".diff", target.diff, markdown=False)
        if isinstance(target.content, bytes):
            target.path.write_bytes(target.content)
        else:
            target.path.write_text(target.content, encoding="utf-8")
        report["wrote"].append(str(target.path))

    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"module   {module_id}  —  {week} {title}")
        print(f"page     {page_title}")
        print(f"stamp    {stamp_property}:: {module_id}")
        print(
            "ILO      "
            + (
                f"{len(ilo['outcomes'])} outcomes from "
                f"{Path(ilo_setting).name}:{ilo['line_no']}"
                if ilo else (
                    "no section matched" if ilo_setting
                    else "curriculum.learning_outcomes_file is not set"
                )
            )
        )
        print(
            f"links    {len(links)} to existing graph pages"
            + (
                ": " + ", ".join(links[:8]) + ("…" if len(links) > 8 else "")
                if links else ""
            )
        )
        print(f"run record   {run_path}")
        for skipped in report["pages_skipped"]:
            print(
                f"skipped  page {skipped['page_index']:<3} "
                f"{skipped['reason']:<18} {(skipped['title'] or '')[:44]}  "
                f"({skipped['items']} item(s))"
            )
        for warning in report["warnings"]:
            print(f"⚠  {warning}")
        for path in report["wrote"]:
            print(f"wrote    {path}")
        if not args.install:
            print("\n--install was not passed: the notes graph was not touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
