#!/usr/bin/env bash
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
set -euo pipefail
cd "$(dirname "$0")/../.."
bazel_command="${BAZEL:-bazel}"
"$bazel_command" build "$@" -c opt //tools/skystore:skystore //tools/skystore:ipc_benchmark
# Resolve the configured output directory, including a caller's output base.
bazel_bin="$("$bazel_command" info "$@" -c opt bazel-bin)"
socket_dir="$(mktemp -d /tmp/skystore-bench.XXXXXXXX)"
server_pid=""
cleanup() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -f "$socket_dir/sock"
  rmdir "$socket_dir"
}
trap cleanup EXIT
"$bazel_bin/tools/skystore/skystore" "$socket_dir/sock" &
server_pid=$!
for attempt in {1..100}; do
  [[ -S "$socket_dir/sock" ]] && break
  kill -0 "$server_pid"
  sleep 0.05
done
[[ -S "$socket_dir/sock" ]]
# Timing happens inside the Java program, after connection and JIT warmup.
for value_bytes in 64 1024; do
  for batch in 1 64 256; do
    "$bazel_command" run "$@" -c opt //tools/skystore:ipc_benchmark -- \
      "$socket_dir/sock" "$batch" "$value_bytes" 5000
  done
done
