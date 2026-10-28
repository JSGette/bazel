# Provider identity conformance fixture (P02)

This deterministic codec fixture promotes the small synthetic reproduction reviewed for the
Revision 2 design. It uses a schemaless Starlark provider with fixed scalar fields and a nonempty
depset. It needs no Go toolchain, Datadog checkout, remote endpoint, or archived binary.

The two cross-branch tests assert the correct three-leaf result. The reviewed implementation
produced four leaves for independently restored branches and for one original plus one restored
branch. These are known failures, not approved semantics or proof of the cause of a broad-build
link failure. No production codec or eligibility policy is changed here. P02 remains incomplete.

The original+restored case models the join boundary of retained/recomputed and restored branches;
it does not execute a SkyFunction or establish that every recomputed allocation has a corresponding
restored origin. The fixture is an identity contract test, not an end-to-end Skycache build test.

## Cases

| Case | Required result |
| --- | --- |
| Original branches share one provider | Three leaves after merging the branches |
| Distinct original depset wrappers have equal contents | Both provider leaves remain distinct, including after one complete round trip |
| Two branches restore independently using shared fingerprint storage | Three merged leaves; sharing backing arrays alone is insufficient |
| One original branch joins one restored branch | Three merged leaves |

Before asserting the cross-branch count, the tests check identical provider singleton, equal scalar
fields and equal flattened depset elements. The independent-restores case also checks backing-array
sharing. The distinct-origins control prevents fixing the symptom by indiscriminately interning
equal-content wrappers.

## Run

Use a dedicated Linux/WSL checkout and output base. Build a candidate Bazel binary separately, then
from this folder run (replace paths with the chosen candidate and new result directory):

```bash
python3 run.py --bazel /tmp/skycache-plan-bazel \
  --output-base /tmp/skycache-provider-identity-tests \
  --output-dir /tmp/skycache-provider-identity-result
```

The default is a conformance check and exits nonzero while the defect remains. To explicitly
reproduce the known blocker instead:

```bash
python3 run.py --bazel /tmp/skycache-plan-bazel \
  --output-base /tmp/skycache-provider-identity-tests \
  --output-dir /tmp/skycache-provider-identity-known-failure \
  --expect-known-failure
```

This mode accepts only all four executed cases, both passing controls, and the two expected
`expected=3 actual=4` assertion signatures. Build errors, timeouts, skipped tests, prerequisite
assertion failures and unexpected test failures are errors. If a fix makes everything pass, this
mode also exits nonzero to require review and removal of the expected-failure classification.
Use repeated `--startup-arg=...` or `--bazel-arg=...` for baseline-specific configuration; the runner
forces uncached, unsharded, single-attempt execution of the complete fixture.

The new result directory contains the exact command/revision/fixture digest, build log, test XML
and log when available, and `result.json`. `known_failure_reproduced` is never labeled an eligibility
pass. Even `conformant` describes only this narrow fixture; it does not certify all providers or the
full P02 gate. No timing in this runner is a performance measurement. It does not stop other Bazel
servers, modify the source checkout, or apply archived patches.

The explicitly named target is:

```text
//src/test/java/com/google/devtools/build/lib/collect/nestedset:ProviderIdentityReproTest
```

It is tagged `manual` and excluded from the implicit `NestedSetTests` suite/wildcard runs. Existing
green codec tests are unchanged. Run the parser's failure-classification checks without Bazel:

```bash
python3 -m unittest -v test_run.py
```

The precursor was the local, untracked
`skycache-demand-experiments-2026-10-27/provider-identity/regression.patch`. Only its deterministic
test shape is promoted; diagnostic overlays, absolute-path launchers, binaries and raw workload
archives are excluded. This directory records no new Java test result until the candidate is run.
