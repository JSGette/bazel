#!/usr/bin/env bash
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
set -euo pipefail
cd "$(dirname "$0")/../.."
bazel_command="${BAZEL:-bazel}"
cargo fmt --manifest-path tools/skystore/Cargo.toml --check
cargo clippy --manifest-path tools/skystore/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path tools/skystore/Cargo.toml
cargo build --locked --release --manifest-path tools/skystore/Cargo.toml
"$bazel_command" test "$@" --test_output=errors //tools/skystore:SkystoreClientTest
"$bazel_command" run "$@" //tools/skystore:interop_test -- "$PWD/tools/skystore/target/release/skystore"
