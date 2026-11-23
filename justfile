export AENEAS_ROOT := justfile_directory() + "/deps/aeneas"
export PATH := AENEAS_ROOT + "/bin:" + AENEAS_ROOT + "/charon/bin:" + env_var('PATH')

default: lint test

setup:
  git submodule update --init
  make -C deps/aeneas setup-charon
  git -C deps/aeneas/charon submodule update --init
  make -C deps/aeneas/charon build-charon-rust
  make -C deps/aeneas
  cd deps/aeneas/backends/lean && lake exe cache get

lint:
  cargo check
  cargo clippy

test: 
  cargo test
  cd lusterna-spec && lake test
  cd lusterna-lean && lake test

build: 
  cd lusterna-spec && lake build
  cd lusterna-lean && lake build
  cargo build --release
