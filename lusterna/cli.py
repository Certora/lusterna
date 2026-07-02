"""CLI entry point — Unix-style: args in, structured output to stdout, logs to stderr."""
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

import click

from . import checkpoint, config, logging_setup
from .state import AgentDeps

log = logging.getLogger(__name__)

_DOCKERFILE_DIR = Path(__file__).parent.parent


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Set log level to DEBUG")
def main(verbose: bool) -> None:
    """Lusterna — Rust → Lean formal verification agent."""
    logging_setup.setup(verbose=verbose)


@main.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.argument("design_doc", type=click.Path(exists=True, dir_okay=False, resolve_path=True))
@click.option("--out", "out_dir", type=click.Path(resolve_path=True), default=None,
              help="Host directory to pull artefacts into when done "
                   "(default: <repo>/../<repo>-lusterna)")
@click.option("--session-id", default=None, help="Resume an existing session by ID")
@click.option("--checkpoint-number", "ckpt_number", default=None, type=int,
              help="Checkpoint number to resume from (default: latest)")
@click.option("--container", default=config.CONTAINER_ID or None,
              help="Attach to a pre-running container instead of starting a new one")
@click.option("--image", default=config.CONTAINER_IMAGE, show_default=True,
              help="Image to start when --container is not given")
def run(
    repo: str,
    design_doc: str,
    out_dir: str | None,
    session_id: str | None,
    ckpt_number: int | None,
    container: str | None,
    image: str,
) -> None:
    """Run the verification pipeline on REPO using DESIGN_DOC."""
    from . import agent as agent_module
    from . import container as container_mod

    repo_path = Path(repo)
    work_path = Path(out_dir) if out_dir else repo_path.parent / (repo_path.name + "-lusterna")

    doc_text = Path(design_doc).read_text()

    sid = session_id or str(uuid.uuid4())
    saved = checkpoint.load(sid, number=ckpt_number)
    if saved:
        log.info(
            "Resuming session %s from checkpoint %s",
            sid, ckpt_number or "latest",
        )
        progress = saved.get("progress", {})
        container = container or saved.get("container_id")
        message_history = checkpoint.load_messages(sid, number=ckpt_number)
    else:
        log.info("Starting new session %s", sid)
        progress = {}
        message_history = []

    _owned = False
    resuming = bool(saved)

    if container and container_mod.is_running(container):
        container_id = container
        log.info("Attaching to existing container %s", container_id[:12])
    else:
        if container:
            log.info("Container %s is not running — starting a fresh one", container[:12])
        container_id = container_mod.start(image=image)
        _owned = True
        container_mod.push_repo(container_id, repo_path)
        if resuming and work_path.exists():
            # Restore partial artefacts so the agent can continue where it left off.
            container_mod.push_artefacts(container_id, work_path)
            log.info("Restored artefacts from %s into container", work_path)
        else:
            container_mod.init_out(container_id)

    deps = AgentDeps(
        container_id=container_id,
        repo_path=repo_path,
        work_path=work_path,
        session_id=sid,
        design_doc=doc_text,
        progress=progress,
        message_history=message_history,
    )

    try:
        summary = asyncio.run(agent_module.run_session(deps))
    finally:
        container_mod.pull_artefacts(container_id, work_path)
        log.info("Artefacts written to %s", work_path)
        if _owned:
            container_mod.stop(container_id)

    output = {
        "session_id": sid,
        "out_dir": str(work_path),
        "container_id": container_id,
        "summary": summary,
        "progress_keys": list(deps.progress.keys()),
    }
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")


@main.command("build-image")
@click.option("--tag", default=config.CONTAINER_IMAGE, show_default=True)
def build_image(tag: str) -> None:
    """Build the lusterna Docker toolchain image."""
    from . import container as container_mod
    container_mod.build_image(_DOCKERFILE_DIR, tag=tag)


@main.command("list-sessions")
def list_sessions() -> None:
    """List all session IDs that have at least one checkpoint."""
    for sid in checkpoint.list_sessions():
        nums = checkpoint.list_checkpoints(sid)
        latest = nums[-1] if nums else {}
        print(f"{sid}  checkpoints={len(nums)}  latest={latest.get('progress_keys', [])}")


@main.command("list-checkpoints")
@click.argument("session_id")
def list_checkpoints_cmd(session_id: str) -> None:
    """List all checkpoints for SESSION_ID."""
    ckpts = checkpoint.list_checkpoints(session_id)
    if not ckpts:
        log.error("No checkpoints found for session %s", session_id)
        sys.exit(1)
    json.dump(ckpts, sys.stdout, indent=2)
    sys.stdout.write("\n")


@main.command("show-checkpoint")
@click.argument("session_id")
@click.option("--number", "-n", default=None, type=int,
              help="Checkpoint number (default: latest)")
def show_checkpoint(session_id: str, number: int | None) -> None:
    """Print the state of a checkpoint for SESSION_ID as JSON."""
    state = checkpoint.load(session_id, number=number)
    if state is None:
        log.error("No checkpoint found for session %s (number=%s)", session_id, number)
        sys.exit(1)
    json.dump(state, sys.stdout, indent=2)
    sys.stdout.write("\n")


