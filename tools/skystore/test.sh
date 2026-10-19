#!/usr/bin/env bash
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
set -euo pipefail
cd "$(dirname "$0")/../.."
bazel_command="${BAZEL:-bazel}"
"$bazel_command" test "$@" --test_output=errors //tools/skystore:all
