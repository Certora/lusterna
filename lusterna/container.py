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
import subprocess
from pathlib import Path

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
    """
    exec_in(container_id, ["mkdir", "-p", REPO_IN])
    tar = subprocess.Popen(
        ["tar", "c", "-C", str(repo_path.resolve()), "."],
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
            ["git", "commit", "--allow-empty", "-m", "chore: pristine source (pre-remediation baseline)"],
            workdir=REPO_IN)
    log.info("Source repo git-initialised at %s inside %s", REPO_IN, container_id[:12])


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


def is_running(container_id: str) -> bool:
    r = _docker("inspect", "--format={{.State.Running}}", container_id, check=False)
    return r.returncode == 0 and r.stdout.strip() == "true"


def exec_in(
    container_id: str,
    cmd: list[str],
    workdir: str = REPO_IN,
    timeout: int = 300,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run *cmd* inside the container and return (returncode, stdout, stderr)."""
    env_flags: list[str] = []
    for k, v in (env or {}).items():
        env_flags += ["--env", f"{k}={v}"]

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
