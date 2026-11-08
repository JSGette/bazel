# First real Java trace/replay qualification

Prepared before execution. This is a functional interoperability check, not M1's savings-ceiling or economic gate. No command is authorized to run from an unfilled example, and no synthetic Python trace can satisfy this qualification. Run only after the new candidate's source build and focused Java tests pass and their owned guard scope is absent without forced cleanup.

Use the existing `run_auxiliary.ps1` guard: six CPU quota, 10,240 MiB `MemoryMax`, no swap, and its unchanged physical/commit reserves and bounded headroom preflight. The harness additionally verifies these cgroup limits. The fresh, exclusive root is `/mnt/shared-evaluation-20261107/m1-trace-acceptance-v1`; all outputs, private bases, repository/SkyCache data, JVM/action temporary files, Git worktrees, home, and XDG directories are beneath it. Both engines use the same qualified binary, private `-Xmx768m` JVMs and `ActiveProcessorCount=2`. The harness never retries or reuses an old root.

## Fixed cases

The fixture is a small Starlark macro calling `native.genrule`. It concatenates sorted `inputs/*.txt` and `optional/*.txt` glob results with explicit `allow_empty = True`, followed by `link.txt`; A intentionally has no optional glob match. Two detached worktrees share their freshly created fixture Git common directory, task-owned SkyCache/repository cache, install base and controlled environment directories. Their source worktrees, output bases and daemon identities are distinct. The trace destination and supplied context are outside the source workspaces.

| Order | Engine | Fixture | Trace | Independent expected output |
| --- | --- | --- | --- | --- |
| 1 | e0 | A | off | `alpha\nomega\nalpha\n` |
| 2 | e0 | A | on | A bytes |
| 3 | e1 | A, fresh engine | on | A bytes |
| 4 | e0 | B | off | `bravo\nomega\nextra\nomega\n` |
| 5 | e0 | B | on | B bytes |
| 6 | e1 | A while e0 is B | off | A bytes |
| 7 | e0 | A restored | on | A bytes |
| 8 | e1 | A repeated | on | A bytes |

A has `inputs/a.txt=alpha`, `inputs/z.txt=omega`, absent `optional/added.txt`, and `link.txt -> inputs/a.txt`. B changes `alpha` to same-size `bravo`, adds `optional/added.txt=extra`, and retargets the symlink to `inputs/z.txt`; all text files end in a newline. Returning to A removes that one optional fixture file and restores the exact content/symlink manifest. These are the only source mutations, all within the fresh owned worktrees. Each command records a manifest of declared file bytes, symlink target strings, and explicit real-directory records for `inputs/` and `optional/`; its SHA is the snapshot identity, including empty-directory existence. Missing or symlinked fixture directories fail verification. No source bytes or ambient environment outside the fixture are collected.

Every build must have return code zero, a single successful BEP command UUID, and exact output bytes matching the independent oracle. BEP `SUCCESS` may omit the proto-default numeric zero; an explicit nonzero still fails. Both worktrees' declared source bytes, symlink targets, and glob membership are checked before the next mutation, after every build, and after shutdown. All trace-disabled commands must leave prior trace/context/receipt bytes intact and create no trace artifacts. Same-engine daemon PID/start ticks must remain stable and private engines must have distinct positive PIDs. Shutdown targets only positively identified processes in the current guard scope with the exact private output-base argument; every recorded daemon identity must be absent afterward. The outer guard subsequently verifies final scope absence.

## Real Java wire proof

Five `--experimental_m1_trace` commands use distinct absolute outputs and distinct `--experimental_m1_trace_context` files. Context contains only the module's exact allowlisted fields. It binds the actual qualified binary, full instrumented source tree and literal combined base-to-instrumented patch, logical repository identity and actual physical worktree, SHA-256 filesystem policy, fixture snapshot, engine/cohort, and effective literal argument-list SHA. The argument-list hash includes the context path but never the context bytes, avoiding a self-reference.

The actual Java-produced trace must pass the frozen `replay.read_trace` validator, including bounds, record counts, dependency references, CPU nesting, exact footer digest, and the real sibling `.completion.json`. The module's command UUID must equal the BEP UUID. The supplied raw context SHA and every context field must equal the trace declaration. The receipt must name the exact original output path. Earlier artifacts must stay byte-identical through later commands.

At least one exact directory or directory-listing projection must belong to a node with an actual event/interval; the first traced build in e1 starts a fresh graph to exercise observation. Every emitted exact listing for `inputs/` or `optional/` is checked against that command's independently declared sorted names/types, including B's addition and restored A's absence. Missing listing projections are explicitly reported as coverage gaps, not fabricated. Count and report only projection classes actually emitted. In particular, regular-file metadata/proxy states may be unsupported on this filesystem; no digest-backed subtype, symlink/missing projection count, CPU availability, cache hit, or equivalence hit is required or invented. Output behavior exercises the changes independently of trace projection coverage.

After all eight builds, invoke the frozen replay CLI on the five actual Java trace paths and a newly written attestation document. The exact attestation is `{ "traces": { TRACE_SHA: { "context": SUPPLIED_CONTEXT, "context_sha256": RAW_CONTEXT_SHA, "completion_sha256": RAW_JAVA_RECEIPT_SHA, "trace_path": ORIGINAL_ABSOLUTE_TRACE_PATH } } }`. All five replay results must be context-attested. Unknown facts/CPU remain unknown; replay output receives no milestone verdict. The existing 22 collector and seven module tests cover pure callback, overflow, and failure edge cases and are not repeated as live scenarios.

## Freeze and literal launch

After successful build qualification is archived, write a new C-backed `work/trace-smoke-manifest.json` with these exact required fields (all hashes filled from actual files; no placeholders):

- `schema_version: 1`, `root` equal to the fixed root above, `cases` equal to the literal eight rows in `qualify_skycache_trace.CASES`.
- `harness_sha256`, `replay_sha256`, `contract_sha256` pin the current helper and the two files in `work/m1-trace-design`.
- `build_report` and `candidate_verification`: each `{ "path": ABSOLUTE_WSL_C_PATH, "expected": ACTUAL_SHA256 }`, naming archived C-backed qualification evidence.
- `source_identity`: `{ "base_commit": "a23ae68903f03f08ce83dcf1be85cdff54bb1eeb", "patch_sha256": BUILD_REPORT.bundle.combined_patch.sha256, "reconstructed_git_tree": BUILD_REPORT.bundle.instrumented_git_tree }`.

The pinned build report must prove successful `version`, `build`, `tests`, and `shutdown`, unchanged verified source, and identical candidate hashes after build/tests. Its separately archived candidate proof must bind the exact report hash, candidate path/hash/bytes, successful qualification, and an absent unsignalled final scope. The harness rehashes the live candidate before and after its commands; it does not use the native candidate or an unqualified replacement.

Once these values exist, create a new literal JSON argument array for the existing wrapper: `python3`, `-B`, the absolute WSL C-path to `work/qualify_skycache_trace.py`, `--manifest`, the absolute WSL C-path to that new manifest, `--manifest-sha256`, and the actual manifest SHA. Preserve this command as `work/skycache-trace-smoke-command.json`. Invoke `run_auxiliary.ps1` with that file, run ID `m1-trace-acceptance-01`, timeout 1800 seconds. This document deliberately contains no runnable placeholder command. Root owns the final freeze/launch after qualification.

Archive the bounded report, manifest, literal command, guard/control evidence, declared fixture snapshots, exact outputs, BEPs, logs, five Java traces and receipts/contexts, binding documents, replay attestations/result, and helper hashes. Preserve failures before any repair. Passing proves this candidate's real trace/replay interoperability and fixture isolation only; the Datadog trace and measured overhead/coverage experiment follow separately.
