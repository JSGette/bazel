# Skycache upstream sync handoff

Bring the local Skycache implementation up to current upstream Bazel while preserving validated worktree sharing, execution metadata reuse, and segmented storage. The architecture matches the community direction; the priority is upstream correctness and serialization changes.

## Starting points

- Implementation: `jsgette/skycache-v2-local`, inspected at `d4971808785aaa3cd46bf6dd1419cb65afb4319e`; local checkout `/home/jgett/REPOS/bazel-skycache-v2`.
- Documentation: `JSGette/bazel`, `jsgette/master`, checkout `/home/jgett/REPOS/bazel-skyframe-findings`. This branch carries docs and patches; use the implementation branch for code changes.
- Upstream reviewed: `bazelbuild/bazel` master at `a5c50fcb65841b505ec72840d83381a704e70afa` (24 October 2026). Fetch fresh refs and record the chosen integration baseline; other work may have advanced the branches. Preserve existing work and use an isolated integration worktree.
- Context: [discussion #31784](https://github.com/bazelbuild/bazel/discussions/31784), [presentation](https://gist.github.com/jin/e66f7496ba84f4f8d7f6f047aad64f3d), [session notes](https://gist.github.com/oquenchil/1da2b1993a68501d37a2bade6f4be8e5).

## Integrate in priority order

1. **Test configuration correctness:** [eff60286834c](https://github.com/bazelbuild/bazel/commit/eff60286834cde199f3f0d4850fcbd03993e8e88) fixes identical serialized keys across different test flags, including baseline/output-directory effects. The inspected codec/provider still have the old behavior; the local environment namespace does not cover this gap. Include the upstream regression tests. The missing fix was confirmed by inspection; the failure was not reproduced in this review.
2. **Selection behavior:** [18dcf7f4e1f5](https://github.com/bazelbuild/bazel/commit/18dcf7f4e1f5dc52e31066ad11352a5b149a35cc) removes active-directory upload selection and makes `--experimental_active_directories` ineffective. Our default `async_bidi` uploader already does not select uploads through that matcher. Correct the claim in `skycache-architecture-2026-10-25.md`; preserve the distinction between read-time top-down pruning and experimental selective publication (E4).
3. **Invalidation serialization:** reconcile the complete upstream dependency chain, including dense leaf IDs (`2e5cd1a6fd3f`), depth ordering (`93e7f0afc815`), [redundant-edge reduction](https://github.com/bazelbuild/bazel/commit/0069ff76788e1b77555197c9342ab349176a5bd1), [bitmap reclamation](https://github.com/bazelbuild/bazel/commit/597065fcd2815d31d62a979b874efdb986d174d9), phase metrics (`a1ec46d7f7bb`), and the `UploadLayout` refactor (`2084c45a3d7a`). Reconcile intervening changes rather than cherry-picking this list blindly. Async uploads use lazy leaf IDs and disable early bitmap reclamation because graph in-degrees are still changing. Our earlier bitmap validation microbenchmark does not evaluate this producer-side reduction.

## Preserve and distinguish

- Preserve local codecs/path relocation, per-entry dependency checks, conservative external/repository guards, final input revalidation, leases/parent closure/GC, bounded resource handling, and miss fallback.
- Preserve portable execution metadata, lazy CAS downloads/rewinding, `minimal`/`toplevel` sharing, and `--noremote_accept_cached` behavior.
- Current code already has compact witnesses and segmented packs. The [latest large-tree report](skycache-large-tree-2026-10-25.md) confirms both 220k/237k scopes publish; old capacity failures are historical. Performance remains mixed, especially minimal-mode latency.
- Eager external-witness admission and broad source capture remain. Selective admission, explicit reader/primer policies, and the [proposed remote protocol](skycache-remote-cache-protocol-2026-10-25.md) are follow-up work. No standardized Skycache wire protocol was established by the discussion. Keep graph-paging prototypes separate.

## Completion checks

Build `//src:bazel-dev`; run relevant configuration codec/provider, serialization, and local Skycache tests. Add/run cross-build test-option cases (`test_filter`, `run_under`, concurrent-test flags and transitions). Use a copied binary and isolated output bases for clean/restart hits, input mutation, worktree concurrency, missing-CAS rewind, and both download policies. Verify output digests and cache traffic. Compare writer memory and end-to-end latency before claiming a gain from upstream proof reduction. Retain format/version isolation when serialized bytes change.

Deliver the integration commit, pinned upstream SHA, test/benchmark results, and updated architecture notes with remaining gaps explicit.
