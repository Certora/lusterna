"""Agent tools — exec commands and manage files inside the container."""
import logging
from pathlib import Path

from . import git_ops
from .container import REPO_IN, OUT_IN, exec_in
from .state import AgentDeps

log = logging.getLogger(__name__)


def _repo(rel: str) -> str:
    return f"{REPO_IN}/{rel}"


def _out(rel: str) -> str:
    return f"{OUT_IN}/{rel}"


def _assert_relative(path: str) -> None:
    """Reject absolute paths — tools only accept repo-relative or out-relative paths."""
    if path.startswith("/"):
        raise ValueError(f"Absolute paths are not accepted; use a repo-relative path (got: {path!r})")


def read_file(deps: AgentDeps, path: str) -> str:
    """Read a source file from the repo inside the container. Path must be relative.

    Returns the file content, or an error string prefixed with 'ERROR:' so the
    agent can detect and recover from missing files without crashing the pipeline.
    """
    _assert_relative(path)
    code, out, err = exec_in(deps.container_id, ["cat", _repo(path)])
    if code != 0:
        msg = f"ERROR: cannot read repo file '{path}': {err.strip()}"
        log.warning(msg)
        return msg
    log.info("read_file: %s (%d chars)", path, len(out))
    return out


def read_output_file(deps: AgentDeps, path: str) -> str:
    """Read a single generated file from /workspace/out. Path must be a file, not a directory.

    To discover available files use list_files first, then call read_output_file on each
    specific file path (e.g. 'specs/informal_spec.json', not 'specs/').
    Returns the file content, or an ERROR: string on failure.
    """
    _assert_relative(path)
    code, out, err = exec_in(deps.container_id, ["cat", _out(path)], workdir=OUT_IN)
    if code != 0:
        msg = f"ERROR: cannot read output file '{path}': {err.strip()} — if this is a directory, call list_files first"
        log.warning(msg)
        return msg
    log.info("read_output_file: %s (%d chars)", path, len(out))
    return out


_WRITE_PROTECTED = {
    "lean-toolchain", "lakefile.lean", "lake-manifest.json",
    "Cargo.toml", "Cargo.lock",
}

def write_file(deps: AgentDeps, path: str, content: str) -> str:
    """Write *content* to *path* inside /workspace/out."""
    _assert_relative(path)
    parts = Path(path).parts
    if ".lake" in parts or ".git" in parts:
        raise ValueError(f"Writing into .lake/ or .git/ is not allowed (got: {path!r})")
    if Path(path).name in _WRITE_PROTECTED:
        raise ValueError(
            f"{Path(path).name!r} is a protected infrastructure file and must not be modified. "
            "Only write to spec files (lean/*Spec.lean)."
        )
    full = _out(path)
    # ensure parent directory exists
    parent = str(Path(full).parent)
    exec_in(deps.container_id, ["mkdir", "-p", parent])
    # write via tee (no shell injection: content goes through stdin)
    import subprocess
    cmd = ["docker", "exec", "--interactive", "--workdir", OUT_IN,
           deps.container_id, "tee", full]
    r = subprocess.run(cmd, input=content, capture_output=True, text=True)
    if r.returncode != 0:
        raise IOError(f"write_file failed for {path}: {r.stderr.strip()}")
    log.info("write_file: %s (%d chars)", path, len(content))
    return f"Written {len(content)} chars to {path}"


def append_file(deps: AgentDeps, path: str, content: str) -> str:
    """Append *content* to *path* inside /workspace/out (creates the file if absent)."""
    _assert_relative(path)
    parts = Path(path).parts
    if ".lake" in parts or ".git" in parts:
        raise ValueError(f"Writing into .lake/ or .git/ is not allowed (got: {path!r})")
    if Path(path).name in _WRITE_PROTECTED:
        raise ValueError(f"{Path(path).name!r} is write-protected.")
    full = _out(path)
    parent = str(Path(full).parent)
    exec_in(deps.container_id, ["mkdir", "-p", parent])
    import subprocess
    cmd = ["docker", "exec", "--interactive", "--workdir", OUT_IN,
           deps.container_id, "tee", "--append", full]
    r = subprocess.run(cmd, input=content, capture_output=True, text=True)
    if r.returncode != 0:
        raise IOError(f"append_file failed for {path}: {r.stderr.strip()}")
    log.info("append_file: %s (+%d chars)", path, len(content))
    return f"Appended {len(content)} chars to {path}"


def list_files(deps: AgentDeps, extension: str = "rs") -> list[str]:
    """List files in the repo or output directory with the given extension.

    Lean files (extension='lean') are searched in /workspace/out since that is
    where Aeneas writes its output.  All other extensions are searched in the
    Rust repository at /workspace/repo.
    """
    search_root = OUT_IN if extension == "lean" else REPO_IN
    code, out, err = exec_in(
        deps.container_id,
        ["find", search_root, "-type", "f", "-name", f"*.{extension}"],
    )
    files = [
        line.removeprefix(search_root + "/")
        for line in out.splitlines()
        if line.strip()
    ]
    log.info("list_files(*.%s) in %s: %d results", extension, search_root, len(files))
    return files


def run_aeneas(deps: AgentDeps, entry_file: str) -> dict:
    """Translate *entry_file* (repo-relative) to Lean 4 via Charon + Aeneas.

    Returns a dict with:
      success      — True if Aeneas produced all files without errors
      partial      — True if some functions translated but others failed
      lean_files   — list of generated Lean files (relative to /workspace/out)
      lean_path    — primary Lean file for downstream tools
      charon_errors — Charon stderr (non-empty on failure)
      aeneas_errors — parsed Aeneas error lines (list of strings)
      commit       — git SHA if files were committed (empty string otherwise)

    When partial=True or success=False, the caller should inspect aeneas_errors /
    charon_errors, rewrite the Rust source via write_rust_file to remove
    untranslatable constructs, then call run_aeneas again.  Only fall back to
    LLM-based extraction if the translation produces zero output after retries.
    """
    from . import config

    lean_out_dir = _out("lean")
    exec_in(deps.container_id, ["mkdir", "-p", lean_out_dir])

    # ── Step 1: Charon ────────────────────────────────────────────────────────
    log.info("Running Charon in %s", REPO_IN)
    charon_code, charon_out, charon_err = exec_in(
        deps.container_id,
        [config.CHARON_BIN, "cargo", "--preset=aeneas"],
        workdir=REPO_IN,
        timeout=600,
    )
    if charon_code != 0:
        log.warning("Charon exited %d", charon_code)
        return {
            "success": False, "partial": False,
            "lean_files": [], "lean_path": "",
            "charon_errors": charon_err,
            "aeneas_errors": [],
            "commit": "",
        }

    # Charon names the output after the crate; find the .llbc file it produced.
    _, llbc_list, _ = exec_in(
        deps.container_id,
        ["find", REPO_IN, "-maxdepth", "2", "-name", "*.llbc"],
        workdir=REPO_IN,
    )
    llbc_files = [l.strip() for l in llbc_list.splitlines() if l.strip()]
    if not llbc_files:
        log.warning("No .llbc file found after Charon run")
        return {
            "success": False, "partial": False,
            "lean_files": [], "lean_path": "",
            "charon_errors": "no .llbc file produced",
            "aeneas_errors": [],
            "commit": "",
        }
    llbc_path = llbc_files[0]
    log.info("Charon produced: %s", llbc_path)

    # ── Step 2: Aeneas ────────────────────────────────────────────────────────
    log.info("Running Aeneas on %s → %s", llbc_path, lean_out_dir)
    aeneas_code, aeneas_out, aeneas_err = exec_in(
        deps.container_id,
        [config.AENEAS_BIN, "-backend", "lean", "-dest", lean_out_dir, llbc_path],
        workdir=REPO_IN,
        timeout=600,
    )
    if aeneas_code != 0:
        log.warning("Aeneas exited %d", aeneas_code)

    # Parse Aeneas [Error]/[Warn] lines for the caller.
    aeneas_errors = [
        line.strip() for line in aeneas_err.splitlines()
        if "[Error]" in line or "Could not translate" in line
    ]

    _, lean_list, _ = exec_in(
        deps.container_id,
        ["find", lean_out_dir, "-name", "*.lean"],
        workdir=REPO_IN,
    )
    lean_files = [
        l.strip().removeprefix(OUT_IN + "/")
        for l in lean_list.splitlines()
        if l.strip() and not Path(l.strip()).name.startswith("._")
    ]
    log.info("Aeneas wrote %d Lean file(s): %s", len(lean_files), lean_files)

    if not lean_files:
        return {
            "success": False, "partial": False,
            "lean_files": [], "lean_path": "",
            "charon_errors": "",
            "aeneas_errors": aeneas_errors or [aeneas_err[:400]],
            "commit": "",
        }

    # Write a lakefile.lean so `lake build` works.  The generated Lean files
    # use `import Aeneas`, so we declare a path dependency on the bundled
    # Aeneas Lean library.  The package/lib name is derived from the crate name.
    _write_lakefile(deps, lean_out_dir, llbc_path)

    partial = aeneas_code != 0
    sha = git_ops.commit(
        deps.container_id,
        f"feat(aeneas): translate {entry_file} → Lean" + (" (partial)" if partial else ""),
        glob="lean/",
    )
    log.info("Aeneas done (partial=%s) — commit %s", partial, sha[:8])
    return {
        "success": not partial,
        "partial": partial,
        "lean_files": lean_files,
        "lean_path": lean_files[0],
        "charon_errors": "",
        "aeneas_errors": aeneas_errors,
        "commit": sha,
    }


AENEAS_LEAN = "/opt/aeneas/backends/lean"
LEAN_TEMPLATE = "/opt/lean-template"


def _write_lakefile(deps: AgentDeps, lean_out_dir: str, llbc_path: str) -> None:
    """Generate a lakefile.lean and wire up the pre-resolved package manifest.

    The Docker image contains /opt/lean-template — a minimal lake project that
    already ran `lake update` against the bundled Aeneas runtime.  We copy its
    lake-manifest.json and symlink its .lake/packages so `lake build` works
    fully offline (--network none).
    """
    crate = Path(llbc_path).stem      # "fibonacci"
    lib_name = crate.capitalize()     # "Fibonacci"

    lakefile = (
        "import Lake\n"
        "open Lake DSL\n\n"
        f'require aeneas from "{AENEAS_LEAN}"\n\n'
        f'package «{crate}» where\n\n'
        f'lean_lib «{lib_name}» where\n'
    )
    import subprocess

    def _exec(cmd_args: list[str]) -> None:
        subprocess.run(["docker", "exec", deps.container_id] + cmd_args,
                       capture_output=True, text=True, check=True)

    def _exec_input(cmd_args: list[str], stdin: str) -> None:
        subprocess.run(["docker", "exec", "--interactive", deps.container_id] + cmd_args,
                       input=stdin, capture_output=True, text=True, check=True)

    _exec_input(["tee", f"{lean_out_dir}/lakefile.lean"], lakefile)

    # Use the pre-resolved manifest and toolchain file from the template so lake
    # knows the pinned package set without any network access.
    _exec(["cp", f"{LEAN_TEMPLATE}/lake-manifest.json",
           f"{lean_out_dir}/lake-manifest.json"])
    _exec(["cp", f"{LEAN_TEMPLATE}/lean-toolchain",
           f"{lean_out_dir}/lean-toolchain"])

    # Symlink the pre-downloaded package trees (Mathlib, Aeneas runtime, etc.)
    _exec(["mkdir", "-p", f"{lean_out_dir}/.lake"])
    _exec(["ln", "-sfn", f"{LEAN_TEMPLATE}/.lake/packages",
           f"{lean_out_dir}/.lake/packages"])

    log.info("Generated lakefile.lean + package symlinks for crate '%s'", crate)


def write_rust_file(deps: AgentDeps, path: str, content: str) -> str:
    """Overwrite a Rust source file inside the container repo.

    Use this to remove or simplify constructs that Aeneas cannot translate
    (e.g. strip a main() that uses vec!, replace unsupported stdlib calls with
    stubs) before retrying run_aeneas.  Path must be repo-relative.
    """
    _assert_relative(path)
    full = _repo(path)
    parent = str(Path(full).parent)
    exec_in(deps.container_id, ["mkdir", "-p", parent])
    import subprocess
    cmd = ["docker", "exec", "--interactive", "--workdir", REPO_IN,
           deps.container_id, "tee", full]
    r = subprocess.run(cmd, input=content, capture_output=True, text=True)
    if r.returncode != 0:
        raise IOError(f"write_rust_file failed for {path}: {r.stderr.strip()}")
    log.info("write_rust_file: %s (%d chars)", path, len(content))
    return f"Written {len(content)} chars to {path}"


def check_lean(deps: AgentDeps, lean_file: str) -> dict:
    """Run `lake build` inside the Lean project directory produced by Aeneas.

    *lean_file* is ignored for the build invocation — lake discovers targets
    from its lakefile. The file is only used to determine the project root
    (the directory that contains the lakefile produced by Aeneas).
    """
    from . import config

    lean_out_dir = _out("lean")
    # lake build with no arguments builds all targets declared in the lakefile.
    # Timeout is raised to 20 min; Lean+Mathlib compilation can be slow even
    # with a pre-populated cache.
    code, out, err = exec_in(
        deps.container_id,
        [config.LAKE_BIN, "build"],
        workdir=lean_out_dir,
        timeout=1200,
    )
    if code != 0:
        log.warning("lake build failed (exit %d): %s", code, err[:200])
    return {"success": code == 0, "stdout": out, "stderr": err}



def search_mathlib(query: str, max_results: int = 8) -> list[dict]:
    """Search Mathlib4 for lemmas by name fragment or type signature via Loogle.

    *query* can be a name fragment (e.g. "Nat.fib_mono"), a type signature
    fragment (e.g. "Monotone Nat.fib"), or a mix.  Returns up to *max_results*
    hits, each with 'name', 'type', 'module', and 'doc' fields.

    This call goes to loogle.lean-lang.org — it runs in the agent process,
    outside the air-gapped Docker container.
    """
    import urllib.request
    import urllib.parse
    import json as _json

    url = "https://loogle.lean-lang.org/json?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "lusterna/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
    except Exception as exc:
        log.warning("search_mathlib failed for %r: %s", query, exc)
        return [{"error": str(exc)}]

    hits = data.get("hits", [])[:max_results]
    log.info("search_mathlib(%r): %d/%s hits", query, len(hits), data.get("count", "?"))
    return [
        {
            "name":   h.get("name", ""),
            "type":   h.get("type", ""),
            "module": h.get("module", ""),
            "doc":    (h.get("doc") or "")[:300],
        }
        for h in hits
    ]


def git_log(deps: AgentDeps, n: int = 10) -> str:
    return git_ops.log_oneline(deps.container_id, n=n)


def git_commit(deps: AgentDeps, message: str) -> str:
    return git_ops.commit(deps.container_id, message)
