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

    No mounts — the container gets its own isolated filesystem.
    Registered with atexit so it is always stopped on clean exit.
    """
    extra_name = ["--name", name] if name else []
    result = _docker(
        "run", "--rm", "--detach",
        *extra_name,
        "--network", "none",
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
    """Copy the source repository into the container."""
    _docker("exec", container_id, "mkdir", "-p", REPO_IN)
    # docker cp <src>/. copies the directory contents, not the directory itself
    _docker("cp", f"{repo_path.resolve()}/.", f"{container_id}:{REPO_IN}")
    log.info("Repo pushed into container %s → %s", container_id[:12], REPO_IN)


def init_out(container_id: str) -> None:
    """Create and git-init the output directory inside the container."""
    exec_in(container_id, ["mkdir", "-p", OUT_IN])
    exec_in(container_id, ["git", "init"], workdir=OUT_IN)
    exec_in(container_id,
            ["git", "commit", "--allow-empty", "-m", "chore: init lusterna session"],
            workdir=OUT_IN)
    log.info("Output repo initialised at %s inside %s", OUT_IN, container_id[:12])


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
    r = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        log.debug("exec exit %d stderr: %s", r.returncode, r.stderr[:300])
    return r.returncode, r.stdout, r.stderr


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
