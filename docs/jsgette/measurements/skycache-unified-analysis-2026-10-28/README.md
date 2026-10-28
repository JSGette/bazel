# Paired baseline foundation

See [recorded implementation results](RESULTS.md) for native smoke evidence,
test results, the known identity failure and the remaining acceptance gates.
See the [retention and attribution increment](RETENTION.md) for the next code
correction, broader workload results and its measured limitations.

P00 provides a stdlib Python harness and a deterministic fixture. It implements
no optimization and establishes no performance improvement. The committed
starting point is `4f5ab75ca9` (documentation at `2c2088c880`), **not** the paper's
uncommitted v8 source tree. Do not import that tree's dirty changes or the
rejected v9 read-to-write experiment to obtain this baseline.

## Tiny smoke run

Use Python 3.11+ on Linux/WSL and an already-built standalone Bazel binary from
the declared source. The run directory must be new and its parent must exist.

```sh
python3 docs/jsgette/measurements/skycache-unified-analysis-2026-10-28/paired_baseline.py \
  --bazel /absolute/path/to/standalone-bazel \
  --source-repo /absolute/path/to/source-checkout \
  --source-commit FULL_HEAD_SHA \
  --run-dir /absolute/path/to/new-smoke-run \
  --pairs 2
```

The supplied commit must equal the checkout's full HEAD. The harness records
its Git tree, complete tracked diff, and hashes of untracked files. It records
the source snapshot's explicit `clean` or `dirty` state without relabeling a
dirty operator-supplied checkout as a committed-source build. It records
the binary SHA-256 and checks it after every command. This records an operator's
binary-to-source declaration; it does not prove the binary was built from those
sources. Preserve native build provenance separately before making that claim.
Use the isolated committed baseline and a standalone binary, not bazelisk or a
mutable agent-overlay installation.

The recorded harness hash is checked immediately before launching each worker
and after each command, including failed worker execution. A changed script
fails the run rather than attributing new worker code to the old hash.

The fixture uses `ctx.actions.write` to emit one fixed file without a shell
compiler or external rule dependency. Normal Bazel installation/module
resolution can still require its usual runtime resources. The input manifest
lists the exact target and expected output SHA-256. `--manifest` may select
another equally small self-contained fixture directory. The harness copies the
fixture, rejects input symlinks and verifies every copied input after each
command. Original inputs and caches remain untouched.

`--producer-manifest` optionally selects different seed-producer targets and
expected outputs. It must be a manifest in the **same directory** as
`--manifest`, preserving a single frozen input inventory. Both specs and their
file identities are recorded. The producer validates its own complete outputs;
all paired cache-off/current-cache consumers validate the `--manifest` outputs.
When omitted, producer and consumer use the same manifest as before. The
[bounded DAG fixtures](fixtures/README.md) support broad, sparse and disjoint
demand, including broad-seed → sparse/disjoint comparisons.

## Comparison contract

One recorded producer primes the local seed before measured arms. Its cost is
retained separately. Each measured arm uses the same binary, controlled
environment, copied workspace and build flags, with a fresh output base and
batch JVM. There is no ordinary remote/disk action cache. Each `current-cache`
arm receives an independent copy of the seed; `cache-off` passes an empty local
Skycache path. Remote Skycache is disabled in both. Command-end publication is
inside measured time.

Order is deterministic and balanced: off/on, on/off, repeated. `--pairs` must
be even and at least two. The repository cache and installation directory are
shared and warmed by the producer. Kernel pages are **uncontrolled**, so this
is not a cold-storage benchmark. No synthetic flush, timing proxy, forced GC,
diagnostic agent or hidden JVM reuse is used. `--ignore_all_rc_files` excludes
user/repository rc settings. Explicit heap, metadata/publication limits, disk
quotas and idle-retention settings are recorded in `manifest.json`; idle
retention is disabled for the batch cohort. CPU model, affinity and Linux
MemTotal describe the execution environment (WSL-visible RAM when applicable).
Only the documented environment allowlist is passed; unrelated inherited
variables and secrets are omitted.

Success requires requested target completions, recursive default output groups,
and the complete expected output digest set to agree. The BEP must end in its
unique `lastMessage`, include one successful `buildFinished`, and contain all
announced child events. Per the protobuf contract, an omitted `exitCode.code`
means zero when the `exitCode` object is present; deprecated `overallSuccess`
is not used as a substitute. Missing/truncated
evidence, additional outputs, missing seed publication, changed binary/inputs,
nonzero exit and timeout fail the run. Outputs must resolve inside the arm's
output base. Timeout kills only the process group started for that command.
Artifacts remain after failure. No directory is deleted; no Bazel clean or
shutdown command is issued.

The validator requires exactly one successful configured completion per fixture
target, keyed by its complete BEP target identity. Cache hits can additionally
emit an unconfigured (`configuration.id = "none"`) empty `aborted` placeholder.
That exact placeholder is recorded separately and never counts as success.
Duplicate real completions, failed/aborted configured targets, missing configured
success and other unconfigured completion payloads remain errors.

The original `smoke-baseline-01` failure is preserved: Bazel commands succeeded,
but the initial validator rejected the legitimate unconfigured placeholder.
`smoke-baseline-02` completed its five commands successfully. Later terminal
validation is applied read-only to its saved BEPs; no original run status,
raw artifact or command timing is rewritten by that validation.

## Evidence API

`fixture.schema.json` describes the fixture input; `evidence.schema.json`
describes version 1 of the root evidence files. Python checks relevant input
and semantic output constraints directly without a JSON Schema dependency.

- `manifest.json`: source, binary, harness and input identity; environment,
  machine/Python description, declared settings, method and full run order.
  `fixture` is the consumer spec; `producer_fixture` is the seed-producer spec;
  `manifest_files` records each selected filename, original path and SHA-256.
- `source.diff`: tracked diff against the recorded commit. Untracked hashes are
  in the manifest; untracked contents remain in the source checkout.
- `runs/producer/` and `runs/pNN-ARM/`: `command.json`, stdout/stderr,
  `metrics.json`, BEP, Bazel profile, passive GC log and private output base.
- Each run's `diagnostics/` copies available `local-skycache*.json` and
  `remote-skycache.json` from its output base, including optional
  `local-skycache-ownership.json`. `diagnostics_sha256` records their hashes.
  Ownership `before_finish` and `after_session_close` snapshots, if emitted by
  the binary, are advisory counts, not measured retained heap or a GC proof.
- `summary.json`: attempted commands and validation, terminal status/error,
  raw-artifact hashes, and paired elapsed differences **current-cache minus
  cache-off**; negative values favor caching.
- `workspace/`, `caches/`, `repository-cache/`, `bazel-user/`, `home/`, `tmp/`
  and `links/`: owned runtime state retained for inspection.

Each command has a fresh Python worker, allowing per-command child rusage rather
than a cumulative experiment maximum. Timing uses `CLOCK_MONOTONIC_RAW` where
available, otherwise explicitly named `monotonic`. Linux user/system child CPU
and raw `ru_maxrss` in KiB are preserved. RSS is the maximum child-process RSS,
not a simultaneous process-tree sum. BEP's major/full-GC retained-heap metric
and passive GC occupancy are separate raw evidence; neither is relabeled as
process peak memory. Batch startup/installation time is included.

Passing proves fixture consistency and evidence plumbing only. Inspect raw
Skycache metrics for reads, hits and publication; successful builds do not
imply useful cache hits or speedup. This fixture and its default observation
count cannot establish latency tails. Larger experiments must preregister
workloads, attribution, resource and promotion gates, account for complete
producer/consumer sequences, and retain failures.

The harness reuses retention/chain conventions: exact runtime and input
identity, raw monotonic timing, separate producer/publication accounting,
independent cache populations and complete output checks. It omits their
workload-specific paths, external services, agent overlays and cleanup commands.

## Cost and ownership extraction

After a complete passing cohort, extract existing telemetry without another
Bazel run:

```sh
python3 docs/jsgette/measurements/skycache-unified-analysis-2026-10-28/summarize_costs.py \
  --run-dir /absolute/path/to/completed-run \
  --out-dir /absolute/path/to/new-cost-report
```

The report writes `phase-costs.csv`, `ownership.csv` and `interpretation.json`.
It requires successful output validation and verifies checksums for consumed
metrics, BEPs, profiles and diagnostics. Failed/incomplete cohorts remain raw
evidence and are rejected by this extractor.

Retrieval/service histograms become conservative bounds on summed latency
samples. They include overlap and waiting; they are not exclusive CPU time.
JSON profile categories expose both the sum of observed spans (including
nesting) and their wall-interval union within that category. Categories can
still overlap and profiles may omit short events. Whole-command child rusage,
BEP phase timings and heap metrics remain separately labeled. Absent telemetry
is unavailable, never silently zero.

`local-skycache-phases.json` covers finalization after Bazel has stopped its JSON
trace. Six fixed scalar slots measure publication, final input validation,
witness/manifest encoding, sealing, local installation and store cleanup.
Collection is enabled only if the Bazel profiler was active when the session
was constructed. Fully disabling collection requires both trace profiling and
slow-operation profiling to be disabled. The disabled path reads no phase clock
and allocates no per-session timing array. Unexecuted phases are null.
Publication includes its subphases and remote-parent publication, excludes its
lifecycle-lock wait and cleanup, and excludes the module's later remote export.
These are elapsed wall times, not exclusive phase costs or CPU attribution.

## Unit tests

```sh
python3 -m unittest discover \
  -s docs/jsgette/measurements/skycache-unified-analysis-2026-10-28 \
  -p test_paired_baseline.py -v
```

Tests cover order, recursive BEP validation, corrupt/incomplete evidence, path
escape, changed inputs, source mismatch, existing-run protection, command
failure, timeout and arm isolation. They do not invoke Bazel.
`provider-identity/` belongs to P02 and is not read or written by this harness.
