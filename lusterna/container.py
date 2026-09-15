"""Docker container lifecycle management.

The container has its own isolated filesystem — no bind-mounts. The source repo is pushed in at
session start; the run works ONE git repo on branch lusterna/<session>, and that branch is fetched
back into the target repo at session end.

Fixed paths inside every container:
  /workspace/repo               — the ONE git repo of the run (source + edits + artefacts),
                                  worked on branch lusterna/<session>
  /workspace/repo/verification  — generated artefacts (Lean, specs, report)
  /workspace/out                — symlink → /workspace/repo/verification, so the stage sessions
                                  and IO helpers address artefacts by a stable path
"""
import atexit
import logging
import subprocess
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

DEFAULT_IMAGE = "lusterna-toolchain:latest"

REPO_IN  = "/workspace/repo"
VERIF_IN = f"{REPO_IN}/verification"     # generated artefacts live here, inside the one repo
OUT_IN   = "/workspace/out"              # symlink → VERIF_IN (stable path for sessions + helpers)
PRISTINE_REF = "refs/lusterna/pristine"  # the pristine root commit — exactly the source we were handed


def run_branch(session_id: str) -> str:
    """The branch every stage commits onto — namespaced so it never collides with a branch the
    delivered repo may already have."""
    return f"lusterna/{session_id}"


def _link_out(container_id: str) -> None:
    """Point the stable /workspace/out path at verification/ inside the one repo.

    OUT_IN MUST END UP A SYMLINK, and it is verified here rather than assumed. The image DOES
    pre-create it (`RUN mkdir -p /workspace/repo /workspace/out`), so a bare `ln -s VERIF_IN OUT_IN`
    silently lands the link INSIDE the existing directory as `/workspace/out/verification` — leaving
    OUT_IN a real directory that is not the repo. Everything then still works, which is what makes
    it dangerous: `write_out`/`read_out`/`setup_lake`/`analyze_translation` all agree on OUT_IN, so a
    whole run translates, specs and judges normally while `git add -A` in REPO_IN sees none of it and
    the exported branch carries an EMPTY verification/ — a complete run delivering nothing.

    `ln -sfn` alone is not the fix: `-n` only stops it descending into a path that is itself a
    SYMLINK to a directory, and here OUT_IN is a real one. So clear it first (`rm -f` for a
    symlink/file, `rmdir` for the empty directory the image ships) and then assert the result."""
    exec_in(container_id, ["sh", "-c",
                           f"rm -f {OUT_IN} 2>/dev/null; rmdir {OUT_IN} 2>/dev/null; "
                           f"ln -sfn {VERIF_IN} {OUT_IN}"])
    # "clean must mean checked": a nested link leaves no error behind, so read the result back.
    code, out, _ = exec_in(container_id, ["readlink", OUT_IN])
    if code != 0 or out.strip() != VERIF_IN:
        raise RuntimeError(
            f"{OUT_IN} is not a symlink to {VERIF_IN} (readlink gave {out.strip()!r}). Every "
            f"artefact the harness writes would land outside the git repo and be lost on export.")


def _write_excludes(container_id: str) -> None:
    """Establish the repo's build-tree excludes in .git/info/exclude — the multi-GB `.lake` lake
    tree (re-provisioned on demand by setup_lake), `target/` (cargo output) and `*.llbc` (Charon
    output). Kept in info/exclude rather than a committed .gitignore so the delivered repo is never
    polluted with a lusterna file. Because info/exclude lives in .git (not in the tree, not in the
    bundle), it must be re-established on import_repo, not only at init — else a resumed stage's
    `git add -A` would start committing the build tree."""
    exec_in(container_id, ["sh", "-c", "printf 'target/\\n*.llbc\\n.lake/\\n' >> .git/info/exclude"],
            workdir=REPO_IN)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["docker", *args]
    log.debug("docker %s", " ".join(args))
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def start(image: str = DEFAULT_IMAGE, name: str | None = None) -> str:
    """Start a detached container and return its ID.

    No mounts — the container gets its own isolated filesystem. Network is left enabled so
    Charon can `cargo build` targets whose dependencies (and toolchain) are fetched on demand;
    the translation's soundness does not rest on network isolation (Aeneas opaques externals
    regardless). Registered with atexit so it is always stopped on clean exit.
    """
    extra_name = ["--name", name] if name else []
    result = _docker(
        "run", "--rm", "--detach",
        *extra_name,
        "--cap-drop", "all",
        "--security-opt", "no-new-privileges",
        image,
        "sleep", "infinity",
    )
    container_id = result.stdout.strip()
    log.info("Container started: %s (image=%s)", container_id[:12], image)
    atexit.register(_stop_on_exit, container_id)
    return container_id


def push_repo(container_id: str, repo_path: Path) -> None:
    """Copy the source repository into the container.

    We pipe a tar archive through `docker exec tar x` rather than using
    `docker cp` so that extracted files are owned by root (the container
    user).  `docker cp` preserves the host UID/GID, and with --cap-drop all
    the containerised root loses CAP_DAC_OVERRIDE and cannot write those files.

    `target/` (cargo build output, regenerated in-container by charon's own
    nightly) and the host `.git` are never pipeline inputs and can be huge for a
    vendored target, so they are excluded from the push.
    """
    exec_in(container_id, ["mkdir", "-p", REPO_IN])
    tar = subprocess.Popen(
        ["tar", "c", "--exclude=./target", "--exclude=./.git",
         "-C", str(repo_path.resolve()), "."],
        stdout=subprocess.PIPE,
    )
    subprocess.run(
        ["docker", "exec", "-i", container_id,
         "tar", "x", "--no-same-owner", "-C", REPO_IN],
        stdin=tar.stdout,
        check=True,
    )
    tar.wait()
    log.info("Repo pushed into container %s → %s", container_id[:12], REPO_IN)


def init_repo_git(container_id: str, session_id: str) -> None:
    """Git-init the pushed source as the ONE repo of the run and branch off a pristine baseline.

    Everything the run produces — the Aeneas translation, the specs, the report — lands in a
    `verification/` subtree of THIS repo, and every stage commits onto branch lusterna/<session>.
    So a single `git diff lusterna/<session>-base lusterna/<session>` in the delivered repo shows
    the whole change: the generated Lean plus any behaviour-preserving source edit TRANSLATE made.

    `/workspace/out` is kept as a symlink into `verification/`, so the stage sessions and the harness
    IO helpers address artefacts by the same stable path while they physically live in the one repo.
    `target/` (cargo output), `*.llbc` (Charon output) and `.lake/` (the multi-GB lake build tree,
    re-provisioned on demand by setup_lake) are excluded so neither commits nor the diff carry them.

    A pinned `rust-toolchain.toml` would force a stable channel Charon cannot drive (it needs its own
    bundled nightly for MIR extraction), so any such pin is neutralised BEFORE the baseline commit —
    build configuration, irrelevant to program behaviour.
    """
    exec_in(container_id,
            ["find", REPO_IN, "-maxdepth", "4", "-name", "rust-toolchain*", "-delete"],
            workdir=REPO_IN)
    exec_in(container_id, ["git", "init", "-q"], workdir=REPO_IN)
    _write_excludes(container_id)
    exec_in(container_id, ["git", "add", "-A"], workdir=REPO_IN)
    exec_in(container_id,
            ["git", "commit", "-q", "--allow-empty", "-m", "chore: pristine source (baseline)"],
            workdir=REPO_IN)
    exec_in(container_id, ["git", "checkout", "-q", "-b", run_branch(session_id)], workdir=REPO_IN)
    # Pin the pristine root (the source exactly as handed to us) so export can offer it as the
    # `<branch>-base` review anchor. It never moves; the whole run is `git diff <branch>-base <branch>`.
    exec_in(container_id, ["git", "update-ref", PRISTINE_REF, "HEAD"], workdir=REPO_IN)
    exec_in(container_id, ["mkdir", "-p", VERIF_IN], workdir=REPO_IN)
    _link_out(container_id)
    log.info("Source repo git-initialised at %s (branch %s) inside %s",
             REPO_IN, run_branch(session_id), container_id[:12])


def resolve_seed_ref(target: Path, branch: str | None) -> str | None:
    """Decide what commit a new run seeds from (and anchors `<branch>-base` at).

    - *branch* given → that ref (raises if it does not resolve in the target repo);
    - omitted, target is a git repo with a resolvable HEAD → "HEAD" (seed from the current checkout);
    - omitted, non-git dir or an unborn HEAD (no commits yet) → None (caller synthesises the pristine
      root from the loose working tree via push_repo + init_repo_git).
    """
    is_git = subprocess.run(["git", "-C", str(target), "rev-parse", "--git-dir"],
                            capture_output=True, text=True).returncode == 0
    if branch:
        if not is_git:
            raise RuntimeError(f"{target} is not a git repository, so branch '{branch}' cannot be used")
        r = subprocess.run(["git", "-C", str(target), "rev-parse", "--verify", "-q", f"{branch}^{{commit}}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"branch/ref '{branch}' does not resolve to a commit in {target}")
        return branch
    if is_git and subprocess.run(["git", "-C", str(target), "rev-parse", "--verify", "-q", "HEAD^{commit}"],
                                 capture_output=True, text=True).returncode == 0:
        return "HEAD"
    return None


def seed_from_ref(container_id: str, target: Path, seed_ref: str, session_id: str) -> None:
    """Start a NEW run seeded from *seed_ref* of the host repo at *target* (a prior run's branch for
    an incremental campaign, or HEAD/a source branch for a fresh one).

    The seed commit becomes both the starting tree of `lusterna/<session>` and the `<branch>-base`
    anchor, so `git diff <branch>-base <branch>` is exactly what THIS run adds. We bundle the seed
    ref on the host and reconstruct it in a fresh in-container repo by SHA (avoids ref-name games),
    then branch off it. If the seed already carries a `verification/` tree (incremental), the stages
    reconcile against it; otherwise it's a plain source seed. `.lake`/`target` stay excluded, and a
    pinned rust-toolchain is neutralised in the working tree (recorded by the first stage commit)."""
    branch = run_branch(session_id)
    seed_sha = subprocess.run(["git", "-C", str(target), "rev-parse", f"{seed_ref}^{{commit}}"],
                              capture_output=True, text=True, check=True).stdout.strip()
    bundle_host = target / ".lusterna-seed.bundle"
    subprocess.run(["git", "-C", str(target), "bundle", "create", str(bundle_host.resolve()), seed_ref],
                   capture_output=True, text=True, check=True)
    bundle_in = "/workspace/seed.bundle"
    try:
        exec_in(container_id, ["mkdir", "-p", REPO_IN])
        _docker("cp", str(bundle_host), f"{container_id}:{bundle_in}")
        exec_in(container_id, ["git", "init", "-q"], workdir=REPO_IN)
        _write_excludes(container_id)
        exec_in(container_id, ["git", "fetch", "-q", bundle_in, seed_ref], workdir=REPO_IN)  # brings the objects
    finally:
        bundle_host.unlink(missing_ok=True)
    exec_in(container_id, ["git", "branch", branch, seed_sha], workdir=REPO_IN)
    exec_in(container_id, ["git", "update-ref", PRISTINE_REF, seed_sha], workdir=REPO_IN)
    exec_in(container_id, ["git", "checkout", "-q", branch], workdir=REPO_IN)
    exec_in(container_id,
            ["find", REPO_IN, "-maxdepth", "4", "-name", "rust-toolchain*", "-delete"], workdir=REPO_IN)
    exec_in(container_id, ["mkdir", "-p", VERIF_IN], workdir=REPO_IN)
    _link_out(container_id)
    log.info("Seeded %s from %s (%s @ %s) inside %s",
             run_branch(session_id), target, seed_ref, seed_sha[:12], container_id[:12])


def export_branch(container_id: str, dest: Path, session_id: str) -> bool:
    """Fetch the run branch out of the container into the target repo at *dest*.

    Returns True ONLY when the run's tip is CONFIRMED present on the host — the signal the caller uses
    to decide the container may be torn down. Any failure returns False (never raised), and on False
    the run still lives solely inside the container: the caller MUST keep it alive.

    The container holds the one repo (source + verification/, on branch lusterna/<session>). The
    pristine root (PRISTINE_REF / `<branch>-base`) is, by construction, a commit *dest* ALREADY holds
    — the run was seeded FROM *dest*. So we ship only the RUN-RELATIVE delta as a THIN bundle
    (`^PRISTINE_REF <branch>`): this never traverses at or below the pristine root, so a SHALLOW seed
    (whose ancestry is absent by design) exports fine, where the old full-history bundle failed to
    traverse the missing parent below the shallow boundary. `<branch>-base` is (re)pointed on the host
    at the pristine commit it already carries. The fetch touches nothing else: any OTHER branch the
    delivered repo has is left as-is.
    Review the whole run with:  git -C <dest> diff lusterna/<session>-base lusterna/<session>

    Robust to the run branch already being CHECKED OUT in *dest* (e.g. re-exporting a resumed session
    whose branch you inspected): a plain fetch into the current branch is refused, so we pass git's
    sanctioned `--update-head-ok` and then re-sync the working tree to the new tip ONLY if it is clean
    — a dirty worktree is never clobbered. The bundle is KEPT on failure (it is the only host copy of
    the run's delta) with a recovery command.
    """
    branch = run_branch(session_id)
    base = f"{branch}-base"
    bundle_in = "/workspace/lusterna.bundle"

    pcode, pout, _ = exec_in(container_id, ["git", "rev-parse", "--verify", "-q", PRISTINE_REF],
                             workdir=REPO_IN)
    tcode, tout, _ = exec_in(container_id, ["git", "rev-parse", "--verify", "-q", branch],
                             workdir=REPO_IN)
    if pcode != 0 or tcode != 0:
        log.warning("Skipping export — %s or %s is absent inside the container.", PRISTINE_REF, branch)
        return False
    pristine_sha, tip_sha = pout.strip(), tout.strip()

    dest.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        subprocess.run(["git", "init", "-q", str(dest)], check=True)

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(dest), *args], capture_output=True, text=True)

    # (Re)point `<branch>-base` at the pristine commit the host already holds (the run was seeded FROM
    # here). Independent of the delta bundle, and always the correct anchor for the review diff. If the
    # host somehow lacks it, warn and press on — the run branch itself carries the artefacts.
    if _git("cat-file", "-e", f"{pristine_sha}^{{commit}}").returncode == 0:
        if _git("branch", "-f", base, pristine_sha).returncode != 0:
            log.warning("Could not point %s at %s on host (checked out?) — review the diff manually.",
                        base, pristine_sha[:12])
    else:
        log.warning("Host is missing the pristine root %s — `%s` not set; the run branch is still "
                    "delivered.", pristine_sha[:12], base)

    if tip_sha == pristine_sha:
        # The run added no commits over the seed: nothing to bundle, and the tip is already on the host
        # (it IS the pristine). Base is anchored above; nothing can be lost.
        log.info("Export: run branch has no commits over the pristine root — base anchored at %s.",
                 tip_sha[:12])
        return True

    # THIN bundle of ONLY the run-relative commits; the host resolves the pristine prerequisite from
    # its own objects, so we never walk below the (possibly shallow) pristine boundary.
    code, _, err = exec_in(container_id,
                           ["git", "bundle", "create", bundle_in, f"^{PRISTINE_REF}", branch],
                           workdir=REPO_IN)
    if code != 0:
        log.warning("Skipping export — could not bundle %s: %s", branch, err.strip()[:200])
        return False
    bundle_host = dest / ".lusterna-run.bundle"
    _docker("cp", f"{container_id}:{bundle_in}", str(bundle_host))

    fetch = _git("fetch", "-q", "--update-head-ok", str(bundle_host.resolve()),
                 f"+{branch}:refs/heads/{branch}")
    if fetch.returncode != 0:
        # NEVER delete the bundle on failure — it is the only host copy of the run's delta.
        log.warning("Export fetch failed (%s) — the run is PRESERVED at %s; recover with:\n"
                    "  git -C %s fetch --update-head-ok %s '+%s:refs/heads/%s'",
                    (fetch.stderr or "").strip()[:200], bundle_host, dest, bundle_host, branch, branch)
        return False
    # CONFIRM the run's tip actually landed on the host before the caller is told it is safe to tear the
    # container down. A ref that fetched but points elsewhere (or a missing prerequisite) is a failure.
    landed = _git("rev-parse", "--verify", "-q", f"refs/heads/{branch}").stdout.strip()
    if landed != tip_sha:
        log.warning("Export could not be confirmed on host (expected %s, got %s) — bundle PRESERVED "
                    "at %s.", tip_sha[:12], (landed or "∅")[:12], bundle_host)
        return False

    # If the run branch is the one checked out here, --update-head-ok moved its ref but not the
    # worktree; re-sync a CLEAN worktree to match, and leave a dirty one untouched (never lose edits).
    if _git("symbolic-ref", "-q", "--short", "HEAD").stdout.strip() == branch:
        if _git("status", "--porcelain").stdout.strip():
            log.warning("Export updated %s but its checked-out worktree in %s has uncommitted changes "
                        "— left as-is; `git -C %s reset --hard %s` to sync when ready.",
                        branch, dest, dest, branch)
        else:
            _git("reset", "--hard", "-q", branch)
    bundle_host.unlink(missing_ok=True)
    log.info("Run branch exported → %s (%s; base %s)", dest, branch, base)
    return True


def host_has_branch(dest: Path, session_id: str) -> bool:
    """True if *dest* is a git repo already carrying this session's run branch (a prior run exported
    it) — the signal that a dead-container resume can restore full state from disk."""
    if not (dest / ".git").exists():
        return False
    r = subprocess.run(["git", "-C", str(dest), "rev-parse", "--verify", "-q",
                        f"refs/heads/{run_branch(session_id)}"], capture_output=True, text=True)
    return r.returncode == 0


def import_repo(container_id: str, src: Path, session_id: str, git_head: str) -> None:
    """Restore the run's repo from the target repo at *src* into a fresh container (dead-container
    resume). Pushes the host repo WITH its .git — so the branch, its history, and the source edits
    all come along — checks out the run branch, hard-resets to the checkpoint commit, and recreates
    the /workspace/out symlink. `.lake`/`target` are not carried (setup_lake re-provisions .lake).
    Raises if the checkpoint commit is absent (the target dir does not match the checkpoint) rather
    than continuing from a mismatched state."""
    branch = run_branch(session_id)
    exec_in(container_id, ["mkdir", "-p", REPO_IN])
    tar = subprocess.Popen(
        ["tar", "c", "-C", str(src.resolve()), "--exclude=./target", "--exclude=*/.lake", "."],
        stdout=subprocess.PIPE)
    subprocess.run(["docker", "exec", "-i", container_id, "tar", "x", "--no-same-owner",
                    "-C", REPO_IN], stdin=tar.stdout, check=True)
    tar.wait()
    code, _, _ = exec_in(container_id, ["git", "cat-file", "-e", f"{git_head}^{{commit}}"],
                         workdir=REPO_IN)
    if code != 0:
        raise RuntimeError(
            f"Checkpoint commit {git_head[:12]} is absent from the repo restored from {src}. "
            f"The target dir does not match this checkpoint — refusing to resume from a mismatch.")
    exec_in(container_id, ["git", "checkout", "-q", "-f", branch], workdir=REPO_IN)
    exec_in(container_id, ["git", "reset", "--hard", git_head], workdir=REPO_IN)
    exec_in(container_id, ["git", "clean", "-fdq"], workdir=REPO_IN)
    # Re-establish the pristine baseline ref from the exported `-base` branch (which IS the pristine
    # root). PRISTINE_REF is container-only in a normal run — a dead-container resume rebuilds from the
    # host, which carries `<branch>-base` as a branch but not the custom ref — so without this the next
    # `export_branch` (`git bundle create … refs/lusterna/pristine`) fails and the resumed run cannot
    # be delivered. `-base` is present whenever a prior run exported (the precondition for this path).
    base = f"{branch}-base"
    if exec_in(container_id, ["git", "rev-parse", "--verify", "-q", f"refs/heads/{base}"],
               workdir=REPO_IN)[0] == 0:
        exec_in(container_id, ["git", "update-ref", PRISTINE_REF, f"refs/heads/{base}"], workdir=REPO_IN)
    # The tarred-in host .git carries an empty info/exclude (it was git-init'd on the host at export),
    # so re-establish the build-tree excludes before any resumed stage runs `git add -A`.
    _write_excludes(container_id)
    _link_out(container_id)
    log.info("Repo restored from %s into %s (branch %s @ %s)",
             src, container_id[:12], branch, git_head[:12])


def _stop_on_exit(container_id: str) -> None:
    try:
        _docker("stop", container_id, check=False)
        log.info("Container stopped: %s", container_id[:12])
    except Exception as exc:
        log.warning("Could not stop container %s: %s", container_id[:12], exc)


def stop(container_id: str) -> None:
    _docker("stop", container_id, check=False)
    log.info("Container stopped: %s", container_id[:12])


def keep_alive(container_id: str) -> None:
    """Leave the container running so a later resume can re-attach to it with FULL state — source
    edits, the verification/ tree, and the run branch all intact — instead of restarting the
    interrupted stage from a pristine tree. Used when a run does not complete (budget hit,
    interrupt, crash). Cancels the atexit auto-stop; the `sleep infinity` entrypoint keeps it alive
    until the session is resumed (which re-attaches) or it is freed with `docker kill`."""
    atexit.unregister(_stop_on_exit)
    log.info("Container %s kept ALIVE for resume (full state preserved). Resume the session to "
             "re-attach; free it with: docker kill %s", container_id[:12], container_id)


def finalize(container_id: str, dest: Path, session_id: str, *, completed: bool, external: bool) -> str:
    """Export the run, then decide the container's fate — the ONE place that guarantees a container is
    torn down only when its results are safely on the host.

    A run container starts with `--rm`, so `stop()` deletes it (and the only in-container copy of the
    proofs and report) forever. We therefore stop it ONLY when the run both COMPLETED and its export
    was CONFIRMED on the host. In every other case — incomplete run, or a completed run whose export
    could not be confirmed — the container is kept alive so `--session-id` can re-attach and recover,
    rather than silently discarding a whole (token-costly) run. Returns the outcome:
    'left-external' | 'stopped' | 'kept-alive'.
    """
    exported = export_branch(container_id, dest, session_id)
    if external:
        log.info("Leaving user-provided container %s as-is", container_id[:12])
        return "left-external"
    if completed and exported:
        stop(container_id)  # run finished AND delivered — free it (--rm removes)
        return "stopped"
    if completed and not exported:
        log.warning("Run COMPLETED but its export could not be confirmed on the host — keeping the "
                    "container ALIVE so the results are not lost. Re-run with `--session-id %s` to "
                    "re-attach and recover; see the export warning above.", session_id)
    keep_alive(container_id)
    return "kept-alive"


def is_running(container_id: str) -> bool:
    r = _docker("inspect", "--format={{.State.Running}}", container_id, check=False)
    return r.returncode == 0 and r.stdout.strip() == "true"


def write_file(container_id: str, abs_path: str, content: str) -> None:
    """Write *content* to an absolute path inside the container, mkdir-ing the parent.

    Used for harness scaffolding that lives outside /workspace/out (e.g. a stage's task
    briefing at /workspace/.lusterna/…), where the tools.write_out OUT_IN helpers don't apply.
    """
    exec_in(container_id, ["mkdir", "-p", str(Path(abs_path).parent)])
    subprocess.run(["docker", "exec", "--interactive", container_id, "tee", abs_path],
                   input=content, capture_output=True, text=True, check=True)


def exec_in(
    container_id: str,
    cmd: list[str],
    workdir: str = REPO_IN,
    timeout: int = 300,
    env: dict[str, str] | None = None,
    passthrough_env: list[str] | None = None,
) -> tuple[int, str, str]:
    """Run *cmd* inside the container and return (returncode, stdout, stderr).

    *env* sets literal KEY=VALUE pairs (value visible in the exec argv). *passthrough_env* names
    variables whose VALUE is taken from the host process env and forwarded WITHOUT appearing in the
    argv (`docker exec --env KEY`) — use it for secrets like ANTHROPIC_API_KEY.
    """
    env_flags: list[str] = []
    for k, v in (env or {}).items():
        env_flags += ["--env", f"{k}={v}"]
    for k in (passthrough_env or []):
        env_flags += ["--env", k]

    full_cmd = ["docker", "exec", "--workdir", workdir, *env_flags, container_id, *cmd]
    log.debug("exec: %s", " ".join(full_cmd))
    try:
        r = subprocess.run(full_cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        # The host-side `docker exec` client is killed, but the in-container process is
        # NOT — so this is only a backstop. Long-running commands (lake build) should wrap
        # themselves with a container-side `timeout` that kills the process tree. Degrade to
        # a normal non-zero result (124) so callers treat it as a failure, never a crash.
        log.warning("exec timed out after %ss: %s", timeout, " ".join(cmd)[:120])
        out = e.stdout.decode("utf-8", errors="replace") if e.stdout else ""
        err = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        return 124, out, (err + f"\n[command timed out after {timeout}s]").strip()
    stdout = r.stdout.decode("utf-8", errors="replace")
    stderr = r.stderr.decode("utf-8", errors="replace")
    if r.returncode != 0:
        log.debug("exec exit %d stderr: %s", r.returncode, stderr[:300])
    return r.returncode, stdout, stderr


def exec_stream(
    container_id: str,
    cmd: list[str],
    workdir: str,
    on_line: Callable[[str], None],
    passthrough_env: list[str] | None = None,
) -> tuple[int, str, str]:
    """Run *cmd* in the container, invoking *on_line* for each stdout line AS IT ARRIVES, and stream
    to completion. Returns (returncode, full_stdout, stderr).

    Unlike exec_in (which buffers), this streams stdout live so a long in-container process — chiefly
    a headless Claude Code session emitting stream-json events — is surfaced to the host log in real
    time (the user watches the harness's stderr, outside the container).

    There is NO wall-clock deadline: a campaign is bounded by its own `--max-budget-usd`, and a human
    watching the log notices a genuine hang. A total-time cap cannot tell a deep-thinking gap or a
    long build from a hang, so it only ever false-kills productive work (and, killing mid-turn, drops
    the session's `result` event and its cost). We do not abruptly kill a running session.
    """
    env_flags: list[str] = []
    for k in (passthrough_env or []):
        env_flags += ["--env", k]
    full_cmd = ["docker", "exec", "--workdir", workdir, *env_flags, container_id, *cmd]
    log.debug("exec-stream: %s", " ".join(full_cmd[:8]) + " …")
    proc = subprocess.Popen(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    out_chunks: list[str] = []
    for line in proc.stdout:                 # blocks until the next line or EOF (process exit)
        out_chunks.append(line)
        try:
            on_line(line.rstrip("\n"))
        except Exception as exc:             # a logging/parse slip must never kill the stage
            log.debug("exec_stream on_line error: %s", exc)
    err = proc.stderr.read() if proc.stderr else ""
    proc.wait()
    return proc.returncode, "".join(out_chunks), err


def build_image(dockerfile_dir: Path, tag: str = DEFAULT_IMAGE) -> None:
    """Build the toolchain image from the project Dockerfile."""
    log.info("Building Docker image %s from %s …", tag, dockerfile_dir)
    r = subprocess.run(
        ["docker", "build", "-t", tag, str(dockerfile_dir)],
        capture_output=False,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"docker build failed (exit {r.returncode})")
    log.info("Image built: %s", tag)
