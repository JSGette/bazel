# Bounded analysis DAG workloads

`generate_analysis_dag.py` writes a deterministic standalone Bazel workspace.
Generate it in the external evidence directory, not in Git or in an already
measured workspace. The destination must be new and its parent must exist.

```sh
python3 docs/jsgette/measurements/skycache-unified-analysis-2026-10-28/fixtures/generate_analysis_dag.py \
  --size default --output /absolute/evidence/dag4096
```

The three fixed sizes are:

| Size | Partitions × layers × width | Node rules | Broad / sparse / disjoint node demand |
|---|---|---:|---|
| quick | 4 × 8 × 16 | 512 | 512 / 128 / 256 |
| default | 8 × 16 × 32 | 4,096 | 4,096 / 512 / 1,024 |
| large | 8 × 32 × 64 | 16,384 | 16,384 / 2,048 / 4,096 |

The default is a **root/metadata and producer-cost** cohort: roots expose only
`DefaultInfo`, so a root cache hit can restore its small action/output state
without hydrating the 4,096-node provider payload graph. The constructed payload
count therefore does not describe restored payload size in this mode.

For a separate, bounded retained-provider cohort, generate a different workspace:

```sh
python3 docs/jsgette/measurements/skycache-unified-analysis-2026-10-28/fixtures/generate_analysis_dag.py \
  --size default --retain-provider-graph --output /absolute/evidence/dag4096-retained
```

This flag adds explicit child-provider tuples to each node and exports a root
`NodeInfo` containing the transitive string-ID depset and its final-layer child
providers. The root's encoded value can then retain the shared provider DAG,
including its string payloads, through ordinary codec fields. Provider objects
are never used as depset leaves. Inspect actual CAS/decode metrics to establish
that hydration occurred; the flag itself is not a measured decode-cost claim.
Existing generated workspaces are never edited. The default remains disabled.

These are declared fixture-rule counts, not measured total Skyframe nodes.
Each partition adds one root rule. Each noninitial layer node depends on four
distinct nodes in the preceding layer, wrapping column indices; each root
depends on the entire final layer. The default has 15,360 node dependency edges
and 256 root edges. Partitions share no node-rule edges, but share BUILD/package
loading, the rule implementation and Bazel infrastructure. The single BUILD
macro declares all targets even for sparse demand: sparse does not eliminate
loading cost.

Every node produces a Starlark provider with a bounded arithmetic checksum,
32 deterministic string payload fields, a scalar payload count, and a
transitive depset whose direct elements are stable node-ID **strings**. Each
payload has a 128-character body plus an identifying prefix. Parents consume
payloads and checksums without repeatedly flattening transitive depsets. Only
root rules flatten their closures and write output files with `ctx.actions.write`.
There are no shell actions, compiler toolchains or external rule dependencies.
Usual Bazel bootstrap/module-resolution work may still occur and must be
accounted for separately from analysis, restore and publication time.

This representation exercises shared analysis state and serialized provider
payloads while avoiding provider/depset-wrapper identity as a set element.
It is a deliberately limited correctness cohort, not proof that arbitrary
mixed restoration of Java providers is sound. A native run still must pass
the exact output oracle.

## Exact outputs and topology evidence

Each root emits its partition, unique reachable-node count, direct input count,
checksum, final-layer payload character count, and the complete sorted node-ID
list. The full list prevents count-only validation from accepting a different
reachable set. The Python oracle independently enumerates graph reachability
and computes the scalar recurrence; expected file digests are generated before
Bazel executes. A hand-calculated small graph anchors the oracle tests.

The retained-provider flag leaves these exact target/output manifests and output
hashes unchanged. `BUILD.bazel` and `topology.json` differ and are included in
the harness's frozen input identity. `topology.json` explicitly records
`retain_provider_graph` and the restoration scope. The output oracle does not
independently walk the restored child-provider objects or prove alias identity;
it checks the root outputs. This workload is not an arbitrary-provider identity
test or proof of every partial-restoration combination.

The generated workspace includes:

- `broad.json`: all partition roots.
- `sparse.json`: only partition 0's root.
- `disjoint.json`: partition 0 and the final partition's roots.
- `topology.json`: shape, edge/demand counts, payload character counts,
  generator/rule hashes and every expected output digest.
- `MODULE.bazel`, `BUILD.bazel`, `analysis_dag.bzl`: complete deterministic inputs.

Payload character counts describe constructed data only. They are not measured
allocated or retained heap bytes. All manifests inhabit the same directory,
so the harness freezes and hashes one input inventory regardless of which
producer/consumer cohort is selected.

## Harness usage

Supply the standard binary, source, new run-directory and pair arguments from
the parent README. Add one of these selections:

```sh
# Broad producer and broad measured demand.
--manifest /absolute/evidence/dag4096/broad.json

# Broad producer; sparse local-only and cache-on measured demand.
--manifest /absolute/evidence/dag4096/sparse.json \
--producer-manifest /absolute/evidence/dag4096/broad.json

# Broad producer; two disjoint partitions measured in both arms.
--manifest /absolute/evidence/dag4096/disjoint.json \
--producer-manifest /absolute/evidence/dag4096/broad.json
```

The producer uses its own targets and exact output oracle. Both measured arms
use the consumer's targets and exact output oracle. Each cache-on arm receives
an independent copy of that producer's seed; cache-off receives no Skycache.
Producer time is recorded separately from paired estimates. Omitting
`--producer-manifest` preserves the original same-cohort producer behavior.

Start with default size. `large` is an explicitly selected stress cohort, not
an automatically executed scaling claim. Do not infer cache benefit, heap
savings, cold-storage behavior or latency tails from topology or passing tests.
Inspect phase costs, hit/miss counts, decoded bytes, publication and ownership
diagnostics alongside complete-command results. In particular, startup/module
resolution may dominate these short invocations; the topology does not remove
that cost.

## Tests without Bazel

```sh
python3 -m unittest discover \
  -s docs/jsgette/measurements/skycache-unified-analysis-2026-10-28/fixtures \
  -p test_analysis_dag.py -v
```

Tests cover exact cohort sizes/edges, disjointness and full reachability,
hand-calculated outputs, observable payload changes, byte reproducibility,
no-overwrite behavior and bounded shapes. Native cache-off/cache-on execution
and exact output comparison are performed by the parent harness, not these
generator tests.
