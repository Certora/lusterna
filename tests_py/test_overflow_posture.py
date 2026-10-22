"""Regression tests for the overflow-posture gate (`lean.check_overflow_posture`).

THE BUG this pins: the gate used to locate the two manifests it compares BY PATH CONVENTION — the
extraction crate was assumed to live under `verification/translate/`, and the deployment profile was
the first `Cargo.toml` with a `[profile.release]` found under the repo. When an agent built the
extraction crate elsewhere in the repo (a real klend run put it at `extraction/klend_core/`), the
extraction finder missed it (→ false "in-place") and the deploy finder picked the extraction crate up
as the "deployment" — so the gate compared the compiled crate's profile against itself and passed
vacuously.

The fix identifies both from GROUND TRUTH: the compiled crate is the one whose crate name an emitted
`<crate>.llbc` names (wherever it lives); the deployment is the shipped workspace root (the shallowest
`[workspace]`); the governing profile of the compiled crate is its own workspace root's; and the gate
FAILS CLOSED when no `.llbc` identifies the compiled crate.
"""
from pathlib import PurePosixPath

from lusterna import lean
from lusterna.schemas import AgentDeps

REPO = "/workspace/repo"

# ── manifest fixtures ────────────────────────────────────────────────────────────
ROOT_WS = """\
[workspace]
members = ["programs/*"]
[profile.release]
overflow-checks = true
lto = "thin"
[profile.release.package.fixed]
debug-assertions = true
overflow-checks = true
"""
# a workspace MEMBER — no profile of its own (cargo honours the root's), no [workspace]
MEMBER = """\
[package]
name = "kamino-lending"
[lib]
name = "klend"
"""
# a STANDALONE extraction crate that copied the deployment profile verbatim
EXTRACTION_MATCH = """\
[package]
name = "klend_core"
[lib]
name = "klend_core"
[workspace]
[profile.release]
overflow-checks = true
lto = "thin"
[profile.release.package.fixed]
debug-assertions = true
overflow-checks = true
"""
# same crate, but the copied profile dropped overflow-checks + the per-package override
EXTRACTION_MISMATCH = """\
[package]
name = "klend_core"
[lib]
name = "klend_core"
[workspace]
[profile.release]
lto = "thin"
"""
# same crate, but no [profile.release] at all — a standalone workspace defaults to WRAPPING
EXTRACTION_NOPROFILE = """\
[package]
name = "klend_core"
[lib]
name = "klend_core"
[workspace]
"""

ROOT_M = f"{REPO}/Cargo.toml"
MEMBER_M = f"{REPO}/programs/klend/Cargo.toml"
EXTR_M = f"{REPO}/extraction/klend_core/Cargo.toml"


def _fake_exec(files: dict, llbc: list, checked: bool):
    """A stand-in for `container.exec_in` over an in-memory container FS: `files` maps a Cargo.toml
    path to its text, `llbc` lists emitted artefacts, `checked` is whether the `.llbc` shows checked
    operators. Understands exactly the shell the gate issues (`find … -name '*.llbc'`, `find … -name
    Cargo.toml` with `-maxdepth`/`-not -path`, `charon pretty-print … | grep`, and `cat`)."""
    def depth(p: str) -> int:
        return len(PurePosixPath(p).relative_to(REPO).parts)

    def run(_cid, argv, workdir=None, timeout=None):
        if argv[0] == "cat":
            p = argv[1]
            return (0, files[p], "") if p in files else (1, "", "No such file")
        if argv[0] == "sh" and len(argv) >= 3:
            cmd = argv[2]
            if "'*.llbc'" in cmd:
                return 0, "\n".join(llbc), ""
            if "charon pretty-print" in cmd:
                return (0, "checked.+", "") if checked else (0, "", "")
            if "-name Cargo.toml" in cmd:
                import re
                md = re.search(r"-maxdepth (\d+)", cmd)
                maxdepth = int(md.group(1)) if md else 99
                excl_verif = "*/verification/*" in cmd
                out = [p for p in files
                       if p.endswith("Cargo.toml") and "/target/" not in p
                       and depth(p) <= maxdepth
                       and not (excl_verif and "/verification/" in p)]
                return 0, "\n".join(sorted(out)), ""
        return 0, "", ""
    return run


def _run(monkeypatch, files, llbc, checked=True) -> dict:
    monkeypatch.setattr(lean, "exec_in", _fake_exec(files, llbc, checked))
    deps = AgentDeps(container_id="c", repo_path=".", session_id="s", design_doc="")
    return lean.check_overflow_posture(deps)


def test_extraction_offconvention_matches_the_real_root(monkeypatch):
    """THE REGRESSION. The extraction crate is at `extraction/klend_core/` (NOT under
    verification/translate), and it copied the deployment profile verbatim. The gate must compare the
    compiled crate against the TRUE workspace root — not against itself — and pass cleanly."""
    rec = _run(monkeypatch, {ROOT_M: ROOT_WS, MEMBER_M: MEMBER, EXTR_M: EXTRACTION_MATCH},
               [f"{REPO}/extraction/klend_core/klend_core.llbc"])
    assert rec["block"] is False
    assert rec["could_not_verify"] is False
    assert rec["deploy_source"] == ROOT_M            # the real root, never the extraction crate
    assert rec["compiled_source"] == EXTR_M
    assert rec["compiled_workspace"] == EXTR_M       # standalone extraction is its own workspace root


def test_extraction_profile_mismatch_blocks(monkeypatch):
    """A standalone extraction crate whose copied profile does NOT match the deployment blocks — the
    exact check that was vacuous before, now against the real root."""
    rec = _run(monkeypatch, {ROOT_M: ROOT_WS, MEMBER_M: MEMBER, EXTR_M: EXTRACTION_MISMATCH},
               [f"{REPO}/extraction/klend_core/klend_core.llbc"])
    assert rec["block"] is True
    assert rec["could_not_verify"] is False
    assert "MISMATCH" in rec["feedback"]
    assert rec["deploy_source"] == ROOT_M


def test_extraction_without_profile_blocks(monkeypatch):
    """A standalone extraction crate with NO `[profile.release]` compiles WRAPPING while the deployment
    checks — must block. (The walk-up must stop at the extraction crate's own `[workspace]`, not drift
    up to the real root and wrongly 'inherit' its checked profile.)"""
    rec = _run(monkeypatch, {ROOT_M: ROOT_WS, MEMBER_M: MEMBER, EXTR_M: EXTRACTION_NOPROFILE},
               [f"{REPO}/extraction/klend_core/klend_core.llbc"])
    assert rec["block"] is True
    assert "MISMATCH" in rec["feedback"]


def test_no_llbc_fails_closed(monkeypatch):
    """No `.llbc` ⇒ the gate cannot identify the compiled crate ⇒ it must BLOCK (could-not-verify),
    never assume a pass. The prior gate would silently treat this as in-place-and-fine."""
    rec = _run(monkeypatch, {ROOT_M: ROOT_WS, MEMBER_M: MEMBER, EXTR_M: EXTRACTION_MATCH}, [])
    assert rec["block"] is True
    assert rec["could_not_verify"] is True
    assert "COULD NOT VERIFY" in rec["feedback"]


def test_in_place_member_matches_by_construction(monkeypatch):
    """An in-place translation compiles a workspace MEMBER (no profile of its own); its governing
    workspace root IS the deployment root, so it matches by construction and does not block."""
    rec = _run(monkeypatch, {ROOT_M: ROOT_WS, MEMBER_M: MEMBER},
               [f"{REPO}/programs/klend/klend.llbc"])
    assert rec["block"] is False
    assert rec["compiled_source"] == MEMBER_M
    assert rec["compiled_workspace"] == ROOT_M       # walked up to the shared root
    assert rec["deploy_source"] == ROOT_M


def test_wrapping_deploy_with_checked_model_blocks(monkeypatch):
    """The preserved rung-2 check: a deployment that WRAPS (no overflow-checks) whose model still emits
    checked operators means the dev default leaked in — block."""
    root_wrap = '[workspace]\nmembers = ["programs/*"]\n[profile.release]\nlto = "thin"\n'
    rec = _run(monkeypatch, {ROOT_M: root_wrap, MEMBER_M: MEMBER},
               [f"{REPO}/programs/klend/klend.llbc"], checked=True)
    assert rec["block"] is True
    assert "WRAPS on overflow" in rec["feedback"]


def test_wrapping_deploy_with_wrapping_model_passes(monkeypatch):
    """A deployment that WRAPS whose model also wraps is faithful — no block (the legitimate
    wrapping regime, not a dev-default leak)."""
    root_wrap = '[workspace]\nmembers = ["programs/*"]\n[profile.release]\nlto = "thin"\n'
    rec = _run(monkeypatch, {ROOT_M: root_wrap, MEMBER_M: MEMBER},
               [f"{REPO}/programs/klend/klend.llbc"], checked=False)
    assert rec["block"] is False
