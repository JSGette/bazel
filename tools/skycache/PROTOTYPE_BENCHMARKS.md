# Parallel validation and idle-retention prototype

The structure decision, rejected implementations, measured results and limitations
are in `docs/jsgette/skycache-structure-decision-2026-10-24.md` and the accompanying
prototype report. The original research lives on the fork's `jsgette/master`
branch under `docs/jsgette`.

Build and check the implementation with Bazel 9.3.0:

```bash
bazel test //src:bazel-dev //src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:all
cp bazel-bin/src/bazel-dev /tmp/bazel-skycache
```

Use separate output bases for this binary. Enable idle eviction explicitly:

```text
--experimental_local_skycache=/absolute/cache/outside/workspace
--experimental_analysis_cache_idle_retention=5m
```

Zero disables idle eviction. Positive durations must be whole seconds. The
prototype resets the complete Skyframe evaluator, including loading state, while
preserving the JVM, action-result cache and output files. It rechecks and leases a
published backing cache before resetting; failed publications do not authorize
eviction. Cache misses after reset are recomputed normally. GC is best-effort and
can briefly delay an arriving command; reduced heap does not promise a matching
OS memory reduction.

## Repeated full-command experiment

Use new result directories; the scripts refuse to overwrite a previous run.
`JDK` must point to a JDK 25 directory appropriate for this Bazel build. Configure
normal Java CA trust on the machine; the cloud fixture optionally accepts
`--trust-store`, and discovers this environment's provisioned trust store.

```bash
python3 tools/skycache/idle_retention_benchmark.py \
  --bazel=/tmp/bazel-skycache --jdk="$JDK" --root=/tmp/skycache-idle-run \
  --rounds=3 --with-off-control --repeated-import-test
python3 tools/skycache/idle_import_probe.py \
  --bazel=/tmp/bazel-skycache --fixture=/tmp/skycache-idle-run \
  --root=/tmp/skycache-cold-import
```

The first command creates its own Git workspace with 2,048 provider-heavy leaves
and one file-writing action. It prepares a module lockfile before measurements,
uses `--lockfile_mode=error` during builds, verifies output bytes independently,
records full client duration with blocking waitpid, samples server PSS, preserves
profiles and checks same-JVM recovery. It waits for ordinary idle GC before the
retained-memory observation and verifies actual eviction receipts. Off controls
also start after a graph reset and preserve the action cache/output files.

The second command restarts only that completed fixture's server and requires a
fully imported, zero-novel-write command to schedule eviction, followed by a
successful same-JVM recovery. It is a correctness test, not a cold-server timing
comparison. These scripts do not flush machine-wide filesystem caches.

The older `idle_retention_probe.py` preserves the original three-build protocol
and setup-failure recovery history. Prefer the repeated harness for new timings.

## Validator and store mechanism experiments

```bash
bazel build \
  //src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:ParallelDependencyValidationBenchmark \
  //src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:ParallelPackedStoreBenchmark
bazel-bin/src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/ParallelDependencyValidationBenchmark \
  --jvm_flag=-Xmx2g /tmp/dependency-run-1
bazel-bin/src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/ParallelDependencyValidationBenchmark \
  --jvm_flag=-Xmx2g /tmp/dependency-run-2
bazel-bin/src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/ParallelPackedStoreBenchmark \
  --jvm_flag=-Xmx2g /tmp/packed-small 20000 256
bazel-bin/src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/ParallelPackedStoreBenchmark \
  --jvm_flag=-Xmx2g /tmp/packed-medium 8000 4096
```

Each executable requires an absent output directory. The validator benchmark
alternates the current implementation with an unchanged serial baseline from
`6f451b2`, at 1/2/4/8 threads, with two warmups and five measured rounds. It uses
actual packed records and file witnesses; initial snapshot construction and final
revalidation occur outside its timed section. The staged JSON label
`root_stripes` denotes the current parallel implementation, including whole-
subtree coalescing and deferred sibling work. Statistics must show equal record
and witness counts. The packed benchmark includes sealing/fsync in durable-write
time and byte-checks every read. Each round writes a new private pack and removes
only that closed fixture pack afterward.

`ProofLayoutBenchmark.java` is a separate structural experiment comparing compact
DAGs, flat arrays, dense bitsets and Roaring 1.3.0. Compile/run it with that Maven
JAR on the classpath. Its SHA-256 is
`73443a71a2e2401c7d518a487983539fcaf5f9652efbd178f929e7b6d36881d0`.
It measures integer-ID set traversal, not filesystem validity or build latency.
Roaring is not a production dependency.
