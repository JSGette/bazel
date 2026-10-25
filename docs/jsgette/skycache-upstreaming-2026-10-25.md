# SkyCache work that can be separated for upstream Bazel

**Start with three independent fixes: monotonic store timing, cleanup of evicted
retrieval continuations, and symlinks to remote inputs with absent parent directories.**
The first is one existing commit, the second is an ordered pair of commits, and the
third is a two-file extraction with unchanged code. None requires the new local
SkyCache backend or the remote protocol.

Reviewed on 25 October 2026: fork
[`0f8d49f6c611`](https://github.com/JSGette/bazel/commit/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee)
against upstream
[`a5c50fcb6584`](https://github.com/bazelbuild/bazel/commit/a5c50fcb65841b505ec72840d83381a704e70afa),
verified as upstream master that day. This audits the fork's accumulated work, not
only the latest remote-cache commits. See the [final protocol proposal](skycache-remote-cache-protocol-2026-10-25.md)
for the architectural work that remains separate.

Here **“as is” means unchanged production code and existing tests, extracted at the
stated boundary, with clean application to that upstream revision**. It does not
mean upstream approval, an already-open PR, or an upstream test run. The patch audit
uses temporary Git indexes; no upstream checkout or source files were modified.
Existing fork test results do not replace running each extracted change against
upstream before submission.

## First submissions: complete independent fixes

| Order | Problem and resulting behavior | Exact extraction | Evidence and remaining gate |
| --- | --- | --- | --- |
| 1 | Wall-clock adjustment can distort store latency. Measure elapsed nanoseconds through asynchronous completion. | Entire [`478adf722d`](https://github.com/JSGette/bazel/commit/478adf722d2b50151177cf974ccf00563b2c2d7d) | Clean-applies alone. Includes regression for async completion and signed clock wrap. Run upstream serialization suite. |
| 2 | Eviction can leave pending lookups alive, including states created after eviction. Abandon continuations when available and close late states without invoking callbacks under the lock. | Entire [`9f3aff359f`](https://github.com/JSGette/bazel/commit/9f3aff359f26d36f04d7d8d750f2fff6d7252aa3), then [`4bfb5a1c8c`](https://github.com/JSGette/bazel/commit/4bfb5a1c8cb8a0a432da406553458518fd83b492) | Clean-apply in order; second is not standalone. Includes pending/immediate and late-state regression cases. Run upstream serialization suite. |
| 3 | A known remote input may have no physical parent directories; symlink resolution must still see its metadata. Create the target parents only in the in-memory remote tree. | From [`71521fabb3`](https://github.com/JSGette/bazel/commit/71521fabb3420707c39d73329fa9aa9622e8c382): only `RemoteActionFileSystem.java` and `RemoteActionFileSystemTest.java` | Clean-applies alone. Regression checks file size/type with no disk directory creation and no prefetch. Run upstream remote filesystem tests. |

Exact paths:

```text
1  src/main/java/com/google/devtools/build/lib/skyframe/serialization/FingerprintValueService.java
   src/test/java/com/google/devtools/build/lib/skyframe/serialization/FingerprintValueServiceTest.java
   src/test/java/com/google/devtools/build/lib/skyframe/serialization/BUILD

2  src/main/java/com/google/devtools/build/lib/skyframe/serialization/SkyValueRetriever.java
   src/test/java/com/google/devtools/build/lib/skyframe/serialization/SkyValueRetrieverTest.java

3  src/main/java/com/google/devtools/build/lib/remote/RemoteActionFileSystem.java
   src/test/java/com/google/devtools/build/lib/remote/RemoteActionFileSystemTest.java
```

These are three review units, not one combined SkyCache PR. The two cleanup commits
belong together because they cover the same lifecycle and the second's test context
depends on the first. The remote filesystem extraction must exclude execution-value
restoration and all other changes from its much larger source commit. Its existing
comment mentions the SkyCache trigger, but the implementation only uses upstream
remote-filesystem APIs.

Regression entry points already present:

- `FingerprintValueServiceTest.latencyUsesElapsedNanosThroughAsynchronousCompletionAndSignedWrap`.
- `SkyValueRetrieverTest` tests added by both cleanup commits, exercising abandonment
  after close and retrieval state creation after close.
- `RemoteActionFileSystemTest.symlinkToRemoteInputWithMissingParentsDoesNotMaterializeTarget`.

The first two live in
`//src/test/java/com/google/devtools/build/lib/skyframe/serialization:SerializationTests`;
the filesystem test lives in
`//src/test/java/com/google/devtools/build/lib/remote:RemoteTests`.
Use a separate output base for upstream validation. The wider remote suite should
also cover relative symlinks, dangling links and non-output paths before submission.

## Next: reusable code with additional evidence needed

| Candidate | Unchanged extraction | Why it is not in the first group |
| --- | --- | --- |
| Array-backed decoding of already-uncompressed shared values | Only `src/main/java/com/google/devtools/build/lib/skyframe/serialization/SharedValueDeserializationContext.java` from [`36a73d8fb4`](https://github.com/JSGette/bazel/commit/36a73d8fb4f13c315002d74f2c73532a2db67cdd) | Clean-applies and leaves the wire format unchanged. Existing tests cover ordinary/deferred, compressed/uncompressed and chunked values, but the commit adds no focused regression or isolated allocation measurement. Add that evidence before presenting a performance claim. |
| Missing worker bearer token | Only the `actualToken.get()` → `actualToken.orElse(null)` hunk in `src/tools/remote/src/main/java/com/google/devtools/build/remote/worker/RemoteWorker.java`, from [`afff0b2bf4`](https://github.com/JSGette/bazel/commit/afff0b2bf4) | Clean-applies and routes absent/malformed headers through existing `PERMISSION_DENIED` handling. Add a dedicated missing/wrong/valid-token test; none was introduced with this hunk. |
| Optional worker bind address | The server-builder bind-address hunk in that `RemoteWorker.java`, and the `listen_address` option hunk in `RemoteWorkerOptions.java`, from `afff0b2bf4` | Clean-apply; no SkyCache dependency. New user-visible feature rather than a bug fix. Add default/explicit-bind tests and agree the option with maintainers. |

Do not cherry-pick all of `36a73d8fb4` or `afff0b2bf4`. Their other changes introduce
our packed-store behavior and the remote SkyCache subsystem respectively. The array
path saves a stream decoder's intermediate buffer when bytes are already available;
compressed and chunked values retain streaming. The aggregate remote benchmark
cannot isolate that optimization's benefit.

## Independent correctness issue, but a codec decision

[`ef40333376`](https://github.com/JSGette/bazel/commit/ef40333376) preserves depset
element types across provider deserialization when the receiving provider has no
prediction or predicts a different type. The following four-file extraction
clean-applies and has no local SkyCache dependency:

```text
src/main/java/com/google/devtools/build/lib/packages/StarlarkInfoWithSchema.java
src/main/java/com/google/devtools/build/lib/packages/BUILD
src/test/java/com/google/devtools/build/lib/skyframe/serialization/StarlarkInfoCodecTest.java
src/test/java/com/google/devtools/build/lib/skyframe/serialization/BUILD
```

Exclude the unrelated Datadog probe change. Existing tests cover multiple schema
sizes, conflicting receiver predictions, absent/scalar/None/empty values. The change
registers a global codec for the schema subclasses and alters serialized values.
Install-checksum isolation in upstream analysis caching helps separate versions,
but is not proof of mixed-reader compatibility. This deserves its own correctness
submission with an explicit codec/version-compatibility explanation and
`//src/test/java/com/google/devtools/build/lib/skyframe/serialization:StarlarkInfoCodecTest`.
It is not an unconditional compatibility-safe cherry-pick.

## Work that needs the SkyCache foundation or design review

| Work | Why it cannot stand alone unchanged |
| --- | --- |
| Optional transitive-package codecs in `AspectValue` and `RemoteConfiguredTargetValue` | Selected by `LocalSkycacheModule`; require agreement on local restoration and package metadata semantics. |
| `LocalRepositoryMappingCodec` | Custom subtype/magic format with `autoRegister=false`; only enabled by the local module. Requires registry and format integration. |
| `RunfilesOrigin`, Runfiles identity/interner changes, related `ConfiguredTargetFactory` and `StarlarkRuleContext` changes | A coupled identity design across independent writers; individual builder hunks do not carry its invariants. |
| Execution owner restoration in `ActionExecutionFunction` | Depends on new execution-value retrieval, admission, output handling and rewind/stale-candidate exclusion. |
| Independent file evidence, `FileDependencySerializer`, `LongVersionGetter`, invalidator/upload/retrieval policy hooks | Changes the validation/lifetime model; needs the evidence provider and local backend, not just interface additions. |
| Concurrent/packed/segmented stores, leases, GC and idle graph retention | New storage/lifecycle subsystem with a POSIX contract and caller integration. The concurrent map/file primitive alone has no upstream production caller or complete retention policy. |
| Remote proto, transport, catalog/worker service, parent descriptors and retained chunks | Depend on the local publication format, namespace, dependency validation and trusted producer model. Require protocol and backend design review. |
| Benchmark/probe infrastructure under `docs/jsgette` and custom harnesses | Supporting fork evidence, not upstream runtime changes or portable upstream tests. |

The latest bucket-lock reduction, concurrency default of 100 and persistent downloaded
chunk cache are useful implementation improvements, but they modify these new stores
and client classes. They are not independent patches to vanilla Bazel.

For the architectural work, propose review boundaries in this order:

1. Local immutable publication format, evidence model, namespaces and codec
   compatibility, with reader/writer/GC lifetime guarantees.
2. Integration into analysis/execution reuse, per-entry source validation and
   missing-output rewind behavior.
3. Remote catalog and REAPI profile, followed by client transport and the reference
   backend. Keep production authentication/retention and cross-clone portability
   explicitly scoped; current v1 does not claim them.
4. Performance changes within that accepted design, backed by focused measurements.

This ordering is a review plan, not a claim that the large existing commits can be
cherry-picked independently.

## Reproducible extraction audit

The [audit record](upstreaming/2026-10-25/audit.json) pins full source commits, the
upstream base, selected paths/hunks and patch application results. The adjacent
[patch files](upstreaming/2026-10-25/) preserve the extracted diffs unchanged; they
are review artifacts, not submitted PRs. The audit seeds a disposable `GIT_INDEX_FILE`
with `git read-tree` at upstream, checks with `git apply --cached --check`, and applies
the cleanup pair in order only inside that disposable index. It never modifies the
real index or working tree. The second cleanup commit fails when checked alone and
passes after its prerequisite.

The included [verification script](upstreaming/2026-10-25/verify.py) independently
reconstructs all seven extracts from their recorded source commits, checks identical
patch bytes and hashes, and repeats their upstream application checks. It also checks
that the real Git index remains unchanged:

```sh
python3 docs/jsgette/upstreaming/2026-10-25/verify.py /path/to/bazel-fork
```

All seven source-byte, hash and application checks passed during finalization. The
repository supplied to this script must contain the recorded source and upstream
commits; the documentation-only branch need not contain those objects.

No new builds, performance runs or upstream test executions were done for this
documentation/extraction audit. The [existing fork validation](https://github.com/JSGette/bazel/tree/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee/docs/jsgette/measurements/skycache-retained-chunks-agent-2026-10-25)
is supporting evidence, not a substitute for extraction-specific upstream tests.

Before any submission, verify the base is still current, run each affected suite on
its extracted tree, and provide a small problem/behavior/test description. Bazel's
[patch acceptance process](https://bazel.build/contribute/patch-acceptance) calls for
a tracking issue for behavior changes and tests/documentation appropriate to the
change. The broader subsystem needs the
[design review process](https://bazel.build/contribute/design-documents). A contributor
must understand and own generated changes under the
[AI-assisted contribution policy](https://bazel.build/contribute/ai-assisted-contributions).
No upstream issue, design announcement or PR was opened as part of this identification work.
