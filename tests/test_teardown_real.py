"""Integration test for confirm-before-teardown, against a REAL container.

The fix's guarantee: a run container (started with `--rm`, so a stop DELETES it and the only copy of
its proofs/report) is torn down ONLY when a completed run is also CONFIRMED exported to the host. A
completed run whose export cannot be confirmed must be kept ALIVE for `--session-id` recovery, not
silently discarded.

This drives the real `container.finalize()` decision against a real container — it starts one, seeds a
run branch through the real `seed_from_ref`, and asserts the container's actual lifecycle outcome
(`is_running`) in three cases. It needs Docker and the lusterna-toolchain image, so it SKIPS cleanly
in the container-free `pytest tests/` environment (like tests/checklean/verify.py, which is the other
toolchain-backed check). Run it explicitly with:  pytest tests/test_teardown_real.py
"""
import atexit
import shutil
import subprocess

import pytest

from lusterna import container as C


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    if subprocess.run(["docker", "ps"], capture_output=True).returncode != 0:
        return False
    return subprocess.run(["docker", "image", "inspect", C.DEFAULT_IMAGE],
                          capture_output=True).returncode == 0


pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not _docker_available(),
                                 reason="needs Docker + the lusterna-toolchain image")]

SID = "test-teardown-real"


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def seeded(tmp_path):
    """A real container seeded from a host repo, with a run commit ('the artefacts') on the run branch.
    Yields (container_id, host, branch, tip). The container is force-removed on teardown."""
    host = tmp_path / "host"
    host.mkdir()
    _git(host, "init", "-q")
    _git(host, "config", "user.email", "t@t")
    _git(host, "config", "user.name", "t")
    (host / "src.txt").write_text("source\n")
    _git(host, "add", "-A")
    _git(host, "commit", "-q", "-m", "seed")

    cid = C.start()
    try:
        C.seed_from_ref(cid, host, "HEAD", SID)
        branch = C.run_branch(SID)
        # a run commit carrying an artefact under verification/ — what a real run produces
        C.exec_in(cid, ["sh", "-c", "mkdir -p verification && printf 'theorem t : True := trivial\\n' "
                                    "> verification/proof.lean"], workdir=C.REPO_IN)
        C.exec_in(cid, ["git", "add", "-A"], workdir=C.REPO_IN)
        C.exec_in(cid, ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "-m", "feat(prove): artefacts"], workdir=C.REPO_IN)
        tip = C.exec_in(cid, ["git", "rev-parse", branch], workdir=C.REPO_IN)[1].strip()
        yield cid, host, branch, tip
    finally:
        subprocess.run(["docker", "kill", cid], capture_output=True)  # --rm removes; harmless if gone
        atexit.unregister(C._stop_on_exit)


def test_completed_and_exported_is_stopped(seeded):
    """The only path that destroys the container: run completed AND export confirmed on the host."""
    cid, host, branch, tip = seeded

    outcome = C.finalize(cid, host, SID, completed=True, external=False)

    assert outcome == "stopped"
    assert C.is_running(cid) is False
    # and the artefacts really are on the host (not lost with the removed container)
    assert _git(host, "rev-parse", branch).stdout.strip() == tip
    assert "theorem t" in _git(host, "show", f"{branch}:verification/proof.lean").stdout


def test_completed_but_export_unconfirmed_is_kept_alive(seeded):
    """THE regression guard: a completed run whose export fails must NOT be destroyed. We sabotage the
    export by deleting the run-branch ref in the container, so `export_branch` cannot confirm the tip."""
    cid, host, branch, tip = seeded
    C.exec_in(cid, ["git", "update-ref", "-d", f"refs/heads/{branch}"], workdir=C.REPO_IN)

    outcome = C.finalize(cid, host, SID, completed=True, external=False)

    assert outcome == "kept-alive"
    assert C.is_running(cid) is True  # the container — and the run inside it — survives for recovery


def test_incomplete_run_is_kept_alive(seeded):
    """An incomplete run is always kept alive, even when the export itself would succeed."""
    cid, host, branch, tip = seeded

    outcome = C.finalize(cid, host, SID, completed=False, external=False)

    assert outcome == "kept-alive"
    assert C.is_running(cid) is True
    # export still ran (partial delivery), so the host holds the latest state for re-attach
    assert _git(host, "rev-parse", branch).stdout.strip() == tip
