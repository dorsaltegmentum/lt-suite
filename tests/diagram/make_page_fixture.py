#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cut a built interactive page down to the fixture the JavaScript tests load.

    python3 tests/diagram/make_page_fixture.py <built page.html> [out.html]
    (default out: tests/diagram/fixtures/toy-case-page.html)

The page of the worked example (skills/mechanistic-diagram/examples/toy-case) keeps its figures, its
figure data and its markup. Three things go, so the fixture stays small and tests the engine's current
scripts: every base64 payload (the PDFs, the .drawio zip, the embedded fonts) becomes empty, every
@font-face rule goes, and the inline script that holds artifact_logic.js and artifact_ui.js becomes the
marker ENGINE_SCRIPTS, which page_fixture.js replaces with the engine's files when a test loads it.
tests/diagram/test_page_fixture.py checks the fixture against a fresh build of the worked example.
"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "fixtures" / "toy-case-page.html"
MARKER = "/* ENGINE_SCRIPTS */"
PAYLOAD = re.compile(r"(data:[a-z/+.-]+;base64,)[A-Za-z0-9+/=]+")
FONT_FACE = re.compile(r"@font-face\s*\{[^}]*\}\s*")
ENGINE_SCRIPT = re.compile(r"<script>\n(?:// SPDX[^\n]*\n)*/\* Mechanistic diagram Artifact: the page's pure logic.*?</script>",
                           re.S)


def strip(html):
    """The fixture text of a built page."""
    html = PAYLOAD.sub(r"\1", html)
    html = FONT_FACE.sub("", html)
    html, n = ENGINE_SCRIPT.subn("<script>" + MARKER + "</script>", html, count=1)
    if n != 1:
        raise SystemExit("make_page_fixture: the page has no inline engine script (artifact_logic.js)")
    return html


def main(argv):
    if not argv or len(argv) > 2:
        raise SystemExit(__doc__)
    src = pathlib.Path(argv[0])
    out = pathlib.Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    text = strip(src.read_text(encoding="utf-8"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}: {len(text)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
