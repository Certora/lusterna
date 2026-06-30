"""CLI entry point — Unix-style: args in, structured output to stdout, logs to stderr."""
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

import click

from . import checkpoint, config, logging_setup, permissions
from .state import AgentDeps

log = logging.getLogger(__name__)

_DOCKERFILE_DIR = Path(__file__).parent.parent


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Set log level to DEBUG")
@click.option("--no-confirm", is_flag=True, help="Skip permission confirmation (for CI use)")
@click.pass_context
def main(ctx: click.Context, verbose: bool, no_confirm: bool) -> None:
    """Lusterna — Rust → Lean formal verification agent."""
    logging_setup.setup(verbose=verbose)
    ctx.ensure_object(dict)
    ctx.obj["no_confirm"] = no_confirm


@main.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.argument("design_doc", type=click.Path(exists=True, dir_okay=False, resolve_path=True))
@click.option("--out", "out_dir", type=click.Path(resolve_path=True), default=None,
              help="Host directory to pull artefacts into when done "
                   "(default: <repo>/../<repo>-lusterna)")
@click.option("--session-id", default=None, help="Resume an existing session by ID")
@click.option("--container", default=config.CONTAINER_ID or None,
              help="Attach to a pre-running container instead of starting a new one")
@click.option("--image", default=config.CONTAINER_IMAGE, show_default=True,
              help="Image to start when --container is not given")
@click.pass_context
def run(
    ctx: click.Context,
    repo: str,
    design_doc: str,
    out_dir: str | None,
    session_id: str | None,
    container: str | None,
    image: str,
) -> None:
    """Run the verification pipeline on REPO using DESIGN_DOC."""
    from . import agent as agent_module
    from . import container as container_mod

    if not ctx.obj.get("no_confirm"):
        permissions.request_permissions()

    repo_path = Path(repo)
    work_path = Path(out_dir) if out_dir else repo_path.parent / (repo_path.name + "-lusterna")

    doc_text = Path(design_doc).read_text()

    sid = session_id or str(uuid.uuid4())
    saved = checkpoint.load(sid)
    if saved:
        log.info("Resuming session %s", sid)
        progress = saved.get("progress", {})
        container = container or saved.get("container_id")
    else:
        log.info("Starting new session %s", sid)
        progress = {}

    _owned = False
    if container:
        if not container_mod.is_running(container):
            log.error("Container %s is not running", container)
            sys.exit(1)
        container_id = container
        log.info("Attaching to existing container %s", container_id[:12])
    else:
        container_id = container_mod.start(image=image)
        container_mod.push_repo(container_id, repo_path)
        container_mod.init_out(container_id)
        _owned = True

    deps = AgentDeps(
        container_id=container_id,
        repo_path=repo_path,
        work_path=work_path,
        session_id=sid,
        design_doc=doc_text,
        progress=progress,
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
    logging_setup.setup()
    from . import container as container_mod
    container_mod.build_image(_DOCKERFILE_DIR, tag=tag)


@main.command("list-sessions")
def list_sessions() -> None:
    """List all saved session IDs."""
    for sid in checkpoint.list_sessions():
        print(sid)


@main.command("show-session")
@click.argument("session_id")
def show_session(session_id: str) -> None:
    """Print the checkpoint state for SESSION_ID as JSON."""
    state = checkpoint.load(session_id)
    if state is None:
        log.error("No checkpoint found for session %s", session_id)
        sys.exit(1)
    json.dump(state, sys.stdout, indent=2)
    sys.stdout.write("\n")


@main.group()
def rag() -> None:
    """Manage the local RAG knowledge base."""


@rag.command("add")
@click.argument("files", nargs=-1, type=click.Path(exists=True))
@click.option("--source", default="manual")
@click.option("--tags", default="")
def rag_add(files: tuple[str, ...], source: str, tags: str) -> None:
    """Ingest FILES into the RAG knowledge base."""
    from . import rag as rag_module
    logging_setup.setup()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    docs = [
        {"id": f, "text": Path(f).read_text(), "source": source, "tags": tag_list}
        for f in files
    ]
    rag_module.ingest(docs)
    log.info("Ingested %d document(s)", len(docs))
