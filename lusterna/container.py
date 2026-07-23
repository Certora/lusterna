"""Docker container lifecycle management.

The container has its own isolated filesystem — no bind-mounts.
The source repo is pushed in with `docker cp` at session start;
artefacts are pulled out with `docker cp` at session end.

Fixed paths inside every container:
  /workspace/repo  — target Rust repo (pushed from host, read by agent)
  /workspace/out   — generated artefacts (git-tracked, pulled to host on exit)
"""
import atexit
import logging
import select
import subprocess
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

DEFAULT_IMAGE = "lusterna-toolchain:latest"

REPO_IN = "/workspace/repo"
OUT_IN  = "/workspace/out"


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


def init_out(container_id: str) -> None:
    """Create and git-init the output directory inside the container."""
    exec_in(container_id, ["mkdir", "-p", OUT_IN])
    exec_in(container_id, ["git", "init"], workdir=OUT_IN)
    exec_in(container_id,
            ["git", "commit", "--allow-empty", "-m", "chore: init lusterna session"],
            workdir=OUT_IN)
    log.info("Output repo initialised at %s inside %s", OUT_IN, container_id[:12])


def init_repo_git(container_id: str) -> None:
    """Git-init the pushed Rust source in REPO_IN and commit a pristine baseline.

    TRANSLATE may (as a last resort) apply behaviour-preserving refactors to the source;
    this baseline is what every such edit is diffed against for the accountability trail.
    `target/` (cargo build output) and `*.llbc` (Charon output) are excluded via
    .git/info/exclude so they never pollute the diffs.

    A pinned `rust-toolchain.toml` would force a stable channel Charon cannot drive (it needs
    its own bundled nightly for MIR extraction), so any such pin is neutralised BEFORE the
    baseline commit — it is build configuration, irrelevant to program behaviour, and baking it
    into pristine keeps it out of the accountability diff.
    """
    exec_in(container_id,
            ["find", REPO_IN, "-maxdepth", "4", "-name", "rust-toolchain*", "-delete"],
            workdir=REPO_IN)
    exec_in(container_id, ["git", "init"], workdir=REPO_IN)
    exec_in(container_id,
            ["sh", "-c", "printf 'target/\\n*.llbc\\n' >> .git/info/exclude"],
            workdir=REPO_IN)
    exec_in(container_id, ["git", "add", "-A"], workdir=REPO_IN)
    exec_in(container_id,
            ["git", "commit", "--allow-empty", "-m", "chore: pristine source (baseline for edit diffs)"],
            workdir=REPO_IN)
    log.info("Source repo git-initialised at %s inside %s", REPO_IN, container_id[:12])


def refold_baseline(container_id: str) -> None:
    """Fold any working-tree changes into the pristine baseline commit (amend).

    EXPLORE may apply BUILD-ENVIRONMENT prep (dropping a `cdylib` crate-type, neutralising a
    removed nightly feature, fixing a vendored `.cargo-checksum`) to make the target buildable.
    Like the `rust-toolchain` pin neutralised in init_repo_git, this is build configuration —
    irrelevant to program behaviour — so it belongs IN the baseline, not in the TRANSLATE
    accountability diff. Amending the single root commit keeps `repo_diff` (root..worktree)
    showing only TRANSLATE's genuine source edits. Safe because REPO_IN holds exactly one commit.
    """
    exec_in(container_id, ["git", "add", "-A"], workdir=REPO_IN)
    exec_in(container_id, ["git", "commit", "--amend", "--no-edit", "--allow-empty"],
            workdir=REPO_IN)
    log.info("Refolded EXPLORE build-env prep into the pristine baseline in %s", container_id[:12])


def push_artefacts(container_id: str, src: Path) -> None:
    """Push existing artefacts from *src* on the host back into OUT_IN.

    Used when resuming a session with a dead container: restores the partial
    output (including the git history) into a freshly started container.
    """
    exec_in(container_id, ["mkdir", "-p", OUT_IN])
    tar = subprocess.Popen(
        ["tar", "c", "-C", str(src.resolve()), "."],
        stdout=subprocess.PIPE,
    )
    subprocess.run(
        ["docker", "exec", "-i", container_id,
         "tar", "x", "--no-same-owner", "-C", OUT_IN],
        stdin=tar.stdout,
        check=True,
    )
    tar.wait()
    log.info("Artefacts pushed %s → %s inside %s", src, OUT_IN, container_id[:12])


def reset_out(container_id: str, git_head: str) -> None:
    """Hard-reset the output repo to *git_head* — the commit a checkpoint captured.

    After push_artefacts restores whatever tree was last on the host, this pins the
    output back to exactly the state of the checkpoint being resumed, discarding any
    later commits and untracked files. Without it, resume trusts the on-disk tree
    rather than the checkpoint, so a drifted work_path silently resumes stale Lean
    files. Raises if the commit is absent from the restored history (the artefacts do
    not match the checkpoint) rather than continuing from a mismatched state.
    """
    code, _, _ = exec_in(
        container_id, ["git", "cat-file", "-e", f"{git_head}^{{commit}}"], workdir=OUT_IN)
    if code != 0:
        raise RuntimeError(
            f"Checkpoint commit {git_head[:12]} is not present in the restored output "
            f"at {OUT_IN}. The artefacts on disk do not match this checkpoint — refusing "
            f"to resume from a mismatched state.")
    exec_in(container_id, ["git", "reset", "--hard", git_head], workdir=OUT_IN)
    exec_in(container_id, ["git", "clean", "-fdq"], workdir=OUT_IN)
    log.info("Output repo reset to checkpoint commit %s inside %s",
             git_head[:12], container_id[:12])


def pull_artefacts(container_id: str, dest: Path) -> None:
    """Copy the output directory from the container to *dest* on the host."""
    dest.mkdir(parents=True, exist_ok=True)
    _docker("cp", f"{container_id}:{OUT_IN}/.", str(dest.resolve()))
    log.info("Artefacts pulled %s → %s", OUT_IN, dest)


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
    """Leave the container running so a later resume can re-attach to it with FULL state — repo
    edits/shims, out/ artefacts, and the accountability baseline all intact — instead of restarting
    the interrupted stage from a pristine tree. Used when a run does not complete (budget hit,
    interrupt, crash). Cancels the atexit auto-stop; the `sleep infinity` entrypoint keeps it alive
    until the session is resumed (which re-attaches) or it is freed with `docker kill`."""
    atexit.unregister(_stop_on_exit)
    log.info("Container %s kept ALIVE for resume (full state preserved). Resume the session to "
             "re-attach; free it with: docker kill %s", container_id[:12], container_id)


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
    timeout: int = 3600,
) -> tuple[int, str, str]:
    """Run *cmd* in the container, invoking *on_line* for each stdout line AS IT ARRIVES.

    Unlike exec_in (which buffers), this streams stdout live so a long in-container process —
    chiefly a headless Claude Code session emitting stream-json events — can be surfaced to the
    host log in real time (the user watches the harness's stderr, outside the container). Returns
    (returncode, full_stdout, stderr). Enforces *timeout* as a wall-clock deadline even across
    output stalls (model-thinking gaps) via select; on breach the process is killed and 124 is
    returned. stderr is read at the end.
    """
    env_flags: list[str] = []
    for k in (passthrough_env or []):
        env_flags += ["--env", k]
    full_cmd = ["docker", "exec", "--workdir", workdir, *env_flags, container_id, *cmd]
    log.debug("exec-stream: %s", " ".join(full_cmd[:8]) + " …")
    proc = subprocess.Popen(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    out_chunks: list[str] = []
    deadline = time.monotonic() + timeout
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.kill(); timed_out = True; break
        ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 5.0))
        if ready:
            line = proc.stdout.readline()
            if line == "":            # EOF
                break
            out_chunks.append(line)
            try:
                on_line(line.rstrip("\n"))
            except Exception as exc:   # a logging/parse slip must never kill the stage
                log.debug("exec_stream on_line error: %s", exc)
        elif proc.poll() is not None:  # no data pending and process exited — drain remainder
            for line in proc.stdout:
                out_chunks.append(line)
                try:
                    on_line(line.rstrip("\n"))
                except Exception:
                    pass
            break
    err = proc.stderr.read() if proc.stderr else ""
    if timed_out:
        log.warning("exec_stream timed out after %ss: %s", timeout, " ".join(cmd[:3]))
        return 124, "".join(out_chunks), (err + f"\n[timed out after {timeout}s]").strip()
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
