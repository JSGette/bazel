# Native single-JVM worktree MVP

The Linux native launcher now supports two concurrent workspace engines in one JVM:

```sh
# Use the same copied candidate binary, startup options and JVM environment in both shells.
/path/to/bazel --output_user_root=/tmp/shared-bazel \
  --experimental_shared_server=demo --host_jvm_args=-Xmx4g \
  --host_jvm_args=-XX:ActiveProcessorCount=4 build //path:target
```

Run that command from each worktree. `info server_pid` reports the same PID; `info
output_base` reports different private directories. The Python process supervisor
is not used. The native v3 namespace is separate from the older v1/v2 prototypes.

Each engine retains its own runtime, modules, evaluator, external repositories,
command lock, action cache, execroot, logs and cancellation context. Graph-backed
global interner pooling is disabled. Shell `PWD` and `OLDPWD` stay in the RPC's
client environment, and are excluded from JVM startup identity. Other JVM startup
settings and environment variables must match. Wrappers such as Bazelisk may add
workspace-specific directories to PATH; normalize the JVM launch PATH and forward
the original PATH with `--client_env=PATH=...` when using such a wrapper.

Eligible local spawns use Bazel's existing REAPI action digest (including command,
environment, input Merkle tree, outputs and execution platform), a daemon-private
disk CAS, and a bounded registry of in-flight keys. An equal concurrent request
waits until the producer has published a complete result. It then materializes
outputs into its own execroot using the existing cache implementation. The registry
retains no graph values, artifacts, reporters or engine callbacks. Uncacheable
spawns execute privately. Failed/cancelled producers release the key so another
consumer can execute; cancelling a waiter leaves its producer alone.

The native invocation policy fixes two action jobs, two loading threads, two local
test jobs, two estimated CPUs and 1920 MiB action memory per engine. The two-engine
bound limits aggregate admission. These estimates are not hard RSS or CPU quotas.
Disk cache publication is synchronous; remote execution is disabled. Fetched repo
contents remain private because their file locks assume a single JVM owner.

This experimental slice disables persistent workers, dynamic execution, sandbox
reuse, global profiling/debugging, allocation tracking and process-wide metrics.
BEP and execution logs remain supported. The policy overrides conflicting execution,
cache and resource settings; inspect the canonical command line in the BEP. No
shared Skyframe graphs or Starlark compilation reuse is claimed. Equal immutable
directory-entry lists and Starlark line-offset tables are weakly interned across
engines. Workspace paths, AST nodes, graph edges and invalidation state remain
private. The Agent sample retained about 2.2 MiB less live heap with two engines;
see the report for the measurement scope. The process remains a shared failure
domain.

`shutdown` drains only the selected engine. Another engine can continue in the
same PID. The last engine's shutdown terminates the daemon. Closed or failed
engine slots remain reserved until process exit; restart the daemon to reopen
one. There is no idle eviction, automatic orphan recovery, or automatic disk-cache
collection in this slice. Directory replacement fences the affected engine.

Run the native end-to-end acceptance harness with a fresh output directory:

```sh
python3 tools/multiworktree/native_integration_test.py \
  --bazel /absolute/path/to/copied/bazel \
  --root /absolute/path/to/new/acceptance-directory \
  --repository-cache /absolute/path/to/repository-cache
```

The harness proves one PID, divergent build/test barriers, equal-action sharing,
warm A/B/A, glob additions/type changes/restoration, source/environment/configuration
isolation, failure and cancellation
isolation, same-engine serialization, bounded
engine admission, scoped shutdown and final daemon exit. It retains commands,
BEP, execution logs and a machine-readable result. See the
[execution report](../../docs/jsgette/plans/multi-worktree-native-mvp-2026-10-31.md)
for the DataDog Agent experiment and exact limitations.

For the consolidated limitations, remaining work and completion gates, see the
[completion roadmap](../../docs/jsgette/plans/multi-worktree-completion-roadmap.md).
