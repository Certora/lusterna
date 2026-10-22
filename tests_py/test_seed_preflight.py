"""Unit tests for the input-target pre-flight (`container.seed_ref_or_fail`).

The target must be a full, non-shallow git repository with a resolvable seed; a bare directory or a
shallow clone is a HARD STOP at run start — before any container work — not an unrecoverable export
failure after a whole campaign. Local git only, no Docker.
"""
import subprocess

import pytest

from lusterna import container as C


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


def _repo(path, commits=1):
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    for i in range(commits):
        (path / "f.txt").write_text(f"v{i}\n")
        _git(path, "add", "-A")
        _git(path, "commit", "-q", "-m", f"c{i}")
    return path


def test_bare_directory_is_rejected(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    (d / "lib.rs").write_text("fn main() {}\n")
    with pytest.raises(RuntimeError, match="not a git repository"):
        C.seed_ref_or_fail(d, None)


def test_unborn_head_is_rejected(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    _git(d, "init", "-q")
    with pytest.raises(RuntimeError, match="no commits yet"):
        C.seed_ref_or_fail(d, None)


def test_shallow_clone_is_rejected(tmp_path):
    origin = _repo(tmp_path / "origin", commits=3)
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", origin.as_uri(), str(shallow)], check=True)
    assert _git_out(shallow, "rev-parse", "--is-shallow-repository") == "true"   # precondition
    with pytest.raises(RuntimeError, match="SHALLOW"):
        C.seed_ref_or_fail(shallow, None)


def test_full_repo_defaults_to_head(tmp_path):
    assert C.seed_ref_or_fail(_repo(tmp_path / "r"), None) == "HEAD"


def test_named_branch_is_returned(tmp_path):
    r = _repo(tmp_path / "r")
    _git(r, "branch", "feature")
    assert C.seed_ref_or_fail(r, "feature") == "feature"


def test_unresolvable_branch_is_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="does not resolve"):
        C.seed_ref_or_fail(_repo(tmp_path / "r"), "nonexistent")


def _git_out(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True).stdout.strip()
