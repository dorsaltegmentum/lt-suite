# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exit codes, output format, excerpts, path handling and file and folder names."""
from __future__ import annotations

import zipfile


def w(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_clean_file_exits_0(run_gate, tmp_path):
    r = run_gate("--paths", str(w(tmp_path / "a.txt", "nothing personal here\n")))
    assert r.returncode == 0 and r.stdout == ""


def test_hit_exits_1_with_path_line_col_id(run_gate, tmp_path):
    r = run_gate("--paths", str(w(tmp_path / "a.txt", "line one\n  see /home/someone/x\n")))
    assert r.returncode == 1
    assert r.stdout.splitlines() == ["a.txt:2:7 home-path"]


def test_no_excerpt_unless_asked(run_gate, tmp_path):
    f = w(tmp_path / "a.txt", "mail someone@mail.test now\n")
    plain = run_gate("--paths", str(f))
    assert "someone" not in plain.stdout and plain.returncode == 1
    loud = run_gate("--paths", str(f), "--excerpt")
    assert "someone@mail.test" in loud.stdout and loud.returncode == 1


def test_missing_path_exits_2(run_gate, tmp_path):
    r = run_gate("--paths", str(tmp_path / "nope.txt"))
    assert r.returncode == 2 and "no such file" in r.stderr


def test_bad_argument_exits_2(run_gate):
    assert run_gate("--no-such-flag").returncode == 2
    assert run_gate("--help").returncode == 0


def test_require_private_without_layer_exits_2(run_gate, tmp_path):
    f = w(tmp_path / "a.txt", "clean\n")
    r = run_gate("--require-private", "--paths", str(f))
    assert r.returncode == 2 and "require-private" in r.stderr
    r = run_gate("--require-private", "--paths", str(f), env={"LT_PRIVACY_DIR": str(tmp_path / "absent")})
    assert r.returncode == 2


def test_without_private_layer_prints_notice(run_gate, tmp_path):
    r = run_gate("--paths", str(w(tmp_path / "a.txt", "clean\n")))
    assert r.returncode == 0 and "private layer not loaded" in r.stderr


def test_corrupt_archive_exits_2_not_1(run_gate, tmp_path):
    bad = tmp_path / "broken.zip"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    r = run_gate("--paths", str(bad))
    assert r.returncode == 2 and "broken.zip" in r.stderr


def test_encrypted_member_exits_2(run_gate, tmp_path):
    z = tmp_path / "enc.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.txt", "x")
    data = bytearray(z.read_bytes())
    data[6] |= 1                       # local header: encrypted flag
    cd = data.rfind(b"PK\x01\x02")
    data[cd + 8] |= 1                  # central directory: encrypted flag
    z.write_bytes(bytes(data))
    assert run_gate("--paths", str(z)).returncode == 2


def test_bad_allowlist_exits_2(run_gate, tmp_path):
    f = w(tmp_path / "a.txt", "clean\n")
    for body in (
        '[[allow]]\npattern = "email"\n',                                   # no reason
        '[[allow]]\npattern = "nope"\nreason = "x"\n',                      # unknown id
        '[[allow]]\npattern = "*"\npath = "docs/**"\nreason = "x"\n',       # "*" outside the self paths
        '[[allow]]\npattern = "home-path"\npath = "docs/a.md"\nreason = "x"\n',  # never-allow id
        '[[allow]]\npattern = "private-3"\nreason = "x"\n',                 # numbered private hit
        '[[allow]]\npattern = "private:arbitrary"\nreason = "x"\n',        # global private bypass
        '[[allow]]\npattern = "email"\nreason = "x"\ncolour = "red"\n',     # unknown key
    ):
        al = w(tmp_path / "allow.toml", body)
        r = run_gate("--allowlist", str(al), "--paths", str(f))
        assert r.returncode == 2, body


def test_folder_walk_and_names(run_gate, tmp_path):
    root = tmp_path / "pkg"
    w(root / "ok.txt", "clean\n")
    w(root / "someone@mail.test" / "b.txt", "clean\n")
    w(root / "decisions" / "c.txt", "clean\n")
    r = run_gate("--paths", str(root))
    assert r.returncode == 1
    lines = r.stdout.splitlines()
    assert "pkg/someone@mail.test:0:5 email" in lines
    assert "pkg/decisions:0:5 private-doc" in lines      # a folder is scanned as "decisions/"
    assert len(lines) == 2


def test_paths_outside_repo_scan_only_their_own_names(run_gate, tmp_path):
    """The temp folder path itself never counts as a hit."""
    f = w(tmp_path / "deep" / "er" / "ok.txt", "clean\n")
    r = run_gate("--paths", str(f))
    assert r.returncode == 0 and r.stdout == ""


def test_symlink_is_scanned_as_its_target_text(run_gate, tmp_path):
    target = w(tmp_path / "clean.txt", "clean\n")
    link = tmp_path / "pkg" / "link.txt"
    link.parent.mkdir()
    link.symlink_to("/home/someone/clean.txt")
    assert run_gate("--paths", str(link)).stdout.split() == ["link.txt:1:1", "home-path"]
    link.unlink()
    link.symlink_to("../" + target.name)
    assert run_gate("--paths", str(link)).returncode == 0


def test_crlf_lines(run_gate, tmp_path, gate):
    rules = gate.load_rules(gate.PATTERNS_FILE)
    g = gate.Gate(rules, gate.load_allowlist(gate.ALLOWLIST_FILE, rules), [])
    assert g.scan_text("review_by: 2027-10-24\r\n", "x", "shared/lexicon/a.md") == []
    hits = g.scan_text("a\r\nb /home/someone/x\r\n", "x", "docs/a.md")
    assert [(h.line, h.col, h.pid) for h in hits] == [(2, 3, "home-path")]


def test_git_listing_modes(run_gate, tmp_path):
    import shutil
    import subprocess
    if not shutil.which("git"):
        import pytest
        pytest.skip("needs git")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    w(repo / ".gitignore", "ignored.txt\n")
    w(repo / "tracked.txt", "clean\n")
    w(repo / "ignored.txt", "/home/someone/x\n")
    w(repo / "untracked.txt", "someone@mail.test\n")
    for name in ("tracked.txt", ".gitignore"):
        oid = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", name],
                             check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "-C", str(repo), "update-index", "--add", "--cacheinfo",
                        "100644", oid, name], check=True)
    r = run_gate("--repo", str(repo))
    assert r.returncode == 0, r.stdout
    r = run_gate("--repo", str(repo), "--all")
    assert r.stdout.splitlines() == ["untracked.txt:1:1 email"]
    # Deleted in the working tree but still staged: the staged copy is what commits.
    w(repo / "tracked.txt", "/home/someone/x\n")
    oid = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "tracked.txt"],
                         check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "update-index", "--cacheinfo",
                    "100644", oid, "tracked.txt"], check=True)
    (repo / "tracked.txt").unlink()
    r = run_gate("--repo", str(repo))
    assert r.stdout.splitlines() == ["tracked.txt:1:1 home-path"]
