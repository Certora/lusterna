"""Regression test for the TRANSLATE gate's `lean/` shape check. Needs Docker.

A real run lost a whole TRANSLATE round here. The agent reused an existing translation by
symlinking `/workspace/out/lean` at `/workspace/repo/verification/lean` — which is the SAME path,
since `/workspace/out` is itself a symlink to `verification/` — so the directory pointed at itself.
`find` does not descend into a symlinked start point, the tree read as empty, and the gate told an
agent whose own `lake env lean` had just printed COMPILE OK that it had produced no Lean.

Rejecting is the only safe answer. Following the link instead (`find -L`) would let the gate pass,
and then `tools.commit`'s `git add -A` commits the symlink — delivering a branch with no Lean in it
at all, which trades a loud failure for a silent one. That is what this test pins.
"""
import shutil
import subprocess

import pytest

from lusterna import lean
from lusterna.schemas import AgentDeps

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs Docker")


@pytest.fixture
def container():
    cid = subprocess.run(
        ["docker", "run", "--rm", "-d", "--cap-drop", "all", "--security-opt",
         "no-new-privileges", "lusterna-toolchain:latest", "sleep", "infinity"],
        capture_output=True, text=True).stdout.strip()
    if not cid:
        pytest.skip("lusterna-toolchain:latest not available")
    # Mirror production's layout: /workspace/out is a SYMLINK to /workspace/repo/verification.
    subprocess.run(["docker", "exec", cid, "bash", "-lc",
                    "mkdir -p /workspace/repo/verification && "
                    "ln -sfn /workspace/repo/verification /workspace/out"], capture_output=True)
    try:
        yield cid
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def _sh(cid: str, cmd: str) -> None:
    subprocess.run(["docker", "exec", cid, "bash", "-lc", cmd], capture_output=True, check=True)


def _deps(cid: str) -> AgentDeps:
    return AgentDeps(container_id=cid, repo_path=".", session_id="t", design_doc="")


def test_real_directory_is_analysed(container):
    _sh(container, "mkdir -p /workspace/out/lean && echo 'def f := 1' > /workspace/out/lean/Crate.lean")
    info = lean.analyze_translation(_deps(container), do_commit=False)
    assert info["success"] and info["error"] == ""
    assert info["lean_files"] == ["lean/Crate.lean"]


def test_symlinked_lean_is_rejected_with_a_specific_reason(container):
    _sh(container, "rm -rf /workspace/out/lean && "
                   "ln -sfn /workspace/repo/verification/lean /workspace/out/lean")
    info = lean.analyze_translation(_deps(container), do_commit=False)
    assert not info["success"]
    # The message must name the ACTUAL problem — "no Lean was produced" sent the agent looking in
    # the wrong place entirely.
    assert "SYMLINK" in info["error"]
    assert "/workspace/repo/verification" in info["error"]
    assert info["lean_files"] == []


def test_missing_lean_dir_still_reports_the_ordinary_reason(container):
    """The symlink probe must not swallow the plain empty-tree case."""
    _sh(container, "rm -rf /workspace/out/lean")
    info = lean.analyze_translation(_deps(container), do_commit=False)
    assert not info["success"] and info["error"] == ""
