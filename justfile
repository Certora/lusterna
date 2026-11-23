default: lint test

setup:
  git submodule update --init
  # Install the OCaml packages that `deps/aeneas/src/aeneas.opam` lists, without asking
  opam install deps/aeneas/src --deps-only --yes
  make -C deps/aeneas setup-charon
  git -C deps/aeneas/charon submodule update --init
  # `opam exec` puts `dune` of the opam switch on `PATH` for the Aeneas build
  opam exec -- make -C deps/aeneas
  cd deps/aeneas/backends/lean && lake exe cache get

bump-aeneas rev:
  [ -z "$(git status --porcelain)" ] || { echo "error: commit or stash your changes before a bump" >&2; exit 1; }
  git -C deps/aeneas fetch origin
  git -C deps/aeneas checkout {{rev}}
  git add deps/aeneas
  just setup
  cd lusterna-spec && lake update
  cd lusterna-lean && lake update
  just test

bump-solanalib rev:
  [ -z "$(git status --porcelain)" ] || { echo "error: commit or stash your changes before a bump" >&2; exit 1; }
  git -C deps/solanalib fetch origin
  git -C deps/solanalib checkout {{rev}}
  git add deps/solanalib
  cd lusterna-lean && lake update
  just test

lint:
  cargo check
  cargo clippy

check-toolchains:
  #!/usr/bin/env bash
  for dir in deps/solanalib lusterna-spec lusterna-lean; do
    if [ "$(cat $dir/lean-toolchain)" != "$(cat deps/aeneas/backends/lean/lean-toolchain)" ]; then
      echo "error: $dir/lean-toolchain must match deps/aeneas/backends/lean/lean-toolchain" >&2
      exit 1
    fi
  done

test: check-toolchains
  cargo test
  cd lusterna-spec && lake test
  cd lusterna-lean && lake test

build: 
  cd lusterna-spec && lake build
  cd lusterna-lean && lake build
  cargo build --release
