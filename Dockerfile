FROM debian:bookworm-slim

# ── system packages ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git build-essential \
        libssl-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

# ── Rust / Cargo ───────────────────────────────────────────────────────────────
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --no-modify-path --default-toolchain stable
RUN rustup component add rustfmt clippy

# ── LLVM / Charon (Aeneas front-end) ──────────────────────────────────────────
# Charon translates Rust MIR → LLBC; Aeneas then translates LLBC → Lean/Coq.
# We install both from their published releases.
ARG CHARON_VERSION=0.1.55
ARG AENEAS_VERSION=0.1.0

RUN cargo install --locked charon --version ${CHARON_VERSION} 2>&1 | tail -1

# Aeneas is distributed as a pre-built binary alongside the Lean library.
# Adjust the URL when a newer release is available.
RUN curl -fsSL \
    "https://github.com/AeneasVerif/aeneas/releases/download/v${AENEAS_VERSION}/aeneas-linux-x86_64" \
    -o /usr/local/bin/aeneas \
    && chmod +x /usr/local/bin/aeneas

# ── Lean 4 / Lake ─────────────────────────────────────────────────────────────
ENV ELAN_HOME=/usr/local/elan \
    PATH=/usr/local/elan/bin:$PATH
RUN curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    | sh -s -- -y --no-modify-path --default-toolchain leanprover/lean4:stable
# lake is bundled with lean; verify it is on PATH
RUN lake --version

# ── workspace layout ───────────────────────────────────────────────────────────
# The host bind-mounts:
#   <repo>      → /workspace/repo   (read-only)
#   <work_path> → /workspace/out    (read-write)
RUN mkdir -p /workspace/repo /workspace/out
WORKDIR /workspace

CMD ["/bin/bash"]
