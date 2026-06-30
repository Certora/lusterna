"""Interactive permission grant at startup (stdin/stdout, Unix-friendly)."""
import sys
import logging

log = logging.getLogger(__name__)

REQUIRED_TOOLS = [
    ("git",         "read/write the work repository via git"),
    ("aeneas",      "run the Aeneas Rust→Lean translator"),
    ("lake",        "build and check Lean projects via Lake"),
    ("filesystem",  "read source files and write generated Lean files"),
    ("subprocess",  "spawn compiler / checker subprocesses"),
]


def request_permissions() -> None:
    """Print required tools and ask the user to confirm before proceeding."""
    print("lusterna requires permission to use the following tools:", file=sys.stderr)
    for name, reason in REQUIRED_TOOLS:
        print(f"  [{name}]  {reason}", file=sys.stderr)
    print("Grant all? [y/N] ", end="", flush=True, file=sys.stderr)
    try:
        answer = sys.stdin.readline().strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("y", "yes"):
        log.error("Permission denied by user — aborting")
        sys.exit(1)
    log.info("Tool permissions granted")
