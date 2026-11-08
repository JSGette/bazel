# Cross-worktree SkyCache admission audit — 2026-11-08

**Finding:** the inspected identical-worktree preparations are cold at publication admission, despite sharing a namespace. The evidence does not show a root-key identity failure. A conservative global input-witness check rejects prior publications before their individual analysis roots can be considered. The exact failing input is not recorded, so neither `go.env` nor a repository symlink can yet be named as the cause. No milestone verdict or running-protocol change follows from this audit.

## Observed evidence

Read only the published [N10 SkyCache restart archive](evidence/m0-v7b-01/i10-sky-r-run.tar.gz), its manifest, `report.json`, and preparation/measured `c00`, `c01`, `c09` command and SkyCache sidecars. No runtime cache, witness block, repository tree or output-base scan was performed.

- Archive SHA-256 rechecked against its manifest: `7e87b0e547bc9ea95d358295c6d2ff0d8e100f18081b99fbfb81d6cc8fa66591`.
- Archived report SHA-256: `c8dbfe3ae7076a5c3ddf97a9db626a00526d77f09aba42fd5955630513804a37`; binary SHA-256: `11547ce20ac9edcaa14c48206ac14618012d898ef24d4bbf6e0ac229b44b1e25`.
- The report records serial build-then-shutdown preparation, all ten base variants succeeding, and the same SkyCache directory throughout. Publication inventory changes from zero to ten, in **one** namespace, before the measured restart.
- The three inspected clients use different `w00/w01/w09` workspaces and `o00/o01/o09` output bases. Their target, compilation mode, common cache directory and explicit repository environment agree. No immutable-workspace override is present.

| Archived observation | Prep c00 | Prep c01 | Prep c09 | Restart c00/c01/c09 |
|---|---:|---:|---:|---:|
| `restore.roots` | 0 | 0 | 0 | 3,272 each |
| `before_finish.accepted_root_entries` | 0 | 0 | 0 | 3,272 each |
| `peak_candidate_count` | 0 | 0 | 0 | 1 each |
| `validation.records_read` | 0 | 0 | 0 | 34,369 each |
| `before_finish.admitted_witness_entries` | 0 | 35 | 35 | 33,938 each |
| `before_finish.rejected_input_entries` | 0 | 1 | 1 | 1 each |
| `before_finish.rejected_witness_entries` | 0 | 1 | 9 | 9 each |
| `rejected_publications` | 0 | 10 | 81 | 45 / 63 / 36 |
| `published` | true | true | true | false each |

All six inspected invocations have zero read-limit, scan-limit and catalog-busy skips. Preparation `c01/c09` has zero CAS get requests and zero validated root records, so there is no evidence of reaching root deserialization and then rejecting roots. Publication rejection counts include repeated attempts; 81 is not 81 distinct publications. The other seven preparation sidecars were not independently reread in this bounded audit.

The restart reuses each client's prepared output-base state. One admitted candidate and 3,272 accepted roots per client establish restored analysis, but **do not establish cross-worktree transfer**. Candidate origin IDs are absent from these sidecars; the evidence is consistent with each client accepting its own earlier publication while rejecting the others.

## Source mechanism, distinct from the measured rejection reason

Read the preserved source under `/home/jgett/skycache-20261029/bazel/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/`. HEAD is `a23ae68903f03f08ce83dcf1be85cdff54bb1eeb`; the first four files below are modified relative to HEAD. These are working-file identities, not a claim that HEAD alone reproduces the measured binary. The binary and preserved patch provenance remain governed by [D004](decision-log.md).

| Source file | SHA-256 of bytes inspected |
|---|---|
| `LocalAnalysisCacheClient.java` | `1e1954e0477d38f00b7f460232d2fbccd8a79a236567beb9c949dfbe1317fdd4` |
| `LocalCacheSession.java` | `f4a6ae49f2715e0b0b5aaf358d2c7f2f6bdc61e800dd9a73d5420b0937a6c777` |
| `LocalInputSnapshot.java` | `62b22894ffaa37db290f42f22c27693e4f829816a4ea33998770ebf9c356d8b5` |
| `LocalSkycacheModule.java` | `b8c3fdedbd6d75c79005b2be04dfba4e8c0cfc1cb0656459727aec148e0a806f` |
| `LocalPathCodec.java` | `b1abd484fd1c4f3abac4b979999d3a2afbabeaf8067908947368d1f23c186a84` |
| `LocalSkycacheOptions.java` | `e55900cca06383733f672d37c6e8262cb7765fb0634af0a79ac4366d797c8069` |
| `LocalWitnessTable.java` | `bedf45b0b1c2448e2cd4625fdf77fbe5f732d6aed2605cc75219b4ce0377f95d` |

1. **Namespace intentionally permits sibling Git worktrees.** `LocalInputSnapshot.captureNamespace():140–182` hashes format/endian, Git **common directory** and effective environment. It does not hash each worktree root when Git metadata resolves to the same `commondir`. Independent clones with different common directories are separated; a directory without Git metadata is separated by its real workspace path. `LocalSkycacheModule.inputEnvironment():215–236` includes effective repository/action/test environments and remote execution namespace. The single archived namespace and actual rejected publications rule out separate per-worktree namespaces as the explanation here.
2. **Publication-wide guards precede individual keys.** `LocalInputSnapshot.globalWitnesses():114–116` keeps every non-`workspace/` witness. `LocalCacheSession:297–300` selects that namespace's publication directory; `refreshCandidates()/admitClosure():858–1073` admits the entire publication and parent closure only after global witnesses match. One mismatch rejects the candidate even if some roots would not depend on that input.
3. **External binding checks run first.** `LocalCacheSession.matchesForAdmission():1077–1103` first checks top-level `external/<repository>` witnesses, then all other global witnesses. `rejectWitness():1106–1113` retains one failed witness per publication and a map keyed by resolved input path. Thus `c09`'s nine failed-witness entries with one rejected input path are consistent with nine prior publications failing on the same current path. The sidecars do not expose that path, expected/actual fingerprints, operation kind or rejection sequence.
4. **Lookup cannot overcome failed admission.** `LocalCacheSession.nextAnalysisCandidate():694–747` searches admitted candidates; `LocalAnalysisCacheClient.lookup()` accepts only type-4 analysis envelopes and validates their dependency root before remembering it. Zero candidates and zero root records locate the observed barrier before this validation. They cannot establish whether all cross-worktree root keys would match after admission.
5. **Logical paths exist, but physical bindings still matter.** `LocalInputSnapshot:502–533` resolves `workspace/` and `external/` against the current worktree/output base; other paths remain absolute. Its symlink capture also records resolved referents (`447–488`). Fingerprinting hashes regular-file bytes and permissions; symlink targets within the current workspace/output base are normalized, other targets retain their spelling (`642–671`). `LocalPathCodec` also rebinds serialized workspace/output-base paths. These mechanisms permit portability in some cases; they are not a proof that arbitrary path normalization is semantically sound.

Consequently, identical checked-in files do not guarantee reusable publications. Missing/generated repository state, changed repository bindings, environment differences, or path-bearing generated files can conservatively prevent reuse. A single global mismatch can make all roots effectively cold for a new worktree. This is a source-supported mechanism and an observed admission failure, **not proof that every identical new worktree must be cold by construction**. The specific `go.env` coupling in [the bootstrap note](m0-repository-bootstrap-notes.md) is a candidate explanation only; this archive contains no failed-witness identity or digest diff to connect the two.

## Smallest existing-binary diagnostic after M0

No witness-path/rejection-detail option was found in `LocalSkycacheOptions`. `LocalSkycacheModule:330–356` emits the same aggregate statistics already archived; `LocalCacheSession.rejectedWitnesses` remains private and its entries are not logged. Existing logs therefore cannot identify the failing path. No new product instrumentation or relaxation of validation is proposed here.

The existing **immutable metadata** is the smallest next source of evidence. After M0 and outside any measured window, capture a bounded list of the already-inventoried ten manifests under `leased-v2/publications-v4/<namespace>/`; their archived total is 7,100 bytes. Their IDs locate `leased-v2/objects/segmented-v1/<id>/witnesses-NNNNN.block`. Select the minimum publication pair needed, copy only manifests and their referenced witness blocks into evidence, and record source paths/hashes. Do not copy value packs/indexes or scan arbitrary runtime trees. Bound expanded metadata to 32 MiB per selected publication; stop and reassess if exceeded. Never alter publication files or delete/rebind repositories to provoke a hit.

The preparatory [offline decoder](harness/decode_skycache_witnesses.py) and its 17 tests are implemented and reviewed; no actual publication has been decoded yet. Manifest-only mode returns bounded descriptors and exact object-relative filenames without opening blocks. An explicit copied block directory and optional block indices enable witness decoding. It records exact copied-file hashes and inspected source identities, marks partial selections, and never treats unopened parents as validated closure. In-band checksums establish consistency, not independent authenticity. Its canonical-writer checks and 32 MiB expansion budget are intentionally stricter than parts of the Java reader; rejection by this tool alone is not evidence of corruption. The expansion budget is not a Python RSS limit.

A source check also distinguishes retention policies. `experimental_analysis_cache_idle_retention=3600s` resets in-memory Skyframe/loading state (`LocalSkycacheOptions:114–123`, `AnalysisCacheIdleRetentionTask:47–76`), while retaining JVM, action cache and outputs; it does not specify one-hour publication deletion. The separate disk collector defaults are 5G maximum size, seven-day publication age and a 30-second idle delay. `LocalCacheSession:535–625` keeps live closures and then eligible recent publications within that budget before deletion. The archived identical N10 store totals 336,194,074 bytes, ten publications and one namespace without inventory truncation. Elapsed time alone therefore gives no evidence-based reason to capture during the running matrix. This does not guarantee retention under other writers, later budget growth or corruption; runtime state has not been inspected.

`LocalCacheSession.readManifestHeader():1308–1357` verifies the trailing SHA-256 and format/namespace/parent/block descriptors. `LocalWitnessTable:183–296` verifies per-block hashes, bounded raw-deflate expansion and path-prefix decoding; records contain exact logical path, listing bit and digest (or `missing`/`not-directory`). A read-only offline decoder can compare those records and identify concrete conflicting bindings/files. It must reproduce the validated format and reject malformed/over-budget data. The run archive contains no such blocks or publication IDs, so this step has **not** been performed. The header's source digest is validated syntactically, not compared for candidate admission; roots rely on their dependency proofs.

A difference between two final publication tables establishes an exact recorded input difference, **not the original first rejection at lookup time**. A small read-only check against the reader's stabilized inputs can establish a sufficient admission mismatch using the source's path/fingerprint rules. Label that result as reconstructed admission evidence unless an actual invocation exposes its chosen failed witness; initial missing repository materialization and later stable differences can produce different first failures.

Only if that distinction changes the architectural decision, use a new tiny N2 diagnostic:

1. Create two fresh sibling worktrees/output bases with one common namespace and the same pinned existing binary, flags, environment and guard. Let A publish to an initially empty diagnostic store, then shut it down and capture its immutable metadata. B's first build can see **only A's existing publication**; B has no earlier publication of its own. Record whether B admits any candidate/restores any root and archive both final witness tables plus correctness outputs. Single-publisher availability establishes cross-worktree provenance without adding binary instrumentation.
2. If B rejects A, inspect the exact metadata difference before changing anything. Charge repository setup and cold-request cost separately. A prepared-repository retry must use a separate diagnostic store containing only the captured A publication and its complete immutable closure, so B cannot quietly hit its own previous output. Require output digest/behavior checks and a changed-source negative control. N1 off/on diagnostics and same-output-base restarts cannot test this transfer.
3. If the rejected witness is a location-dependent repository binding or generated file, test a shared immutable repository result identified by tree content plus semantic configuration and stable logical execution paths. Keep scratch state private. Require changed SDK/repository content/environment/symlink-binding controls; removing paths only from hashes is not an implementation contract.
4. If unrelated global witnesses block otherwise reusable roots, investigate narrower dependency-scoped admission. Preserve transitive dependency evidence and publication lifetime guarantees; do not simply remove global guards. Measure successful cross-worktree roots, total request CPU/latency and validation cost before considering broader evaluator changes.

Decision recorded by this audit: treat cross-worktree publication admission as an explicit unproven capability, separate from persistence across a daemon restart. No product source, runtime cache, benchmark configuration or gate result was changed.
