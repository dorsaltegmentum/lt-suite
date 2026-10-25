# SPDX-FileCopyrightText: 2026 dorsaltegmentum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Commit objects expose identity, offsets, trailers, old names and deleted blobs."""
import subprocess

import pytest

IDENTITY = "dorsaltegmentum <208164922+dorsaltegmentum@users.noreply.github.com>"


def git(repo, *args, data=None):
    return subprocess.run(["git", "-C", str(repo), *args], input=data,
                          capture_output=True, check=True).stdout.strip().decode()


def object_commit(repo, *, author=IDENTITY, committer=IDENTITY, author_offset="+0000",
                  committer_offset="+0000", message="Synthetic change", text="clean", parent=None,
                  name="old.txt"):
    """Build scratch Git objects directly, without staging or running porcelain commits."""
    blob = git(repo, "hash-object", "-w", "--stdin", data=text.encode())
    tree = git(repo, "mktree", data=f"100644 blob {blob}\t{name}\n".encode())
    raw = f"tree {tree}\n" + (f"parent {parent}\n" if parent else "")
    raw += (f"author {author} 946684800 {author_offset}\n"
            f"committer {committer} 946684800 {committer_offset}\n\n{message}\n")
    return git(repo, "hash-object", "-t", "commit", "-w", "--stdin", data=raw.encode())


@pytest.fixture
def scratch_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    return repo


@pytest.mark.parametrize("mutation,pattern", [
    ({"author": "Other Author <a@example.org>"}, "commit-author"),
    ({"committer": "Other Committer <a@example.org>"}, "commit-committer"),
    ({"author_offset": "+1100"}, "commit-offset"),
    ({"committer_offset": "+1100"}, "commit-offset"),
    ({"message": "Synthetic\n\nClaude-Session: opaque"}, "agent-trailer"),
    ({"message": "Synthetic\n\nCo-Authored-By: Assistant <a@example.org>"}, "agent-trailer"),
    ({"message": "Synthetic\n\nSigned-off-by: " + IDENTITY}, "commit-trailer"),
])
def test_history_rejects_identity_offset_and_trailers(run_gate, scratch_repo, mutation, pattern):
    sha = object_commit(scratch_repo, **mutation)
    git(scratch_repo, "update-ref", "refs/heads/main", sha)
    r = run_gate("--repo", str(scratch_repo), "--history")
    assert r.returncode == 1 and pattern in r.stdout, r.stdout + r.stderr


def test_history_reads_deleted_blobs_and_old_names(run_gate, scratch_repo):
    first = object_commit(scratch_repo, text="someone@mail.test", name="other@mail.test")
    last = object_commit(scratch_repo, parent=first)
    git(scratch_repo, "update-ref", "refs/heads/main", last)
    r = run_gate("--repo", str(scratch_repo), "--history")
    assert r.returncode == 1 and "other@mail.test@" in r.stdout
    assert ":0:" in r.stdout and "2 commits" in r.stderr


def test_clean_history_and_private_commit_message(run_gate, scratch_repo, tmp_path):
    sha = object_commit(scratch_repo)
    git(scratch_repo, "update-ref", "refs/heads/main", sha)
    assert run_gate("--repo", str(scratch_repo), "--history").returncode == 0
    last = object_commit(scratch_repo, parent=sha, message="synthetic-owner-91")
    git(scratch_repo, "update-ref", "refs/heads/main", last)
    private = tmp_path / "private"
    private.mkdir()
    (private / "private-patterns.txt").write_text("synthetic-owner-91\n", encoding="utf-8", newline="\n")
    r = run_gate("--repo", str(scratch_repo), "--history", "--require-private",
                 env={"LT_PRIVACY_DIR": str(private)})
    assert r.returncode == 1 and r.stdout.startswith("private-")
    assert "synthetic-owner" not in r.stdout + r.stderr


@pytest.mark.parametrize('footer', ['Reviewed-by:Example', 'Reviewed-by: Example\n continuation'])
def test_history_uses_git_trailer_semantics(run_gate, scratch_repo, footer):
    """Git recognizes trailers without whitespace after the separator."""
    sha = object_commit(scratch_repo, message='Synthetic change\n\n' + footer)
    git(scratch_repo, 'update-ref', 'refs/heads/main', sha)
    r = run_gate('--repo', str(scratch_repo), '--history')
    assert r.returncode == 1 and 'commit-trailer' in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize('ref', ['v0.1.0', 'initial-public'])
def test_public_history_accepts_contributor_identity_offset_and_dco_after_baseline(run_gate, scratch_repo, ref):
    """Public PR checks retain the initial baseline and accept later DCO identities."""
    first = object_commit(scratch_repo)
    git(scratch_repo, 'update-ref', 'refs/tags/' + ref, first)
    last = object_commit(scratch_repo, parent=first,
                         author='Contributor <person@contributor.test>',
                         committer='Contributor <person@contributor.test>',
                         author_offset='+1100', committer_offset='-0800',
                         message='Synthetic change\n\nSigned-off-by: Contributor <person@contributor.test>')
    git(scratch_repo, 'update-ref', 'refs/heads/main', last)
    r = run_gate('--repo', str(scratch_repo), '--public-history', '--initial-release-ref', ref)
    assert r.returncode == 0, r.stdout + r.stderr
    assert '2 commits' in r.stderr
    assert run_gate('--repo', str(scratch_repo), '--history').returncode == 1


@pytest.mark.parametrize('baseline', [False, True])
def test_public_history_keeps_initial_identity_strict(run_gate, scratch_repo, baseline):
    first = object_commit(scratch_repo, author='Other <a@example.org>')
    git(scratch_repo, 'update-ref', 'refs/heads/main', first)
    if baseline:
        git(scratch_repo, 'update-ref', 'refs/tags/v0.1.0', first)
    r = run_gate('--repo', str(scratch_repo), '--public-history')
    assert r.returncode == 1 and 'commit-author' in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize('message,text,pattern', [
    ('Synthetic\n\nClaude-Session:opaque', 'clean', 'agent-trailer'),
    ('Synthetic\n\nReviewed-by:Example', 'clean', 'commit-trailer'),
    ('See /home/someone/x', 'clean', 'home-path'),
    ('Synthetic', '/home/someone/x', 'home-path'),
    ('Contact person@contributor.test\n\nSigned-off-by: Contributor <person@contributor.test>', 'clean', 'email'),
])
def test_public_history_still_scans_later_leaks(run_gate, scratch_repo, message, text, pattern):
    first = object_commit(scratch_repo)
    git(scratch_repo, 'update-ref', 'refs/tags/v0.1.0', first)
    last = object_commit(scratch_repo, parent=first, message=message, text=text)
    git(scratch_repo, 'update-ref', 'refs/heads/main', last)
    r = run_gate('--repo', str(scratch_repo), '--public-history')
    assert r.returncode == 1 and pattern in r.stdout, r.stdout + r.stderr


def test_public_history_keeps_unrelated_roots_strict(run_gate, scratch_repo):
    """An unrelated branch must not inherit post-release identity exceptions."""
    first = object_commit(scratch_repo)
    git(scratch_repo, 'update-ref', 'refs/tags/v0.1.0', first)
    other = object_commit(scratch_repo, author='Other <a@example.org>')
    git(scratch_repo, 'update-ref', 'refs/heads/main', other)
    r = run_gate('--repo', str(scratch_repo), '--public-history')
    assert r.returncode == 1 and 'commit-author' in r.stdout, r.stdout + r.stderr


def test_public_history_never_reads_private_inputs(run_gate, scratch_repo, tmp_path):
    """Public CI must not parse local private patterns or private environment secrets."""
    first = object_commit(scratch_repo)
    git(scratch_repo, 'update-ref', 'refs/heads/main', first)
    private = tmp_path / 'private'
    private.mkdir()
    (private / 'private-patterns.txt').write_text('[invalid regex\n', encoding="utf-8", newline="\n")
    r = run_gate('--repo', str(scratch_repo), '--public-history',
                 env={'LT_PRIVACY_DIR': str(private), 'PRIVACY_PATTERNS_B64': 'invalid secret'})
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_gate('--repo', str(scratch_repo), '--public-history', '--require-private')
    assert r.returncode == 2, r.stdout + r.stderr
