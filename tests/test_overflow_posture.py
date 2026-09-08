"""Unit tests for the TRANSLATE-time overflow-posture VERIFY gate (`lean.check_overflow_posture`).

The gate verifies the agent compiled under the deployment's profile, against ground truth, in two
steps: (1) the extraction crate's overflow `[profile.release]` must MATCH the deployment's; (2)
`--release` must have taken effect (a wrapping deployment whose model still emits `checked.` ops = the
dev default leaked). The three readers (deployment profile, extraction profile, model op posture) are
mocked here so the decision is pinned without a container; the readers themselves (tomllib parse of
`[profile.release]`, `charon pretty-print` grep) run against the container in a campaign.
"""
import pytest

from lusterna import lean
from lusterna.schemas import AgentDeps

CHECKED = {"overflow-checks": True, "debug-assertions": False, "package": {}}
WRAPPING = {"overflow-checks": False, "debug-assertions": False, "package": {}}


def _deps():
    return AgentDeps(container_id="c", repo_path=".", session_id="s", design_doc="")


@pytest.fixture
def stub(monkeypatch):
    def install(deploy_prof, extraction_prof, model):
        monkeypatch.setattr(lean, "_deploy_overflow_profile", lambda deps: (deploy_prof, "dep/Cargo.toml"))
        monkeypatch.setattr(lean, "_extraction_overflow_profile",
                            lambda deps: (extraction_prof, "(in-place)" if extraction_prof is None else "ext/Cargo.toml"))
        monkeypatch.setattr(lean, "_model_has_checked_ops", lambda deps: (model, f"model={model}"))
    return install


# ── aligned: passes ──────────────────────────────────────────────────────────────────────────────

def test_matching_profile_checked_deploy_passes(stub):
    stub(CHECKED, CHECKED, True)
    assert lean.check_overflow_posture(_deps())["block"] is False


def test_matching_profile_wrapping_deploy_wrapping_model_passes(stub):
    stub(WRAPPING, WRAPPING, False)
    assert lean.check_overflow_posture(_deps())["block"] is False


def test_in_place_checked_deploy_passes(stub):
    """In-place (no extraction crate): the profile is the deployment's by construction, so a checked
    model under a checked deployment passes — even if the model is unreadable."""
    stub(CHECKED, None, None)
    assert lean.check_overflow_posture(_deps())["block"] is False


# ── profile mismatch: blocks and hands back the profile to copy ────────────────────────────────────

def test_profile_mismatch_blocks(stub):
    """The extraction copied the wrong profile (wrapping) for a checked deployment → block with the
    deployment profile to copy, regardless of the model posture."""
    stub(CHECKED, WRAPPING, True)
    rec = lean.check_overflow_posture(_deps())
    assert rec["block"] is True and "OVERFLOW-PROFILE MISMATCH" in rec["feedback"]


# ── --release didn't take effect: dev-default leak over a wrapping deployment ──────────────────────

def test_wrapping_deploy_checked_model_is_dev_leak_block(stub):
    """Profiles match (both wrapping) but the model still emits checked ops → the dev default leaked
    (profile ignored) → block."""
    stub(WRAPPING, WRAPPING, True)
    rec = lean.check_overflow_posture(_deps())
    assert rec["block"] is True and "dev-default profile leaked" in rec["feedback"]


def test_in_place_wrapping_deploy_checked_model_blocks(stub):
    stub(WRAPPING, None, True)
    assert lean.check_overflow_posture(_deps())["block"] is True


def test_wrapping_deploy_unreadable_model_blocks_fail_closed(stub):
    """No `.llbc` to rule out checked ops under a wrapping deployment → fail-closed block."""
    stub(WRAPPING, WRAPPING, None)
    assert lean.check_overflow_posture(_deps())["block"] is True
