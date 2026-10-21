# Using the experimental local SkyCache

Use a Bazel binary built from [`jsgette/skycache-concurrent`](https://github.com/JSGette/bazel/tree/jsgette/skycache-concurrent).
Stock Bazel does not include this backend. Each server keeps its full Skyframe graph in RAM and
its own output base. Independent servers can publish and read immutable analysis records in one
shared directory. Action outputs still use ordinary execution and action caching.

The tested scope is same-user Linux/WSL ext4, Git worktrees of one local repository, and compatible
Bazel/configuration/toolchain inputs. Separate clones and cross-machine sharing are not the current
identity contract. Remote gRPC is future work. This is experimental: consult the
[current status and measurements](measurements/skycache-concurrent-development-2026-10-20.md)
for passing gates, failed attempts and remaining acceptance work.

## Paths and invocation

Choose a cache directory outside every source worktree and separate from action/repository caches.
Use the same compiled binary for all servers and a different output base for each worktree.
The following is a template; replace the paths, target and active package prefix for your project:

```sh
cd /home/me/project-feature-a
/path/to/bazel-skycache --output_base=/home/me/bazel-output/feature-a build //app:app \
  --experimental_local_skycache=/home/me/bazel-cache/skycache \
  --experimental_active_directories=app \
  --experimental_strict_repo_env
```

Run the corresponding command from feature B with its own output base and the same SkyCache path.
Both can run at once. Successful commands publish their validated results; misses fall back to
local analysis. A cold writer can be expensive, and restored analysis still consumes RAM.

Active directories use comma-separated workspace-relative package prefixes. They define the
upstream serialization frontier: nodes inside active directories are evaluated locally, while
eligible dependency analysis outside the active area can be shared. This flag overrides active
directories in `PROJECT.scl`. Without active directories, upstream falls back to full serialization,
which can increase work. The substantial Datadog probe uses `cmd/cluster-agent` for
`//cmd/cluster-agent:cluster-agent`.

Keep effective repository, action and test environments compatible. Explicitly exposed values
remain significant to reuse. Strict repository environment mode avoids treating unrelated client
environment variables as repository inputs; retain any environment inputs your rules actually need.

Keep Bazel's normal native repository cache enabled. If its default location lacks space, place
both downloads and repository contents on a suitable local filesystem, identically for all servers:

```sh
--repository_cache=/home/me/bazel-cache/repositories
--repo_contents_cache=/home/me/bazel-cache/repositories/contents
```

These flags are distinct from SkyCache and action caching. In the tested Datadog setup, disabling
native repository sharing produced different `rules_python` bytecode-cache link paths and correctly
prevented shared analysis reuse. Existing valid repository marker files can preserve old bindings;
the recorded fixture preparation explains the native fetch/rebinding procedure used in that test.
Do not change validation fingerprints to manufacture hits.

## Resource policy

For a standalone invocation, use available CPUs and an action-memory budget that leaves room for
the JVM, OS and other processes. `--jobs` controls action concurrency; it does not bound graph size.
`--local_resources=memory=...` is a scheduling budget, not a hard RAM limit. A JVM heap cap also does
not include native allocations or child processes.

The matched Datadog matrix fixes each server at jobs=2, action-memory=3072 MiB and Xmx4g. Standalone
repository preparation used jobs=32, action-memory=8192 MiB and Xmx4g on WSL with 32 logical CPUs and
15.44 GiB RAM. Keep comparison phases matched rather than changing only one side's limits.

Bazel source builds/tests for this work use Cache&Carry:
`--config=efcache --bes_backend= --remote_upload_local_results=false`.
The Datadog acceptance probe disables remote action caching to isolate analysis reuse. Ordinary
workloads can use their separately configured action cache; it is a different layer.

## Checking reuse

After a command, inspect the console configured-target hit summary and these files in that server's
output base:

- `local-skycache.json`: reads, shared reads, writes, deduplication, candidate admission/rejection,
  publication and failure counters.
- `local-skycache-validation.json`: dependency-validation work and memoization counters.
- `local-skycache-gc.json`: statistics from the most recent idle-collection attempt that returned a report.

For proof of independent reuse, finish a writer command, start a separate reader server/output base,
and require positive configured-target hits and shared reads. Correct output with zero hits proves
fallback correctness only. Compare changed outputs with independent cache-off controls and retain
the exact binary, source, lockfile and resource settings. Raw BEP and JVM logs can contain environment
data; the public development report contains curated evidence rather than those raw logs.

Local retained-value validation runs interruptibly in the command's invalidation stage. Its full
filesystem and dependency-check time counts toward command latency; it does not use the remote
request dispatcher's ten-second deadline. Warm builds can therefore spend noticeable time validating
inputs even when they download no analysis. Missing original evidence still causes invalidation.
Configuration forwarding can leave additional graph keys holding deserialized values without a
direct lookup record, so a small nonzero invalidation count alone does not prove that imported
analysis was discarded. Compare retained counts, dependency rejections and subsequent reload hits.

## Storage limits and disabling the backend

| Option | Default | Meaning |
|---|---:|---|
| `--experimental_local_skycache_max_publications` | `64` | Maximum publications admitted per command; zero disables shared reads. |
| `--experimental_local_skycache_max_metadata_mb` | `256` | Encoded manifest/index admission budget; decoded metadata and graphs use additional RAM. |
| `--experimental_local_skycache_gc_max_size` | `5G` | Soft size limit for recognized lease-aware cache data. |
| `--experimental_local_skycache_gc_max_age` | `7d` | Publication-age limit, not time since last access. |
| `--experimental_local_skycache_gc_idle_delay` | `30s` | Delay before an idle server attempts collection. |

Active readers/writers and their publication dependencies remain protected, so size/age limits can
be exceeded. Legacy layouts and unrecognized files are preserved. Setting either GC limit to zero
disables that dimension; setting both to zero disables automatic collection. Avoid manual deletion
while servers may still hold cache data; use the lease-aware collector for live cleanup.

Pass `--experimental_local_skycache=` to disable the backend. This does not erase cache data. Keep
the normal in-memory graph; the local backend rejects the upstream minimize-memory mode. See the
[architecture and remaining roadmap](plans/skycache_local_backend_f13e5f7f.plan.md) for the design
contract and future transport work.


## Fingerprint version changes

The content-digest implementation uses project namespace `local-project-v3-content-sha256` and
source domain `local-source-v11-content-sha256`. Earlier publications remain untouched in their
previous namespace. A new binary initially rebuilds its analysis cache; mixed fingerprint versions
do not share entries, and rollback selects the previous namespace. No manual cache rewrite is
required. The lease-aware collector continues to manage recognized inactive publications.

Large-file content digests are reused only within one fresh validation/capture pass, with Unix
device/inode, ctime, mtime and size checks. No digest memo survives between passes or commands.
All paths, modes, links, directory membership and expected content digests remain checked.
