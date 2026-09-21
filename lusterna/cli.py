"""CLI entry point — Unix-style: args in, structured output to stdout, logs to stderr."""
import asyncio
import json
import logging
import re
import signal
import sys
import uuid
from pathlib import Path

import click

from . import checkpoint, config
from .schemas import AgentDeps

log = logging.getLogger(__name__)


def _install_sigterm_handler() -> None:
    """Make SIGTERM unwind like Ctrl-C (SIGINT). Python's DEFAULT SIGTERM disposition terminates the
    process WITHOUT running `finally` blocks, so a `docker stop` / orchestrator / `kill` would skip
    run()'s finally — the branch export AND the container keep-alive — and lose the run's state.
    Re-raising KeyboardInterrupt routes SIGTERM through the SAME tested graceful path as Ctrl-C: the
    partial branch is exported to the host repo and the container is kept alive for `--session-id`
    resume. (SIGKILL and power-loss cannot be caught; the detached `sleep infinity` container is the
    net there — resume re-attaches via the checkpoint's container_id.)"""
    def _graceful(signum, frame):  # noqa: ARG001
        log.warning("SIGTERM received — exporting partial state and keeping the container alive for resume")
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, _graceful)
    except ValueError:
        pass  # not the main thread (programmatic use) — leave the default disposition

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
@click.option("--session-id", default=None,
              help="Session ID. If a checkpoint exists for it, RESUME that session; otherwise start a "
                   "NEW session under this name instead of the default random one. A name that "
                   "collides with an existing checkpoint resumes it, so pick a fresh name for a new run.")
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
    not flags), and only the delta is recomputed. Omit BRANCH to seed from the target's current HEAD.
    REPO must be a full (non-shallow) git repository with at least one commit — a bare directory or a
    shallow clone is refused up front. REPO's working tree and existing branches are left untouched.
    Review with:
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

    # Pre-flight the target BEFORE any container work: it must be a full, non-shallow git repo with a
    # resolvable seed, or this HARD-STOPS with an actionable message — never a lost campaign after the
    # fact. Doubles as resolving the commit a fresh run seeds from.
    seed_ref = container_mod.seed_ref_or_fail(repo_path, branch)

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
            # The Claude Code sessions are container-local — they died with the old container. Drop
            # their ids so any stage that RE-RUNS here starts a FRESH session (with its briefing)
            # rather than trying to resume a conversation that no longer exists (which fails instantly,
            # error_during_execution/turns=0). Completed stages are skipped by their progress markers,
            # so clearing their ids is harmless.
            progress["cc_sessions"] = {}
            # import_repo does not carry the (excluded) .lake build tree; re-provision it if the
            # translation already exists so a resumed lean stage builds against the cache, not a
            # cold full Mathlib rebuild.
            needs_lake = "aeneas" in progress
        else:
            # Fresh (or incremental) run — seed from the pre-flighted ref. A seed that already carries a
            # translation needs .lake re-provisioned (it is not bundled), so a reused/continued lean
            # stage builds against the cache rather than cold.
            container_mod.seed_from_ref(container_id, repo_path, seed_ref, sid)
            needs_lake = container_mod.exec_in(
                container_id, ["test", "-d", f"{container_mod.VERIF_IN}/lean"],
                workdir=container_mod.REPO_IN)[0] == 0

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

    # SIGTERM (docker stop / orchestrator / kill) must run the finally below — export + keep-alive —
    # not terminate silently. Routes it through the same graceful path as Ctrl-C.
    _install_sigterm_handler()
    try:
        summary = asyncio.run(pipeline.run_session(deps))
    finally:
        # Export the branch out (partial too) and decide the container's fate in one place: it is torn
        # down (--rm = permanent) ONLY when a completed run is also confirmed on the host; otherwise it
        # is kept alive so `--session-id` can re-attach and recover. Never masks the run's result.
        container_mod.finalize(container_id, repo_path, sid,
                               completed=deps.completed, external=_external)

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


