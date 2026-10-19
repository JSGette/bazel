# Personal branches

Branches of [JSGette/bazel](https://github.com/JSGette/bazel) and the local
clone they come from. "Local only" branches are not pushed.

## Published

| Branch | Base | What it is |
|---|---|---|
| [`jsgette/master`](https://github.com/JSGette/bazel/tree/jsgette/master) | `master` | Personal docs: module architecture, rewrite designs and plans (`docs/jsgette/`), this list. |
| [`jsgette/skycache-local`](https://github.com/JSGette/bazel/tree/jsgette/skycache-local) | `master` | Local Skycache backend: RAM + packed disk fingerprint value stores, `LocalSkycacheModule`, warm uploads without an analysis cache service, tolerated absolute symlinks. Rebased from `skycache-local` (9.2.0). |
| [`jsgette/otel_bazel`](https://github.com/JSGette/bazel/tree/jsgette/otel_bazel) | `master` | OTel tracing module fed by the event bus: `--experimental_otel_trace_json` / `--experimental_otel_trace_endpoint` (OTLP HTTP and gRPC). Rebased from `otel_bazel`, without the repo_env NPE fix that has since landed upstream. |
| [`bep-action-strategy-details`](https://github.com/JSGette/bazel/tree/bep-action-strategy-details) | `master` | Report spawn runner and cache hit in BEP `ActionExecuted` events. [bazelbuild/bazel#31811](https://github.com/bazelbuild/bazel/pull/31811) (open). |
| [`jsgette/28605/fix_NPE_on_repo_env`](https://github.com/JSGette/bazel/tree/jsgette/28605/fix_NPE_on_repo_env) | `master` | NPE on `--repo_env` without a value. [bazelbuild/bazel#28606](https://github.com/bazelbuild/bazel/pull/28606), imported upstream. |
| [`jsgette/issue_29129/fix_broken_fallback_for_remote_executor`](https://github.com/JSGette/bazel/tree/jsgette/issue_29129/fix_broken_fallback_for_remote_executor) | `master` | Fallback mechanism for `--remote_executor` ([bazelbuild/bazel#29129](https://github.com/bazelbuild/bazel/issues/29129)). |
| [`jsgette/skystore-phase0`](https://github.com/JSGette/bazel/tree/jsgette/skystore-phase0) | `jsgette/master` | Phase 0 Rust/Java Skyframe cold-plane IPC prototype: batched UDS value store, protocol/lifecycle tests, Linux measurements. No production evaluator changes. |

## Local only

| Branch | Base | What it is |
|---|---|---|
| `jsgette/lazy-packed-action-cache` | `master` | Action cache entries in append-only pack files read lazily, with compaction. Under review. |
| `remote-missing-execution-timestamps` | `master` | Don't report remote spawns as starting at the Unix epoch. |
| `jsgette/fix_build_file_not_found` | `master` | Fix a `build` folder being read as a file. |
| `skycache-local` | `9.2.0` | Original Skycache backend, superseded by `jsgette/skycache-local`. |
| `otel_bazel` | `9.0.0rc1`-era `master` | Original OTel module, superseded by `jsgette/otel_bazel`. |
| `release-8.5.1` | `8.5.0` | Release tracking branch, no own commits. |
