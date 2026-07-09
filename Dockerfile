FROM debian:bookworm-slim

# ── System packages ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl git ca-certificates \
        pkg-config libssl-dev libgmp-dev libffi-dev \
        opam bubblewrap m4 \
    && rm -rf /var/lib/apt/lists/*

# ── Rust / Cargo ───────────────────────────────────────────────────────────────
# Needed both at build time (to compile Charon) and at runtime
# (charon cargo invokes cargo inside the user's crate).
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --no-modify-path --default-toolchain stable

# ── OCaml 5.2 + OPAM ──────────────────────────────────────────────────────────
# Required to build Aeneas. OCaml 4.x is not sufficient.
# ocamlformat must be exactly 0.27.0 — any other version breaks the build.
ENV OPAMROOT=/usr/local/opam \
    OPAMYES=1

RUN opam init --bare --no-setup --disable-sandboxing -a

# ~5-10 min; cached as its own layer so code changes don't re-trigger it.
RUN opam switch create 5.2.0 \
        --packages=ocaml-variants.5.2.0+options,ocaml-option-flambda

RUN opam install -y --switch=5.2.0 \
        calendar core_unix domainslib easy_logging menhir \
        "ocamlformat=0.27.0" ocamlgraph odoc \
        ppx_deriving ppx_deriving_yojson \
        progress unionFind visitors yojson zarith

# ── Charon + Aeneas ───────────────────────────────────────────────────────────
# Aeneas ships a `charon-pin` file that records the exact Charon commit it
# was tested with.  `make setup-charon` reads that file and clones Charon at
# the right commit — the two binaries must always match.
WORKDIR /opt/aeneas

RUN git clone --depth=1 https://github.com/AeneasVerif/aeneas.git .

# Clone Charon at the pinned commit (reads ./charon-pin internally).
RUN make setup-charon

# Build the Charon Rust binary.  The Charon repo ships its own
# rust-toolchain.toml so rustup will download the right toolchain.
RUN make -C charon build-charon-rust \
    && cp charon/bin/charon /usr/local/bin/charon \
    && cp charon/bin/charon-driver /usr/local/bin/charon-driver

# Build the Aeneas OCaml binary (native compilation; no OCaml runtime needed
# at container run time — the binary is self-contained).
RUN eval $(opam env --switch=5.2.0) && make \
    && cp bin/aeneas /usr/local/bin/aeneas

# ── Lean 4 / Lake ─────────────────────────────────────────────────────────────
# The Lean toolchain version must match the one declared in
# /opt/aeneas/backends/lean/lean-toolchain.
ENV ELAN_HOME=/usr/local/elan \
    PATH=/usr/local/elan/bin:$PATH

RUN LEAN_TC=$(cat /opt/aeneas/backends/lean/lean-toolchain) \
    && curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
       | sh -s -- -y --no-modify-path --default-toolchain "$LEAN_TC"

# Pre-fetch Mathlib and pre-compile the Aeneas Lean runtime library so the
# container can work with --network none at run time AND never pay to compile
# the Aeneas lib on the first `lake build` of a session.
# `lake exe cache get` downloads pre-compiled Mathlib .olean files from the CDN
# (~minutes vs ~hours from source); `lake build` then compiles the Aeneas lib's
# own modules (which are NOT on the Mathlib CDN) into oleans baked into the image.
WORKDIR /opt/aeneas/backends/lean
RUN lake update \
    && (lake exe cache get || echo "WARNING: Mathlib CDN unavailable; oleans not pre-cached") \
    && lake build

# ── Lean project template ──────────────────────────────────────────────────────
# Pre-create a minimal lake project that depends on the bundled Aeneas runtime.
# `lake update` runs here (with network) so the manifest is resolved and baked
# into the image.  At agent runtime (--network none) we copy this template into
# /workspace/out/lean and only overwrite the lakefile with the crate-specific
# package/lib names — no further network access needed.
RUN mkdir -p /opt/lean-template \
    && printf 'import Lake\nopen Lake DSL\nrequire aeneas from "/opt/aeneas/backends/lean"\npackage «template» where\nlean_lib «Template» where\n' \
       > /opt/lean-template/lakefile.lean \
    && cd /opt/lean-template && lake update

# ── Lean LSP MCP (interactive proof development) ─────────────────────────────────
# PROVE drives the Lean language server through lean-lsp-mcp (lean_goal,
# lean_multi_attempt, lean_diagnostic_messages, …) for goal-directed proofs instead of
# blind `lake build` guessing. It is a Python package, so install Python and place the
# MCP server in an isolated venv. At run time it is launched over `docker exec -i` with
# `--transport stdio`; the network-only search tools are disabled via `--disable-tools`
# (the agent container runs `--network none`; the essential LSP tools are all local).
RUN apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
       python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/leanmcp \
    && /opt/leanmcp/bin/pip install --quiet --upgrade pip \
    && /opt/leanmcp/bin/pip install --quiet lean-lsp-mcp
ENV LEAN_LSP_MCP_BIN=/opt/leanmcp/bin/lean-lsp-mcp

# ── Workspace layout ───────────────────────────────────────────────────────────
RUN git config --global user.email "lusterna@agent" \
    && git config --global user.name "Lusterna"

RUN mkdir -p /workspace/repo /workspace/out
WORKDIR /workspace
