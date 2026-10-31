# Multi worktree Phase 0 evidence

The [Phase 0 execution report](../../plans/multi-worktree-phase0-2026-10-31.md) records status, acceptance criteria and limitations. The [implementation plan](../../plans/multi-worktree-implementation-plan.md) is a separate document.

- `baseline-lock.json` pins the reviewed implementation source, official stock executable, checksum and host limits. The release executable is not a build of the implementation source.
- `representative-workload.json` pins the inspected DataDog source and target cohort. Its tests have not yet been run.
- `harness/` contains the deterministic fixture, Python runner and focused harness tests.
- `smoke/` preserves the failed bootstrap attempt (registry TLS failure and container zombie-reaping timeout), including the original runner matching its manifest checksum. It is excluded from build-time comparisons.
- `preflight/` preserves the failed fixture preflight after the environment fixes. Its empty optional-file glob was corrected with explicit `allow_empty = True`; this attempt is also excluded from timing comparisons.
- `smoke-verified/` contains the full worktree smoke matrix; use its manifest and summary as the execution authority.
- `smoke-observations.json` contains verified counts and per-command BEP metrics derived from the successful archive.
- `reproduction/` contains the fresh-directory, two-worktree reproduction check.
- `wsl-context-smoke/` and `wsl-context-smoke-observations.json` contain the expanded 1/2/4-worktree release-binary matrix: 253 builds including context divergence and baseline restores.
- `wsl-control-build/` contains the successful pinned-source control build log, provenance, rc, and module lock.
- `wsl-control-smoke/` and `wsl-control-smoke-observations.json` contain the fresh source-control reproduction: 169 builds across the full 1/2/4-worktree mutation/restore matrix.

## Reproduce the smoke cohort

Download `bazel-9.3.1-linux-x86_64` from the URL in `baseline-lock.json`, verify SHA-256 `7f775b54bbec487efaf0f49ccda1d2ed02cd34f582edfdd6aeeec76e39ef585c`, and make it executable. The binary embeds its own Java runtime. This fixture also requires Git, Python 3, Linux `/proc`, `/bin/sh` and `/bin/cat`.

In this managed environment, the bundled JDK needs the installed environment CA certificates. Create an experiment-local trust store without disabling TLS verification:

```sh
cp /etc/ssl/certs/java/cacerts /workspace/tools/bazel-environment-cacerts
for cert in /usr/local/share/ca-certificates/*.crt; do
  keytool -importcert -noprompt -trustcacerts \
    -keystore /workspace/tools/bazel-environment-cacerts -storepass changeit \
    -alias "phase0-$(basename "$cert")" -file "$cert"
done
```

Use a new trust-store destination when repeating this setup. `changeit` is the conventional password protecting this copy of a public CA store, not a service credential. The observed store hash is in the Phase 0 report and each successful run manifest. Other environments can omit `--java-trust-store` if the bundled JDK already trusts their HTTPS path; record that difference.

From this directory, use fresh, separate state and results directories:

```sh
python3 -m unittest discover -s harness -p 'test_*.py' -v
python3 harness/run.py \
  --bazel /workspace/tools/bazel-9.3.1 \
  --java-trust-store /workspace/tools/bazel-environment-cacerts \
  --state-dir /workspace/scratch/multi-worktree-phase0-reproduction-state \
  --results-dir /workspace/scratch/multi-worktree-phase0-reproduction-results \
  --concurrency 1,2,4 --repetitions 2
python3 harness/verify_results.py \
  /workspace/scratch/multi-worktree-phase0-reproduction-results
```

Adjust only machine-specific paths. Never point the harness at existing personal output bases or nonempty directories. The runner preserves its fixture checkout and raw results, and shuts down its own servers in a `finally` path. On Linux it becomes a child subreaper and reaps only its own identified daemon children, avoiding dependence on container PID 1 to reap exited Java processes. Its README defines the artifact schema and measurement scope.

The two repetitions per case are pipeline smoke checks. They combine a first invocation after an edit with a warm repeat and must not be pooled as a statistically qualified latency comparison. Groups share increasingly populated caches and resetting a case may restore a previous edit; these are not matched edit-cost arms. A command-overlap interval is not a claim about simultaneous CPU execution. Server-RSS samples exclude clients and action children.

The local WSL continuation adds a source-built control and synthetic context
divergence. Qualification still requires representative build/test runs, broader
context/lifecycle coverage and memory attribution, and repeated observations under
the predeclared acceptance criteria. The release-binary WSL run overlapped the
control build; its timings must not be used for a performance comparison.
