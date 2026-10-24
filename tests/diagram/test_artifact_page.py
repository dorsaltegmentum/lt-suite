# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The interactive page of the worked example is one standalone HTML document: complete head, plain
<a download> links with data URIs, inline scripts only, no host-specific save interface (check_artifact.py)."""
import check_artifact as A


def palettes(case):
    return len([p for p in case.glob("final-*.svg") if ".static" not in p.name])


def test_toy_page_passes(toy_build):
    import build
    page = toy_build / "toy-case.html"
    assert palettes(toy_build) >= 2
    # the zipped .drawio files carry the build's SOURCE_DATE_EPOCH (the newest source mtime)
    assert A.check(page, epoch=build.source_epoch(toy_build), palettes=palettes(toy_build)) == []


def test_page_title_and_head(toy_build):
    head = (toy_build / "toy-case.html").read_text(encoding="utf-8")[:2000]
    assert head.startswith("<!doctype html>")
    assert "<title>Fictional enzyme X deficiency: mechanism</title>" in head


def mutated(toy_copy, old, new):
    page = toy_copy / "toy-case.html"
    text = page.read_text(encoding="utf-8")
    assert old in text
    page.write_text(text.replace(old, new, 1), encoding="utf-8")
    return A.check(page, palettes=palettes(toy_copy))


def test_a_fragment_fails(toy_copy):
    bad = mutated(toy_copy, "<!doctype html>", "")
    assert any("doctype" in b for b in bad)


def test_an_external_script_fails(toy_copy):
    bad = mutated(toy_copy, "</body>", '<script src="https://example.org/x.js"></script></body>')
    assert any("external script" in b for b in bad)


def test_a_host_save_interface_fails(toy_copy):
    bad = mutated(toy_copy, "</body>", "<script>window.claude && 1;</script></body>")
    assert any("host-specific" in b for b in bad)


def test_a_download_without_the_attribute_fails(toy_copy):
    bad = mutated(toy_copy, ' download="toy-case-drawio.zip"', "")
    assert any("plain <a download>" in b for b in bad)
