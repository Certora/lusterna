"""CLI entry point — Unix-style: args in, structured output to stdout, logs to stderr."""
import asyncio
import json
import logging
import re
import sys
import uuid
from pathlib import Path

import click

from . import checkpoint, config
from .schemas import AgentDeps

log = logging.getLogger(__name__)

_DOCKERFILE_DIR = Path(__file__).parent.parent


def _campaign_name(design_doc: str) -> str:
    """CamelCase campaign identifier from the instruction filename — names the per-campaign spec
    module and gets embedded in the session id / branch so runs are scannable, not opaque UUIDs.
    `SHARE_PRICE_DEPOSIT.md` → `SharePriceDeposit`; a digit-leading result is prefixed to stay a
    valid Lean module ident; empty → `Spec`."""
    stem = Path(design_doc).stem
    name = "".join(p.capitalize() for p in re.split(r"[^0-9A-Za-z]+", stem) if p)
    if not name:
        return "Spec"
    return name if name[0].isalpha() else "C" + name


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Set log level to DEBUG")
def main(verbose: bool) -> None:
    """Lusterna — Rust → Lean formal verification agent."""
    config.setup_logging(verbose=verbose)


@main.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.argument("design_doc", type=click.Path(exists=True, dir_okay=False, resolve_path=True))
@click.argument("branch", required=False, default=None)
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
    branch: str | None,
    session_id: str | None,
    ckpt_number: int | None,
    container: str | None,
    image: str,
) -> None:
    """Run the verification pipeline on REPO using DESIGN_DOC, optionally seeded from BRANCH.

    The run works one git repo inside the container and, on exit, fetches its results into REPO as
    branch `lusterna/<session>`. It SEEDS from BRANCH — its tree is the starting point and its
    `<branch>-base` anchor. Passing a prior run's `lusterna/<sid>` branch makes the run INCREMENTAL:
    the earlier translation/spec/proofs are reused (what to redo vs reuse is driven by DESIGN_DOC,
    not flags), and only the delta is recomputed. Omit BRANCH to seed from the target's current HEAD
    (or, for a non-git target, a synthesised pristine baseline). REPO's working tree and existing
    branches are left untouched. Review with:
    `git -C REPO diff lusterna/<session>-base lusterna/<session>`.

    Per-stage cost is capped by the agent's own --max-budget-usd (config.CC_STAGE_BUDGET_USD);
    there is no session-wide token budget knob."""
    from . import pipeline
    from . import container as container_mod

    repo_path = Path(repo)
    doc_text = Path(design_doc).read_text()

    campaign = _campaign_name(design_doc)
    # Session id / branch carry the campaign so `git branch` and checkpoint dirs are scannable
    # (Core-…, SharePriceDeposit-…) rather than opaque UUIDs.
    sid = session_id or f"{campaign}-{uuid.uuid4()}"
    # A container the USER passed (--container / env) is external — we never stop it. One that only
    # comes from the checkpoint is ours (a prior run kept it alive), and we manage its lifecycle.
    _user_container = container
    saved = checkpoint.load(sid, number=ckpt_number)
    if saved:
        log.info("Resuming session %s from checkpoint %s", sid, ckpt_number or "latest")
        progress = saved.get("progress", {})
        container = container or saved.get("container_id")
    else:
        log.info("Starting new session %s", sid)
        progress = {}

    _external = bool(_user_container) and container_mod.is_running(_user_container)
    resuming = bool(saved)

    needs_lake = False   # only the dead-container import path (below) drops the .lake build tree
    if container and container_mod.is_running(container):
        # Container kept alive from an interrupted run — re-attach with full in-container state.
        container_id = container
        log.info("Attaching to existing container %s", container_id[:12])
    else:
        if container:
            log.info("Container %s is not running — starting a fresh one", container[:12])
        container_id = container_mod.start(image=image)
        if resuming and container_mod.host_has_branch(repo_path, sid):
            # Dead-container resume: rebuild the whole repo (source edits + verification + branch)
            # from the target dir, pinned to the checkpoint commit — no stage is restarted pristine.
            container_mod.import_repo(container_id, repo_path, sid, saved.get("git_head", "HEAD"))
            # import_repo does not carry the (excluded) .lake build tree; re-provision it if the
            # translation already exists so a resumed lean stage builds against the cache, not a
            # cold full Mathlib rebuild.
            needs_lake = "aeneas" in progress
        elif (seed_ref := container_mod.resolve_seed_ref(repo_path, branch)) is not None:
            # New session seeded from a branch/HEAD — fresh or (if the seed carries prior artefacts)
            # incremental. Same path either way; the stages reconcile against whatever the seed holds.
            container_mod.seed_from_ref(container_id, repo_path, seed_ref, sid)
            # A seed that already has a translation needs .lake re-provisioned (it isn't carried), so
            # a reused/continued lean stage builds against the cache rather than cold.
            needs_lake = container_mod.exec_in(
                container_id, ["test", "-d", f"{container_mod.VERIF_IN}/lean"],
                workdir=container_mod.REPO_IN)[0] == 0
        else:
            # Non-git target (or unborn HEAD): synthesise a pristine baseline from the working tree.
            container_mod.push_repo(container_id, repo_path)
            container_mod.init_repo_git(container_id, sid)

    deps = AgentDeps(
        container_id=container_id,
        repo_path=repo_path,
        session_id=sid,
        design_doc=doc_text,
        campaign=campaign,
        progress=progress,
    )

    if needs_lake:
        from . import lean
        lean.setup_lake(deps)

    try:
        summary = asyncio.run(pipeline.run_session(deps))
    finally:
        # Always fetch the branch out (partial too), so the target repo holds the latest state even
        # if the kept-alive container is later killed. Best-effort: never masks the run's result.
        container_mod.export_branch(container_id, repo_path, sid)
        if _external:
            log.info("Leaving user-provided container %s as-is", container_id[:12])
        elif deps.completed:
            container_mod.stop(container_id)          # run finished — free it
        else:
            # Incomplete (budget/interrupt/crash): keep it alive so `--session-id %s` re-attaches
            # with full state instead of restarting the stage from a pristine tree.
            container_mod.keep_alive(container_id)

    output = {
        "session_id": sid,
        "repo": str(repo_path),
        "branch": container_mod.run_branch(sid),
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


