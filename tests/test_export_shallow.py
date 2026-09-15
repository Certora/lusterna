"""Regression test for `container.export_branch` over a SHALLOW seed.

A campaign against a shallow clone (`git clone --depth 1`, e.g. ~/src/payment-channels) lost its whole
run: the in-container repo carried the seed tip but not its parent, so the old full-history bundle
(`git bundle create <branch> <pristine>`) aborted with "Failed to traverse parents", the export was
skipped, and the `--rm` container took the only copy of the proofs and report with it.

The fix ships only the RUN-RELATIVE delta as a thin bundle (`^PRISTINE_REF <branch>`), which never
walks below the pristine boundary, and returns True only once the tip is confirmed on the host.

These tests run the REAL `export_branch` against local git repos — `exec_in`/`_docker` are redirected
to a temp "container" repo, so no Docker or image is needed. The shallow boundary is reproduced
deterministically by dropping `.git/shallow` from a depth-1 clone: the tip is present, its parent
object is genuinely absent, and any traversal below it fails on every git version.
"""
import shutil
import subprocess

import pytest

from lusterna import container as C


def _git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=check)


def _commit(cwd, path, text, msg):
    dst = cwd / path
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text)
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", msg)


SID = "test-shallow-0000"
CID = "fake-container"


@pytest.fixture
def shallow_world(tmp_path, monkeypatch):
    """origin (3 commits) → host = depth-1 shallow clone (the delivery target) → cont = the broken
    in-container repo (seed tip present, parent absent, no shallow marker) with one run commit on top."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    for i in range(3):
        _commit(origin, "f.txt", f"v{i}\n", f"c{i}")

    host = tmp_path / "host"
    subprocess.run(["git", "clone", "-q", "--depth", "1", origin.as_uri(), str(host)], check=True)
    assert _git(host, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"

    # The in-container repo, reproduced deterministically: a depth-1 clone whose `.git/shallow` marker
    # is removed → git believes it has full history but the parent object is simply missing.
    cont = tmp_path / "cont"
    subprocess.run(["git", "clone", "-q", "--depth", "1", origin.as_uri(), str(cont)], check=True)
    _git(cont, "config", "user.email", "t@t")
    _git(cont, "config", "user.name", "t")
    (cont / ".git" / "shallow").unlink(missing_ok=True)
    seed_sha = _git(cont, "rev-parse", "HEAD").stdout.strip()
    branch = C.run_branch(SID)
    _git(cont, "update-ref", C.PRISTINE_REF, seed_sha)
    _git(cont, "checkout", "-q", "-b", branch)
    _commit(cont, "verification/proof.lean", "theorem t : True := trivial\n", "feat(prove): artifacts")
    tip = _git(cont, "rev-parse", branch).stdout.strip()

    bundle_tmp = str(tmp_path / "cont.bundle")

    def fake_exec_in(container_id, cmd, workdir=C.REPO_IN, **kw):
        cmd = [bundle_tmp if a == "/workspace/lusterna.bundle" else a for a in cmd]
        r = subprocess.run(cmd, cwd=str(cont), capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def fake_docker(*args, check=True):
        if args and args[0] == "cp":  # `cp <cid>:/workspace/lusterna.bundle <host>`
            shutil.copy(bundle_tmp, args[2])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(C, "exec_in", fake_exec_in)
    monkeypatch.setattr(C, "_docker", fake_docker)

    return host, cont, branch, tip, seed_sha


def test_old_full_history_bundle_fails_on_shallow_seed(shallow_world):
    """Witness the original failure: bundling full history cannot traverse the absent parent."""
    host, cont, branch, tip, seed_sha = shallow_world
    r = _git(cont, "bundle", "create", str(host / "old.bundle"), branch, C.PRISTINE_REF, check=False)
    assert r.returncode != 0
    assert "traverse" in (r.stderr + r.stdout).lower() or "could not read" in (r.stderr + r.stdout).lower()


def test_export_survives_shallow_seed(shallow_world):
    """The fix delivers the run and confirms it on the host despite the shallow boundary."""
    host, cont, branch, tip, seed_sha = shallow_world

    assert C.export_branch(CID, host, SID) is True

    # the run tip landed on the host, carrying the artefact
    assert _git(host, "rev-parse", branch).stdout.strip() == tip
    assert "theorem t" in _git(host, "show", f"{branch}:verification/proof.lean").stdout
    # base is anchored at the pristine root, so the review diff is exactly the run's delta
    assert _git(host, "rev-parse", f"{branch}-base").stdout.strip() == seed_sha
    names = _git(host, "diff", "--name-only", f"{branch}-base", branch).stdout.split()
    assert names == ["verification/proof.lean"]


def test_export_returns_false_when_tip_absent(shallow_world, monkeypatch):
    """A container missing the run branch must return False (so the caller keeps it alive), never True."""
    host, cont, branch, tip, seed_sha = shallow_world
    real = C.exec_in

    def drop_branch(container_id, cmd, workdir=C.REPO_IN, **kw):
        if cmd[:3] == ["git", "rev-parse", "--verify"] and cmd[-1] == branch:
            return 1, "", "fatal: needed a single revision"
        return real(container_id, cmd, workdir, **kw)

    monkeypatch.setattr(C, "exec_in", drop_branch)
    assert C.export_branch(CID, host, SID) is False
