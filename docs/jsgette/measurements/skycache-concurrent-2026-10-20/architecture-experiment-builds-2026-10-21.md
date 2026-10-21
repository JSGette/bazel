# SkyCache architecture experiments

Recorded attempts: 15/15; passed runs: 15; measured successful runs: 13.
Implementation `e29c37affc2fdcbeb42c3beefcba5368ee0ac4af`; binary SHA256 `53a3ddfdbec7b44e20d2190dbbef8bd5894af0eefc205dbb5f844d7865141b5b`.

**Audit qualification:** stage audits did not pass for e1. Passing build/output/action checks and artifact hashes do not erase those protocol failures. Comparisons involving these rows are qualified observations; inspect the JSON stage audit and amendment evidence.


Fresh server and graph for each invocation; warm output/action caches; sequential native NVMe runs, 32 jobs, 6 GiB heap and 6144 MiB action memory. Preparation builds are excluded from comparisons.

| Run | Result | Full command s | Setup s | Analysis s | Execution s | CPU s | Peak PSS GiB | Analysis hits |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| prepare-a (preparation) | passed | 20.815 | 0.001 | 5.117 | 1.500 | 137.760 | 3.659 | 0 |
| prepare-b (preparation) | passed | 19.752 | 0.001 | 5.576 | 1.293 | 135.300 | 2.631 | 0 |
| off-a | passed | 18.777 | 0.001 | 5.256 | 0.934 | 130.940 | 3.827 | 0 |
| off-b | passed | 18.764 | 0.001 | 5.105 | 1.113 | 132.920 | 3.992 | 0 |
| baseline-writer-a | passed | 29.136 | 0.432 | 8.105 | 1.151 | 218.790 | 5.345 | 0 |
| baseline-reader-b1 | passed | 21.447 | 0.464 | 5.424 | 2.809 | 106.920 | 4.172 | 4501 |
| baseline-reader-b2 | passed | 20.204 | 0.438 | 4.980 | 2.564 | 111.540 | 4.976 | 4501 |
| e1-reader-b1 | passed | 22.277 | 0.015 | 5.220 | 3.414 | 115.020 | 4.998 | 4501 |
| e1-reader-b2 | passed | 22.386 | 0.017 | 5.959 | 3.309 | 112.710 | 5.186 | 4501 |
| e2-reader-b1 | passed | 34.018 | 0.019 | 17.795 | 2.619 | 113.080 | 5.360 | 4501 |
| e2-reader-b2 | passed | 20.774 | 0.018 | 5.461 | 2.526 | 109.560 | 5.300 | 4501 |
| e4-writer-a | passed | 30.049 | 0.463 | 5.806 | 1.281 | 198.610 | 4.077 | 0 |
| e4-reader-b1 | passed | 26.301 | 0.425 | 4.921 | 5.752 | 163.360 | 3.775 | 7036 |
| e4-reader-b2 | passed | 24.149 | 0.413 | 4.893 | 6.113 | 169.500 | 3.933 | 7036 |
| final-off-b | passed | 20.809 | 0.003 | 5.515 | 1.487 | 136.090 | 3.795 | 0 |

Comparisons use arithmetic means; two reader samples show repeatability only and do not support a statistical confidence claim. Negative deltas are faster.

| Comparison | Variant / reference n | Full command Δ s | Δ % | Setup Δ s | Analysis Δ s | Execution Δ s | PSS Δ GiB |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline-writer vs off-a | 1 / 1 | 10.358 | 55.16 | 0.430 | 2.849 | 0.217 | 1.518 |
| baseline-readers vs off-b | 2 / 1 | 2.062 | 10.99 | 0.450 | 0.097 | 1.574 | 0.583 |
| e1-readers vs baseline-readers | 2 / 2 | 1.506 | 7.23 | -0.435 | 0.387 | 0.675 | 0.518 |
| e1-readers vs off-b | 2 / 1 | 3.568 | 19.01 | 0.015 | 0.484 | 2.249 | 1.100 |
| e2-readers vs e1-readers | 2 / 2 | 5.064 | 22.68 | 0.002 | 6.039 | -0.789 | 0.238 |
| e2-readers vs baseline-readers | 2 / 2 | 6.570 | 31.55 | -0.433 | 6.426 | -0.114 | 0.756 |
| e2-readers vs off-b | 2 / 1 | 8.632 | 46.00 | 0.017 | 6.523 | 1.459 | 1.339 |
| e4-writer vs baseline-writer | 1 / 1 | 0.913 | 3.13 | 0.032 | -2.299 | 0.130 | -1.268 |
| e4-writer vs off-a | 1 / 1 | 11.272 | 60.03 | 0.462 | 0.550 | 0.347 | 0.249 |
| e4-readers vs baseline-readers | 2 / 2 | 4.399 | 21.13 | -0.032 | -0.295 | 3.246 | -0.720 |
| e4-readers vs off-b | 2 / 1 | 6.461 | 34.43 | 0.418 | -0.198 | 4.819 | -0.137 |

| Reader | CAS gets | CAS value MiB | Shared reads | Proof records | Witnesses | Exported roots |
|---|---:|---:|---:|---:|---:|---:|
| baseline-reader-b1 | 312793 | 321.199 | 317294 | 163377 | 7798 | 4495 |
| baseline-reader-b2 | 312793 | 321.199 | 317294 | 163377 | 7798 | 4495 |
| e1-reader-b1 | 312793 | 321.199 | 317294 | 163377 | 7798 | 4495 |
| e1-reader-b2 | 312793 | 321.199 | 317294 | 163377 | 7798 | 4495 |
| e2-reader-b1 | 312793 | 321.199 | 317294 | 163377 | 7798 | 4495 |
| e2-reader-b2 | 312793 | 321.199 | 317294 | 163377 | 7798 | 4495 |
| e4-reader-b1 | 278693 | 140.263 | 285729 | 163381 | 7798 | 690 |
| e4-reader-b2 | 278694 | 140.263 | 285730 | 163381 | 7798 | 690 |

| Publication cache | Pack MiB | Index MiB | Manifest MiB | Total MiB | Writer-only attribution |
|---|---:|---:|---:|---:|---|
| cache-default | 391.023 | 20.311 | 25.860 | 437.194 | True |
| cache-frontier | 164.284 | 13.772 | 51.719 | 229.775 | False |

Same-worktree cache-off drift (final B versus initial B): 2.045 s (10.90%). This is a descriptive order/thermal/cache-state check, not a drift correction.

Output hashes match the registered output: True. Matched warm-action state for all measured successful rows: True.

E3 is a separate dependency-certificate mechanism experiment. Its preparation, validation rounds and negative probes do not establish full-build speed or a production-safe integration.

Limitations:

- Reader groups have two observations, writers and initial paired controls one each; order is fixed, not randomized.
- A failed stage audit remains failed even when its individual builds pass. Setup-only audit amendments must preserve original snapshots, results and journal evidence; within-stage observations then require an explicit qualification.
- Fresh server/graph, warm outputs/action cache, no changed-source or concurrent-throughput claims in this experiment set.
- A failed or incomplete group is retained explicitly; comparisons may have fewer observations than registered and must be treated as incomplete.
- Profile spans overlap/nest; setup, BEP phase deltas and full-command differences must not be added as independent contributions.
- Main repository mapping and loading remain in the pre-analysis interval; configured-target reductions alone do not imply proportional time savings.
- Full-command timing includes publication cost omitted by some BEP timings; GC pause sums omit concurrent collector work.
- Cache storage is observed after completed stages; writer-exclusive attribution requires zero subsequent writes/publications.
- Input content/mode/symlink inventories and external repository bindings are audited outside timed builds; unchanged bindings do not by themselves prove unchanged external repository contents.
- E2 includes E1 policy, so its incremental comparison is against E1; comparing E2 with default readers combines both changes.
- E4 uses normal source capture and synchronous analysis-frontier selection; compare its readers with normal default readers.
- E3 is separate validation-path evidence and does not establish complete-build performance or production integration safety.
- Raw commands, environment contents, source paths, fixture inventories and logs are private; this summary copies allowlisted values and evidence hashes only.
