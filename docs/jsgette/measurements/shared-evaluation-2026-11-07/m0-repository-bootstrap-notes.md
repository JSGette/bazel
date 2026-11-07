# Repository bootstrap as a separate sharing experiment

Read-only audit, 2026-11-08, fork revision `236a4d0b31573e125876c7b76da259a383698073`; Bonanza revision `bf9368403b95540ad07e0fd33faf5210b9d8b86b`. This supplements [Bonanza contracts](bonanza-contract-notes.md) and [cost attribution](m0-cost-attribution-notes.md). It proposes a smaller experiment after M0; it is not a milestone verdict or a change to the current benchmark.

## What the evidence establishes

- D014 records 36 Gazelle-tool contents-cache candidates under one predeclared-input bucket, with binary file lengths totaling **774,016,416 bytes**; three completed source clients each had a **21,500,456-byte** binary. These are coordinator observations recorded in the [decision log](decision-log.md), not a fresh runtime inspection by this audit. Equal file lengths do not establish equal hashes, duplicate physical extents or reclaimable RAM.
- D015 records **8,371,748,864 bytes of cgroup-charged file memory** at the last c06 preparation sample, after seven successful serial clients. It includes more than the Gazelle binaries: attribution to individual files or exact duplicated content was not collected. Cgroup file charge is neither evaluator RAM nor a deduplication opportunity measured in bytes.
- Per-pool `GOCACHE`/`GOPATH` paths and repository symlink destinations were previously observed. No paired `go.env`/`.recorded_inputs` capture or actual candidate-rejection reason was retained. **Path coupling is observed; its causal role in a cache miss is still a hypothesis.**
- The archived v7 preparation profiles do establish repeated execution of the Gazelle tools repository function. Selected successful preparation rows are below; the cohort later aborted before its measured phase.

| Client | Initial preparation wall (s) | Gazelle tools repository-function elapsed (s) | Scope CPU (s) | Sampled Bazel JVM CPU (s) |
| --- | ---: | ---: | ---: | ---: |
| c00 | 45.271 | 10.276 | 61.956 | 32.09 |
| c01 | 24.761 | 7.842 | 53.173 | 25.41 |
| c06 | 25.272 | 6.603 | 53.453 | 25.43 |

The repository duration is one `Starlark repository function call` event per row, not a sum of nested fetch/function/execute spans. It is elapsed time, not process CPU, and it can overlap other work. Scope CPU includes clients, JVMs and subprocesses; JVM sampling can miss final ticks. The difference cannot be assigned entirely to Go compilation. These diagnostics justify measuring bootstrap separately; they do **not** establish that it dominates newcomer cost.

Source: `evidence/m0-v7-01/source-ref-run.tar.gz`, `preparation/c{00,01,06}/{result.json,profile.json.gz}` and `preparation-c{00,01,06}-resources.json`. This audit rechecked archive SHA-256 `db94485f7824a7bb459641335af856e1c7c4b73860720ca15be339ffb470ba94` against the recorded manifest. The prepared/restarted benchmark excludes this serial initial setup; its measured CPU attribution must remain separate.

## What the implementation actually keys and shares

1. **Bucket identity and candidate identity are different.** `DigestWriter.computePredeclaredInputHash` hashes format/semantics, repository rule identity and transitive Starlark digest, canonical repository name, attributes, host OS/architecture, declared environment and recorded repository mappings. There is no general output-base field, although attributes or environment values may contain physical paths. `LocalRepoContentsCache` places random UUID candidates inside that bucket and validates each candidate's recorded inputs. A distinct UUID is not evidence of a distinct semantic key or output tree. GC deduplicates identical recorded-input file bytes, not output-tree content. [Declared inputs][declared], [cache layout/publication/GC][cache].

2. **Observation paths are already partially portable.** `RepoCacheFriendlyPath` represents files inside repositories as repository name plus relative path; outside paths remain absolute. File observations compare content digests. Thus a *hypothetically observed* `go.env` whose bytes embed different output-base paths could invalidate a candidate even when the observation's own path is portable. We have not proved that this is the rejected observation here. Stripping paths from keys or ignoring changed file bytes would erase real dependencies. [Recorded inputs][inputs].

3. **Reusable repository outputs already exist in this fork.** Fetch first validates the current repository, then local candidates, then the remote repository contents cache. Publication requires a non-local rule declaring reproducibility plus eligible symlinks; marker publication follows successful fetch. There are other explanations for refetching, including changed observations, forced fetching and eligibility/policy differences. Diagnose those before inventing another repository cache. [Fetch and publication][fetch].

4. **Local repository commands have a distinct execution route.** `repository_ctx.execute` builds a `StarlarkExecutionResult` process directly; it does not pass through ordinary build-action spawn caching. The fork also already has an experimental remote repository-execution route for eligible remotable rules, passing explicit label inputs, arguments, environment, execution properties and working directory. A warm ordinary action cache therefore does not imply a warm repository bootstrap. [Local/remote execution][execute].

5. **Native/private policy matters.** The native comparator forces separate repository contents caches per engine to avoid same-JVM file-lock conflicts; private processes can use a shared cache. A bootstrap experiment must control this policy explicitly and report it separately from evaluator sharing. See [M1 native preflight](m1-native-preflight.md).

## Bonanza's useful contract

Bonanza's repository execution constructs a command including arguments, merged explicit/repository-platform environment, working directory and a stable-input-root identifier; captures the current input tree; and requests an `ActionResult` dependency containing the action reference, platform public key and timeout. Returned output directories update the logical repository tree. Missing results suspend/restart the computation through the evaluator. The stable input-root query explicitly exists because repository APIs expose absolute paths. [Command/input/result construction][bonanza-repo], [stable-path contract][bonanza-path].

This provides a concrete model for **repository execution as a query over explicit inputs with an immutable result**, without treating a developer's output-base path as its identity. It does not prove perfect hermeticity, automatic equivalence of different physical paths, cross-request in-flight deduplication, or measured worktree scale. Its command requests writable input files during execution; sharing completed results does not mean sharing a live writable repository. We can test the contract locally before changing SkyValue ownership or phase ordering.

## Proposed small experiment after M0

1. **Identify the rejection before changing semantics.** For two fresh output bases with matched configuration, record bucket hashes, candidate counts and typed rejection reasons from `areRepositoryAndMarkerFileConsistent`; capture only relevant redacted marker entries and file hashes. Distinguish predeclared mismatch, changed observed input, eligibility/force-fetch policy and duplicate concurrent publication. Compare actual tool hashes and output-tree manifests; equal binary sizes are insufficient.
2. **Separate an immutable bootstrap artifact from per-invocation state.** If path-bearing scratch configuration is the demonstrated cause, prototype a narrow repository-rule adapter: explicit SDK/tool source/module checksums, platform, semantic version and effective environment identify the tool artifact; private writable `GOCACHE`, `GOPATH` and temporary state remain invocation-owned. Test stable logical execution paths through an isolated sandbox/mapping, rather than substituting paths only in hashes. Reuse existing repository contents-cache machinery where possible.
3. **Measure fresh-worktree setup as its own workload.** Use matched 1/2/10-worktree runs with warm download and ordinary action caches but new output bases. Collect repository cache outcomes, repository subprocess CPU/wall, I/O, charged file/anonymous memory, tool hashes, unique tree content and storage extents where measurable. Compare total cohort cost and tail newcomer latency. Keep prepared/restarted results separate; require unchanged output oracles and action counts there.
4. **Require semantic and publication checks before generalizing.** Relocation with identical logical inputs should reuse the artifact; source/SDK/environment changes must miss when relevant. Test simultaneous newcomers, cancellation and interrupted publication; never expose a partial tree. Only consider shared pending-query work after completed immutable-result reuse is valid. Compare the measured saving and implementation cost with the M1 sharing ceiling before selecting a later architectural experiment.

Protocol: inspected C-backed source, previously fetched pinned Bonanza source, decision records and archived logs only. No runtime-volume reads, builds, source edits, cache changes or gate decisions were made.

[declared]: https://github.com/JSGette/bazel/blob/236a4d0b31573e125876c7b76da259a383698073/src/main/java/com/google/devtools/build/lib/bazel/repository/DigestWriter.java#L181-L222
[cache]: https://github.com/JSGette/bazel/blob/236a4d0b31573e125876c7b76da259a383698073/src/main/java/com/google/devtools/build/lib/bazel/repository/cache/LocalRepoContentsCache.java#L45-L56
[inputs]: https://github.com/JSGette/bazel/blob/236a4d0b31573e125876c7b76da259a383698073/src/main/java/com/google/devtools/build/lib/rules/repository/RepoRecordedInput.java#L335-L502
[fetch]: https://github.com/JSGette/bazel/blob/236a4d0b31573e125876c7b76da259a383698073/src/main/java/com/google/devtools/build/lib/bazel/repository/RepositoryFetchFunction.java#L259-L363
[execute]: https://github.com/JSGette/bazel/blob/236a4d0b31573e125876c7b76da259a383698073/src/main/java/com/google/devtools/build/lib/bazel/repository/starlark/StarlarkBaseExternalContext.java#L2069-L2314
[bonanza-repo]: https://github.com/buildbarn/bonanza/blob/bf9368403b95540ad07e0fd33faf5210b9d8b86b/pkg/model/analysis/repo.go#L1946-L2225
[bonanza-path]: https://github.com/buildbarn/bonanza/blob/bf9368403b95540ad07e0fd33faf5210b9d8b86b/pkg/proto/model/analysis/analysis.proto#L1980-L1991
