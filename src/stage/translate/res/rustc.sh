#!/usr/bin/env bash
for arg in "$@"; do
    case "$arg" in
    -V | --version | -vV)
        rustc "$@" | sed 's/-nightly//'
        exit "${PIPESTATUS[0]}"
        ;;
    esac
done
exec rustc "$@"
