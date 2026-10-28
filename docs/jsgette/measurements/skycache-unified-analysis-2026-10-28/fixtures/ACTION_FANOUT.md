# Action-owner graph registration fixture

Generate a new workspace outside Git, then use the existing paired harness. The
default is eight partitions, 64 writer rules per partition, and eight independent
`ctx.actions.write` actions per writer: **4096 actions and output files**, 512
writer rules, and eight roots. Root rules forward their writers' files without
adding actions. No compiler, shell action, external toolchain, or provider-object
depset element is used. Repository/module initialization remains Bazel work.

```sh
python3 fixtures/generate_action_fanout.py --size default --output "$EVIDENCE/actions4096"
python3 -m unittest discover -s fixtures -p 'test_action_fanout.py' -v
```

`quick` has 128 actions (4 × 8 × 4); `large` has 16384 (8 × 128 × 16). Generation
refuses to overwrite any existing directory. `topology.json` records exact shape,
action/output counts, source hashes, and cohort targets. The harness freezes and
hashes all generated inputs and checks every default output's exact SHA-256.

| Manifest | Default requested targets | Exact output files | Purpose |
| --- | ---: | ---: | --- |
| `broad.json` | 8 roots | 4096 | Action registration across disjoint writer sets |
| `sparse.json` | First root | 512 | Sparse demand; optionally seeded by broad producer |
| `overlapped.json` | 8 roots + all 512 writers | 4096 | Writers are both top-level requests and dependencies; no extra actions or outputs |

For each cohort, pass `--manifest "$EVIDENCE/actions4096/<cohort>.json"` with
the existing harness's required binary/source/run arguments. For broad-seeded
sparse demand, also pass `--producer-manifest "$EVIDENCE/actions4096/broad.json"`.
Omit that argument for an overlapped producer and overlapped consumers; add it
only when deliberately testing overlapped demand from a broad seed. These are
distinct experiments and must retain their recorded producer manifests.

The broad and overlapped output maps are exactly equal; requesting writers twice
through different graph paths is the intended distinction. This tests repeated
action-owner graph discovery/registration. It does not assert that Bazel creates
or executes duplicate actions. Keep BEP action/ALV counts and profiler scopes so
any change can be tied to observed traversal work.

## Separate semantic cases

The generated `semantics` package supplies tiny two-owner cases. `shared.json`
is a success manifest requesting both owners of the same output with equal
content and executable bit. Its exact oracle expects one physical output path.
It can use the existing success-only harness independently of the large cohorts.

The dedicated correctness runner exercises four cases cold, with a restored left
owner and local right owner, then with both owners restored from separately
produced publications. It uses 14 commands: 12 case checks and two producers.
Each producer selects four owners with distinct output paths. Each consumer gets
an independent seed copy, fresh batch JVM and fresh output base. Use a new run
directory outside the generated workspace:

```sh
python3 fixtures/run_action_semantics.py --bazel "$BAZEL" \
  --workspace "$EVIDENCE/actions4096" --run-dir "$EVIDENCE/action-semantics-02"
python3 -m unittest discover -s fixtures -p 'test_action_semantics.py' -v
```

The runner reuses the sibling paired harness's exact flags, process worker and
successful-output validation. It records binary, helper, harness and fixture
hashes; preserves raw commands/BEP/diagnostics; and refuses to overwrite runs.
It requires complete BEP terminal evidence and exact configured-target cache-hit
counts of zero, one or two for the intended matrix paths. Those are aggregate
counts combined with deliberately selected seed targets, not per-label hit
instrumentation. Shared success additionally requires exactly **one executed
FileWrite action** in BEP, and each producer requires four; equal final bytes
alone would not demonstrate shared execution.

Final file modes are recorded, not compared with `is_executable`. Bazel's output
metadata store normalizes regular outputs to `0555` by default (`0755` when
`experimental_writable_outputs` is enabled). Thus an action's false executable
flag can produce a final executable file. The action key still includes that
flag, so the executable-bit mismatch remains an action-conflict check. The first
external runner incorrectly compared these two different contracts; its five
raw commands and original helper snapshot remain in `actions-semantics-01`.
The corrected runner must use a new directory; no result from that failed run is
rewritten as a pass.

`semantic-cases.json` describes three expected failures: same output with
different content, same content/output with different executable bit, and an
output file whose path is a prefix of another output. Run those target pairs in
separate native correctness checks with cache off and cache on as appropriate;
require nonzero exit plus the corresponding structured failure or conflict
diagnostic. Do not pass failure cases to the success-only harness or interpret
any nonzero exit as sufficient proof. They are generated expectations until
native runs verify them. Do not use `//...`, which would mix intentional failures
into the successful performance workloads.

The cases reflect `Actions.canBeShared`: equal mnemonic/key plus compatible
input/output artifacts can share. `FileWriteAction.computeKey` includes content
and the executable bit. Hash equality alone does not verify action-key equality,
executable mode, restored wrapper identity, or general sharing safety. The mode
mismatch negative case specifically checks that equal output bytes are not the
only conflict criterion.

## Cost interpretation

The retained-provider producer profiles had only eight FileWrite actions and
4110 action-lookup values. Their `ALV collection` scope includes waiting for an
exclusive lock; `Go through actions` waits for asynchronous worker futures.
Neither is exclusive action-deduplication CPU. Preserve these scope meanings when
comparing this fixture, especially the overlapped cohort. Registration may run
during collection, and summed parallel durations are not elapsed wall time.

This default has 520 fixture rule targets, so it is not an ALV-count-matched
comparison with the 4096-node provider DAG. File writing, artifact materialization,
BEP size, and validation work also grow with output count. Report action counts,
traversal/registration evidence, command timing, and memory separately. A synthetic
win here cannot establish a full-build speedup or producer-memory reduction.
