# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ltcore.fonts: the bundled defaults, metrics, resolution order, fc-match
acceptance, static instances and pinned downloads. No test touches the network.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ltcore import fonts as F

REPO = Path(__file__).resolve().parents[2]
FONTS = REPO / "shared" / "fonts"
CAT = json.loads((FONTS / "catalog.json").read_text(encoding="utf-8"))
NOTO = FONTS / "noto-sans" / "NotoSans-Variable.ttf"
NOTO_MONO = FONTS / "noto-sans-mono" / "NotoSansMono-Variable.ttf"


class Cfg(dict):
    """A stand-in for ltcore.config.Config: dotted keys in a flat dict."""
    path = None

    def get(self, key, default=None):
        return dict.get(self, key, default)


# --------------------------------------------------------------------------- bundle
def test_bundled_defaults_match_catalog_and_carry_ofl():
    meta = F.catalog_meta()
    assert meta["defaults"]["sans"] == "noto-sans" and meta["defaults"]["mono"] == "noto-sans-mono"
    total = 0
    for fid in ("noto-sans", "noto-sans-mono"):
        e = F.catalog()[fid]
        for f in e["bundled"]["files"]:
            p = FONTS / f["file"]
            data = p.read_bytes()
            total += len(data)
            assert hashlib.sha256(data).hexdigest() == f["sha256"]
            assert hashlib.sha256(data).hexdigest() == e["fetch"]["files"][0]["sha256"], \
                "the bundled file is the upstream file, byte for byte"
        ofl = (FONTS / e["bundled"]["licence_file"]).read_text(encoding="utf-8")
        assert ofl.startswith("Copyright 2022 The Noto Project Authors")
        assert "SIL OPEN FONT LICENSE Version 1.1" in ofl
        assert e["licence"]["reserved_font_names"] == []
    assert total <= 4 * 1024 * 1024, "font budget per skill is 4 MB"


def test_only_the_two_defaults_are_bundled():
    files = sorted(p.relative_to(FONTS).as_posix() for p in FONTS.rglob("*") if p.is_file())
    assert files == ["catalog.json", "noto-sans-mono/NotoSansMono-Variable.ttf", "noto-sans-mono/OFL.txt",
                     "noto-sans/NotoSans-Variable.ttf", "noto-sans/OFL.txt"]


def test_catalog_fetch_entries_are_pinned():
    for e in CAT["fonts"]:
        spec = e.get("fetch")
        if not spec:
            continue
        for f in spec["files"] + [spec["licence"]]:
            assert f["url"].startswith("https://raw.githubusercontent.com/google/fonts/")
            assert len(f["url"].split("/")[5]) == 40, "pinned to a commit"
            assert len(f["sha256"]) == 64


def test_catalog_corrections_from_review():
    by = {e["id"]: e for e in CAT["fonts"]}
    assert "preference" in by["noto-sans"]["readability_evidence"]
    assert "no population-level font effect" in by["noto-sans"]["readability_evidence"]
    assert by["noto-sans"]["preinstalled_default_count"] == 2
    assert by["noto-sans-mono"]["preinstalled_default_count"] == 2
    assert "cascadia-mono" in by and by["cascadia-mono"]["licence"]["reserved_font_names"] == ["Cascadia Code"]
    assert CAT["defaults"]["low_vision"]["sans"] == "atkinson-hyperlegible-next"


# --------------------------------------------------------------------------- reading
def test_read_info_of_bundled_fonts():
    i = F.read_info(NOTO)
    assert i.family == "Noto Sans" and i.variable
    assert i.axes["wght"] == (100.0, 400.0, 900.0)
    assert i.units_per_em == 1000
    m = F.read_info(NOTO_MONO)
    assert m.family == "Noto Sans Mono"


def test_metrics_reproduce_the_old_noto_constants():
    """The engine used (LH - 1.362 FS) / 2 + 1.069 FS for Noto Sans at 14 px in a 16.8 px line."""
    m = F.metrics(NOTO, 14, 16.8)
    assert m["ascender"] == pytest.approx(1.069 * 14, abs=1e-6)
    assert m["ascender"] + m["descender"] == pytest.approx(1.362 * 14, abs=1e-6)
    assert m["baseline_offset"] == pytest.approx((16.8 - 1.362 * 14) / 2 + 1.069 * 14, abs=1e-6)


def test_read_info_rejects_non_fonts(tmp_path):
    p = tmp_path / "x.ttf"
    p.write_bytes(b"not a font at all")
    with pytest.raises(F.FontError):
        F.read_info(p)


# --------------------------------------------------------------------------- resolve order
def test_resolve_default_is_bundled():
    f = F.resolve("sans")
    assert f.path == NOTO and f.source == "bundled" and not f.fallback
    assert F.resolve("mono").path == NOTO_MONO


def test_resolve_returns_a_path_that_carries_the_lookup():
    f = F.resolve("sans", weight=600)
    assert isinstance(f, Path) and f == NOTO and str(f) == str(NOTO)
    assert f.path == NOTO and type(f.path) is not F.FontPath
    assert f.font.source == "bundled" and f.family == "Noto Sans" and f.font_id == "noto-sans"
    assert f.source_file == NOTO and f.variable
    assert f.parent == NOTO.parent      # a derived path is a plain path
    with pytest.raises(AttributeError):
        f.no_such_attribute


def test_resolve_takes_config_by_position_or_keyword(tmp_path):
    p = tmp_path / "Mine.ttf"
    shutil.copy(NOTO_MONO, p)
    cfg = Cfg({"fonts.sans_path": str(p)})
    assert F.resolve("sans", cfg, weight=700) == p
    assert F.resolve("sans", 700, cfg=cfg) == p
    assert F.resolve("sans", weight=700, italic=False, cfg=cfg) == p
    assert F.resolve("sans", None, weight=700) == NOTO
    with pytest.raises(TypeError):
        F.resolve("sans", cfg, cfg)


def test_resolve_static_cuts_an_instance_at_the_weight(tmp_path):
    pytest.importorskip("fontTools")
    f = F.resolve("sans", weight=600, static=True, cache_dir=tmp_path)
    info = F.read_info(f)
    assert info.family == "NotoSansStatic" and info.weight == 600 and not info.variable
    assert f.source_file == NOTO and f.family == "Noto Sans"
    b = F.resolve("mono_bold", static=True, cache_dir=tmp_path)
    assert F.read_info(b).weight == 700 and F.read_info(b).family == "NotoSansMonoStatic"


def test_family_names_the_configured_font():
    assert F.family("sans") == "Noto Sans"
    assert F.family("mono", Cfg({"fonts.mono": "Noto Sans Mono"})) == "Noto Sans Mono"
    assert F.family("mono_bold") == "Noto Sans Mono"


def test_explicit_path_beats_everything(tmp_path):
    p = tmp_path / "Mine.ttf"
    shutil.copy(NOTO_MONO, p)
    f = F.resolve("sans", Cfg({"fonts.sans_path": str(p), "fonts.sans": "Noto Sans"}))
    assert f.path == p and f.source == "config fonts.sans_path"
    f = F.resolve("sans", Cfg({"fonts.sans": str(p)}))
    assert f.path == p and "a file" in f.source
    with pytest.raises(F.FontError, match="does not exist"):
        F.resolve("sans", Cfg({"fonts.sans_path": str(tmp_path / "missing.ttf")}))


def test_mono_bold_path_only_for_bold(tmp_path):
    b = tmp_path / "Bold.ttf"
    shutil.copy(NOTO_MONO, b)
    cfg = Cfg({"fonts.mono_bold_path": str(b)})
    assert F.resolve("mono", cfg, weight=700).path == b
    assert F.resolve("mono", cfg, weight=400).path == NOTO_MONO


def test_bundled_beats_data_folder_and_system(tmp_path):
    (tmp_path / "fonts" / "noto-sans").mkdir(parents=True)
    shutil.copy(NOTO, tmp_path / "fonts" / "noto-sans" / "NotoSans-Variable.ttf")
    calls = []

    def runner(*a, **k):
        calls.append(a)
        raise AssertionError("fc-match must not run when a bundled copy exists")

    f = F.resolve("sans", data_dir=tmp_path, runner=runner)
    assert f.source == "bundled" and calls == []


def test_data_folder_beats_system(tmp_path, monkeypatch):
    font_dir = tmp_path / "fonts" / "atkinson-hyperlegible-next"
    font_dir.mkdir(parents=True)
    shutil.copy(NOTO, font_dir / "AtkinsonHyperlegibleNext[wght].ttf")   # any font file will do
    monkeypatch.setattr(F, "fc_match", lambda *a, **k: pytest.fail("system looked up first"))
    f = F.resolve("sans", Cfg({"fonts.sans": "Atkinson Hyperlegible Next"}), data_dir=tmp_path)
    assert f.source.startswith("data folder") and f.font_id == "atkinson-hyperlegible-next"


def test_system_lookup_then_fallback(tmp_path, monkeypatch):
    sysfont = tmp_path / "Some-Regular.ttf"
    shutil.copy(NOTO, sysfont)
    monkeypatch.setattr(F, "fc_match", lambda fam, *a, **k: sysfont if fam == "Some Family" else None)
    monkeypatch.setattr(F, "scan_family", lambda fam, dirs=None: [])
    f = F.resolve("sans", Cfg({"fonts.sans": "Some Family"}), data_dir=tmp_path)
    assert f.path == sysfont and f.source == "fc-match"
    f = F.resolve("sans", Cfg({"fonts.sans": "Missing Family"}), data_dir=tmp_path)
    assert f.fallback and f.path == NOTO and "Missing Family not found" in f.source
    with pytest.raises(F.FontError, match="not found"):
        F.resolve("sans", Cfg({"fonts.sans": "Missing Family"}), data_dir=tmp_path, fallback=False)


def test_platform_scan_after_fc_match(tmp_path, monkeypatch):
    d = tmp_path / "share"
    d.mkdir()
    shutil.copy(NOTO_MONO, d / "NotoSansMono-Regular.ttf")
    monkeypatch.setattr(F, "fc_match", lambda *a, **k: None)
    assert F.scan_family("Noto Sans Mono", [d]) == [d / "NotoSansMono-Regular.ttf"]
    monkeypatch.setattr(F, "scan_family", lambda fam, dirs=None: [d / "NotoSansMono-Regular.ttf"])
    f = F.resolve("mono", family="Unlisted Mono Family", data_dir=tmp_path)
    assert f.source == "platform font folder"


def test_fc_match_rejects_a_substituted_family(monkeypatch, tmp_path):
    p = tmp_path / "DejaVuSans.ttf"
    shutil.copy(NOTO, p)
    monkeypatch.setattr(F.shutil, "which", lambda name: "/usr/bin/fc-match")

    def runner(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, stdout=f"{p}\nDejaVu Sans", stderr="")

    assert F.fc_match("Adwaita Sans", runner=runner) is None
    assert F.fc_match("DejaVu Sans", runner=runner) == p


def test_fc_match_absent(monkeypatch):
    monkeypatch.setattr(F.shutil, "which", lambda name: None)
    assert F.fc_match("Noto Sans") is None


# --------------------------------------------------------------------------- static instances
def test_static_instance_renames_family_and_sets_weight(tmp_path):
    pytest.importorskip("fontTools")
    out = F.static_instance(NOTO, weight=700, cache_dir=tmp_path)
    i = F.read_info(out)
    assert i.family == "NotoSansStatic" and i.weight == 700 and not i.variable
    assert out.name == "NotoSansStatic-Bold.ttf"
    again = F.static_instance(NOTO, weight=700, cache_dir=tmp_path)
    assert again == out


def test_face_file_cuts_variable_and_keeps_reserved_names(tmp_path, monkeypatch):
    pytest.importorskip("fontTools")
    p, font = F.face_file("sans", weight=500, cache_dir=tmp_path)
    assert font.variable and F.read_info(p).weight == 500
    real = F.find_entry

    def with_rfn(name):
        e = real(name)
        if e and e["id"] == "noto-sans":
            e = dict(e, licence=dict(e["licence"], reserved_font_names=["Noto"]))
        return e

    monkeypatch.setattr(F, "find_entry", with_rfn)
    p2, _ = F.face_file("sans", weight=500, cache_dir=tmp_path / "x")
    assert p2 == NOTO, "a font with a Reserved Font Name is never cut"


def test_fontconfig_file_lists_dirs(tmp_path):
    conf = F.fontconfig_file([tmp_path / "a", tmp_path / "b"], tmp_path)
    text = conf.read_text(encoding="utf-8")
    assert "/etc/fonts/fonts.conf" in text and str((tmp_path / "a").resolve()) in text


# --------------------------------------------------------------------------- Pillow
def test_pillow_font_sets_weight():
    pytest.importorskip("PIL")
    f = F.pillow_font("sans", 20, weight=700)
    assert f.lt_default is False
    reg = F.pillow_font("sans", 20, weight=400)
    assert f.getlength("Ammonia") > reg.getlength("Ammonia")


def test_pillow_font_default_only_when_nothing_resolves(tmp_path):
    pytest.importorskip("PIL")
    f = F.pillow_font("sans", 12, path=tmp_path / "missing.ttf")
    assert f.lt_default is True
    with pytest.raises(F.FontError):
        F.pillow_font("sans", 12, path=tmp_path / "missing.ttf", allow_default=False)


# --------------------------------------------------------------------------- fetch
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _entry_with_fetch(data: bytes, lic: bytes) -> dict:
    return {"id": "test-font", "family": "Test Font", "role": "sans", "licence": {},
            "fetch": {"files": [{"url": "https://example.org/a.ttf", "sha256": hashlib.sha256(data).hexdigest(),
                                 "file": "a.ttf"}],
                      "licence": {"url": "https://example.org/OFL.txt", "sha256": hashlib.sha256(lic).hexdigest(),
                                  "file": "OFL.txt"}}}


def test_fetch_checks_sha256_and_writes(tmp_path, monkeypatch):
    data, lic = NOTO_MONO.read_bytes(), b"licence text"
    monkeypatch.setattr(F, "catalog", lambda: {"test-font": _entry_with_fetch(data, lic)})
    bodies = {"https://example.org/a.ttf": data, "https://example.org/OFL.txt": lic}
    out = F.fetch("test-font", dest_root=tmp_path, opener=lambda url, timeout: _Resp(bodies[url]))
    assert sorted(p.name for p in out) == ["OFL.txt", "a.ttf"]
    assert (tmp_path / "test-font" / "a.ttf").read_bytes() == data


def test_fetch_mismatch_writes_nothing(tmp_path, monkeypatch):
    data, lic = NOTO_MONO.read_bytes(), b"licence text"
    monkeypatch.setattr(F, "catalog", lambda: {"test-font": _entry_with_fetch(data, lic)})
    with pytest.raises(F.FontError, match="sha256 mismatch"):
        F.fetch("test-font", dest_root=tmp_path, opener=lambda url, timeout: _Resp(b"tampered"))
    assert not (tmp_path / "test-font").exists()


def test_fetch_refused_without_permission_or_https(tmp_path, monkeypatch):
    data = b"x"
    e = _entry_with_fetch(data, b"l")
    monkeypatch.setattr(F, "catalog", lambda: {"test-font": e})
    with pytest.raises(F.FontError, match="allow_download is false"):
        F.fetch("test-font", Cfg({"fonts.allow_download": False}), dest_root=tmp_path)
    e["fetch"]["files"][0]["url"] = "http://example.org/a.ttf"
    with pytest.raises(F.FontError, match="non-https"):
        F.fetch("test-font", dest_root=tmp_path, opener=lambda url, timeout: _Resp(data))


def test_fetch_unpinned_font_points_to_packages():
    with pytest.raises(F.FontError, match="package manager"):
        F.fetch("adwaita-sans", dest_root=Path("/nonexistent"))
