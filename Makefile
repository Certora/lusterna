.PHONY: build

build:
	cargo build --release
	cd llbc-rewrite && cargo build --release
