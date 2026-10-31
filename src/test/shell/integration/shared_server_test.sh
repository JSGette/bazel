#!/usr/bin/env bash
# Copyright 2026 The Bazel Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CURRENT_DIR}/../integration_test_setup.sh" \
  || { echo "integration_test_setup.sh not found!" >&2; exit 1; }

function set_up() {
  shared_workspace="$(pwd -P)"
  shared_root="$TEST_TMPDIR/shared-$TEST_name"
  shared_control="$shared_root/shared_servers/v2/prototype/control"
  shared_args=()
}

function shared_bazel() {
  # Keep shell-launch context identical across foreground, background and
  # command-substitution clients: v1 deliberately compares the full JVM env.
  env SHLVL=1 OLDPWD="$shared_workspace" bazel --output_user_root="$shared_root" --experimental_shared_server=prototype \
    "${shared_args[@]}" "$@"
}

function tear_down() {
  cd "$shared_workspace"
  shared_bazel shutdown >>"$TEST_log" 2>&1 || fail "Shared shutdown failed"
}

function assert_shared_pid() {
  local actual
  actual=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Shared info failed"
  assert_equals "$1" "$actual"
}

function expect_shared_rejection() {
  local status=0
  shared_bazel "$@" >"$TEST_log" 2>&1 || status=$?
  assert_equals 36 "$status"
  expect_log 'Shared server'
}

function test_shared_namespace_and_legacy_coexistence() {
  local shared_pid output legacy_pid
  shared_pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  output=$(shared_bazel info output_base 2>"$TEST_log") || fail "Info failed"
  [[ "$output" == "$shared_root/shared_servers/v2/prototype/engines/"* ]] \
    || fail "Build state is outside the private engine namespace: $output"
  assert_equals "$shared_pid" "$(cat "$shared_control/server/server.pid.txt")"
  [[ ! -e "$output/server/server.pid.txt" ]] || fail "PID exposed in engine output base"
  [[ -e "$shared_control/lock" && -e "$output/lock" ]] || fail "Missing separate locks"
  legacy_pid=$(bazel --output_user_root="$shared_root" info server_pid 2>"$TEST_log") || fail "Legacy startup failed"
  assert_not_equals "$legacy_pid" "$shared_pid"
  bazel --output_user_root="$shared_root" --host_jvm_args=-Dlegacy_restart=true \
    info server_pid >"$TEST_log" 2>&1 || fail "Legacy restart failed"
  assert_shared_pid "$shared_pid"
  bazel --output_user_root="$shared_root" shutdown >"$TEST_log" 2>&1 \
    || fail "Legacy shutdown failed"
  assert_shared_pid "$shared_pid"
}

function test_shared_ordered_jvm_options() {
  shared_args=(--host_jvm_args=-Dshared.order=first --host_jvm_args=-Dshared.order=last)
  local pid
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  shared_args=(--host_jvm_args=-Dshared.order=last --host_jvm_args=-Dshared.order=first)
  local status=0
  shared_bazel info server_pid >"$TEST_log" 2>&1 || status=$?
  shared_args=(--host_jvm_args=-Dshared.order=first --host_jvm_args=-Dshared.order=last)
  assert_equals 36 "$status"
  expect_log 'startup profile'
  assert_shared_pid "$pid"
  expect_shared_rejection --max_idle_secs=17 info server_pid
  assert_shared_pid "$pid"
}

function test_shared_installation_mismatch() {
  local pid
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  expect_shared_rejection --install_base="$TEST_TMPDIR/other-install" info server_pid
  expect_log 'binary is incompatible'
  assert_shared_pid "$pid"
}

function test_shared_environment_mismatch() {
  local pid
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  SHARED_CONTEXT_SENTINEL=private_value expect_shared_rejection info server_pid
  expect_not_log 'private_value'
  assert_shared_pid "$pid"
}

function test_shared_other_workspace_cannot_shutdown() {
  local pid status=0
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  mkdir -p "$shared_workspace/other"
  touch "$shared_workspace/other/MODULE.bazel"
  (cd "$shared_workspace/other" && shared_bazel shutdown) >"$TEST_log" 2>&1 || status=$?
  assert_equals 36 "$status"
  assert_shared_pid "$pid"
}

function test_shared_concurrent_startup() {
  shared_bazel info server_pid >"$TEST_TMPDIR/first.pid" 2>"$TEST_TMPDIR/first.log" &
  local first=$!
  shared_bazel info server_pid >"$TEST_TMPDIR/second.pid" 2>"$TEST_TMPDIR/second.log" &
  local second=$!
  wait "$first" || fail "First client failed"
  wait "$second" || fail "Second client failed"
  assert_equals "$(cat "$TEST_TMPDIR/first.pid")" "$(cat "$TEST_TMPDIR/second.pid")"
  assert_shared_pid "$(cat "$TEST_TMPDIR/first.pid")"
}

function test_shared_missing_profile_does_not_restart() {
  local pid status=0
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  mv "$shared_control/server/startup_profile" "$shared_control/server/profile.saved"
  shared_bazel info server_pid >"$TEST_log" 2>&1 || status=$?
  mv "$shared_control/server/profile.saved" "$shared_control/server/startup_profile"
  assert_equals 36 "$status"
  assert_shared_pid "$pid"
}

function test_shared_unavailable_server_is_not_replaced() {
  local pid status=0
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  mv "$shared_control/server/server_info.rawproto" "$shared_control/server/info.saved"
  shared_bazel info server_pid >"$TEST_log" 2>&1 || status=$?
  mv "$shared_control/server/info.saved" "$shared_control/server/server_info.rawproto"
  assert_equals 36 "$status"
  assert_shared_pid "$pid"
}

function test_shared_damaged_start_time_does_not_replace_live_process() {
  local pid status=0
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  cp "$shared_control/server/server.starttime" "$shared_control/server/start.saved"
  echo damaged > "$shared_control/server/server.starttime"
  shared_bazel info server_pid >"$TEST_log" 2>&1 || status=$?
  mv "$shared_control/server/start.saved" "$shared_control/server/server.starttime"
  assert_equals 36 "$status"
  assert_shared_pid "$pid"
}

function test_shared_control_and_output_locks() {
  local pid output lock ready release holder status waiter
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  output=$(shared_bazel info output_base 2>"$TEST_log") || fail "Info failed"
  for lock in "$shared_control/lock" "$output/lock"; do
    ready="$lock.ready"
    release="$lock.release"
    python3 - "$lock" "$ready" "$release" <<'PY' &
import fcntl, pathlib, sys, time
with open(sys.argv[1], "a") as lock:
    fcntl.lockf(lock, fcntl.LOCK_EX)
    pathlib.Path(sys.argv[2]).touch()
    deadline = time.monotonic() + 30
    while not pathlib.Path(sys.argv[3]).exists():
        if time.monotonic() > deadline:
            raise RuntimeError("lock release timed out")
        time.sleep(0.05)
PY
    holder=$!
    for ((i=0; i<200; i++)); do
      [[ -f "$ready" ]] && break
      sleep 0.05
    done
    [[ -f "$ready" ]] || fail "Lock holder did not become ready"
    status=0
    shared_bazel --noblock_for_lock info server_pid >"$TEST_log" 2>&1 || status=$?
    # Release before asserting to leave a recoverable test fixture on failure.
    if [[ "$status" != 9 ]]; then
      touch "$release"
      wait "$holder"
      fail "Expected lock rejection 9, got $status"
    fi
    shared_bazel info server_pid >"$TEST_TMPDIR/waiter.pid" 2>"$TEST_TMPDIR/waiter.log" &
    waiter=$!
    for ((i=0; i<200; i++)); do
      grep -q 'Waiting for it to complete' "$TEST_TMPDIR/waiter.log" && break
      sleep 0.05
    done
    touch "$release"
    wait "$holder" || fail "Lock holder failed"
    wait "$waiter" || fail "Waiting client failed"
    grep -q 'Waiting for it to complete' "$TEST_TMPDIR/waiter.log" \
      || fail "Client never reached the lock wait"
    assert_equals "$pid" "$(cat "$TEST_TMPDIR/waiter.pid")"
  done
}

function test_shared_output_replacement_fences_until_restart() {
  local pid output first_status=0 shutdown_status=0 restored_status=0 next_pid
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  output=$(shared_bazel info output_base 2>"$TEST_log") || fail "Info failed"
  mv "$output" "$output.original"
  mkdir "$output"
  echo replacement-owned > "$output/sentinel"
  shared_bazel info server_pid >"$TEST_TMPDIR/fenced.log" 2>&1 || first_status=$?
  shared_bazel shutdown >"$TEST_TMPDIR/fenced-shutdown.log" 2>&1 || shutdown_status=$?
  # Restore original objects before assertions so ordinary teardown is possible.
  mv "$output" "$output.replacement"
  mv "$output.original" "$output"
  assert_equals 36 "$first_status"
  assert_equals 36 "$shutdown_status"
  assert_contains 'engine is fenced' "$TEST_TMPDIR/fenced.log"
  assert_equals replacement-owned "$(cat "$output.replacement/sentinel")"
  [[ ! -e "$output.replacement/command.log" ]] || fail "Dispatched against replacement output"
  [[ ! -e "$output.replacement/execroot" ]] || fail "Initialized replacement output"
  shared_bazel info server_pid >"$TEST_log" 2>&1 || restored_status=$?
  assert_equals 36 "$restored_status"
  expect_log 'engine is fenced'
  # Shutdown is allowed only with the original directory objects restored.
  shared_bazel shutdown >"$TEST_log" 2>&1 || fail "Restored engine shutdown failed"
  next_pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Fresh engine failed"
  assert_not_equals "$pid" "$next_pid"
}

function test_shared_missing_output_recreated_by_client_is_fenced() {
  local output status=0
  output=$(shared_bazel info output_base 2>"$TEST_log") || fail "Startup failed"
  mv "$output" "$output.original"
  # The launcher creates a new directory at the old path. It cannot reuse the engine.
  shared_bazel info workspace >"$TEST_TMPDIR/recreated.log" 2>&1 || status=$?
  mv "$output" "$output.replacement"
  mv "$output.original" "$output"
  assert_equals 36 "$status"
  assert_contains 'engine is fenced' "$TEST_TMPDIR/recreated.log"
  [[ ! -e "$output.replacement/command.log" ]] || fail "Dispatched against recreated output"
}

function test_shared_directory_identity_handles_non_ascii_paths() {
  mkdir -p "$shared_workspace/workspace-ü-空"
  cd "$shared_workspace/workspace-ü-空"
  touch MODULE.bazel
  shared_workspace="$(pwd -P)"
  shared_root="$shared_root/output-ü-空"
  shared_control="$shared_root/shared_servers/v2/prototype/control"
  local pid actual
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Non-ASCII startup failed"
  actual=$(shared_bazel info workspace 2>"$TEST_log") || fail "Non-ASCII info failed"
  assert_equals "$shared_workspace" "$actual"
  assert_shared_pid "$pid"
}

function test_shared_rejects_unsupported_options() {
  local option status
  for option in --batch --run_in_user_cgroup --output_base="$TEST_TMPDIR/explicit-engine" \
      --delete_output_base_on_shutdown; do
    status=0
    shared_bazel "$option" info >"$TEST_log" 2>&1 || status=$?
    assert_equals 2 "$status"
  done
  status=0
  shared_bazel clean >"$TEST_log" 2>&1 || status=$?
  assert_equals 2 "$status"
  [[ ! -e "$shared_control" ]] || fail "Rejected options created control state"
}

function test_shared_build_and_test() {
  mkdir -p shared_target
  cat > shared_target/BUILD <<'EOF'
load(":rules.bzl", "local_check_test")
local_check_test(name = "check")
EOF
  cat > shared_target/rules.bzl <<'EOF'
def _impl(ctx):
    executable = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(executable, "#!/bin/sh\necho shared-check-ok\n", is_executable = True)
    return [DefaultInfo(executable = executable)]
local_check_test = rule(implementation = _impl, test = True)
EOF
  shared_bazel build //shared_target:check >"$TEST_log" 2>&1 || fail "Build failed"
  local pid
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Info failed"
  shared_bazel test --test_output=all //shared_target:check >"$TEST_log" 2>&1 || fail "Test failed"
  expect_log 'shared-check-ok'
  assert_shared_pid "$pid"
}

function test_shared_endpoint_rejects_incomplete_protocol() {
  local pid output
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  output=$(shared_bazel info output_base 2>"$TEST_log") || fail "Info failed"
  echo preserve-engine-output > "$output/protocol-sentinel"
  "$(rlocation io_bazel/src/test/java/com/google/devtools/build/lib/server/shared_server_protocol_probe)" \
    "$shared_control/server/server_info.rawproto" "$shared_workspace" "$output" \
    >"$TEST_log" 2>&1 || fail "Shared protocol probe failed"
  expect_log '8 raw Run requests rejected; engine registration unchanged'
  assert_equals preserve-engine-output "$(cat "$output/protocol-sentinel")"
  assert_shared_pid "$pid"
}

function test_shared_v2_leaves_old_namespace_untouched() {
  local old="$shared_root/shared_servers/v1/prototype/control/server"
  mkdir -p "$old"
  echo old-profile-sentinel > "$old/startup_profile"
  echo old-pid-sentinel > "$old/server.pid.txt"
  shared_bazel info server_pid >"$TEST_log" 2>&1 || fail "V2 startup failed"
  shared_bazel shutdown >"$TEST_log" 2>&1 || fail "V2 shutdown failed"
  assert_equals old-profile-sentinel "$(cat "$old/startup_profile")"
  assert_equals old-pid-sentinel "$(cat "$old/server.pid.txt")"
}

function test_shared_ownership_survives_missing_control_metadata() {
  local pid status=0 shutdown_status=0
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  # Suspend the PID watcher while all control metadata is absent. A second
  # launcher must reject before creating a profile or spawning another daemon.
  kill -STOP "$pid"
  mv "$shared_control" "$shared_control.saved"
  shared_bazel info server_pid >"$TEST_TMPDIR/ownership-reject.log" 2>&1 || status=$?
  shared_bazel shutdown >"$TEST_TMPDIR/ownership-shutdown.log" 2>&1 || shutdown_status=$?
  mv "$shared_control" "$shared_control.replacement"
  mv "$shared_control.saved" "$shared_control"
  kill -CONT "$pid"
  assert_equals 36 "$status"
  assert_equals 36 "$shutdown_status"
  assert_contains 'Shared server ownership is held' "$TEST_TMPDIR/ownership-reject.log"
  [[ ! -e "$shared_control.replacement/server/startup_profile" ]] || fail "Replaced live profile"
  [[ ! -e "$shared_control.replacement/server/server.pid.txt" ]] || fail "Spawned another daemon"
  assert_shared_pid "$pid"
}

function test_shared_ownership_rejects_other_namespace_output_alias() {
  local pid output other_control status=0
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  output=$(shared_bazel info output_base 2>"$TEST_log") || fail "Info failed"
  other_control="$shared_root/shared_servers/v2/other/control"
  mkdir -p "$shared_root/shared_servers/v2/other/engines"
  ln -s "$output" "$shared_root/shared_servers/v2/other/engines/$(basename "$output")"
  shared_bazel --experimental_shared_server=other info server_pid >"$TEST_log" 2>&1 || status=$?
  assert_equals 36 "$status"
  expect_log 'Shared server ownership is held'
  [[ ! -e "$other_control/server/startup_profile" ]] || fail "Initialized colliding engine"
  assert_shared_pid "$pid"
}

function test_shared_ownership_released_after_process_death() {
  local pid next_pid
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Startup failed"
  kill -KILL "$pid"
  # The PID may remain as a zombie under the test environment. Its kernel
  # reservations must still be released, without trusting/deleting lock files.
  for ((i=0; i<200; i++)); do
    [[ ! -e "/proc/$pid/stat" ]] && break
    [[ "$(awk '{print $3}' "/proc/$pid/stat")" == Z ]] && break
    sleep 0.05
  done
  next_pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Crash recovery failed"
  assert_not_equals "$pid" "$next_pid"
}

function test_shared_ownership_released_after_failed_startup() {
  local status=0 pid
  shared_bazel --host_jvm_args=-XX:DefinitelyInvalidSharedOwnershipOption \
    info server_pid >"$TEST_log" 2>&1 || status=$?
  [[ "$status" != 0 ]] || fail "Invalid JVM option succeeded"
  pid=$(shared_bazel info server_pid 2>"$TEST_log") || fail "Failed startup retained ownership"
  assert_shared_pid "$pid"
}

run_suite "Shared server startup and control namespace"
