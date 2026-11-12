# Existing N1 trace families

Analysis: 2026-11-12. No new build, trace, cross-engine replay or gate verdict.

This summary streams the ten already verified trace archives, checks their exact byte identities and reconciles every function/state subtotal with the earlier hierarchy-validated replay. The three focused helper tests passed, followed by independent code and result review. [Complete data](m1-trace-family-coverage.json), [helper and tests](evidence/m1-trace-family-analysis/), [N1 overhead assessment](m1-trace-n1-diagnostics.md).

## Restarted requests: captured COMPUTE CPU

These are exclusive CPU seconds inside traced function intervals, including any instrumentation within those intervals. They are neither trace-off costs nor avoided CPU. All five pairs are included.

| Function | Pair 1 | Pair 2 | Pair 3 | Pair 4 | Pair 5 | Median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CONFIGURED_TARGET | 1.631444 | 1.669998 | 1.691355 | 1.693715 | 1.679253 | 1.679253 |
| MODULE_FILE | 1.310847 | 1.254915 | 1.147743 | 1.288062 | 1.176486 | 1.254915 |
| BZL_LOAD | 0.590747 | 0.565498 | 0.592760 | 0.543968 | 0.560326 | 0.565498 |
| ARTIFACT | 0.284927 | 0.290914 | 0.307096 | 0.286342 | 0.293420 | 0.290914 |
| FILE | 0.300445 | 0.289388 | 0.290312 | 0.294774 | 0.286473 | 0.290312 |
| PACKAGE | 0.284599 | 0.270876 | 0.292411 | 0.274673 | 0.280635 | 0.280635 |
| FILE_STATE | 0.278188 | 0.268662 | 0.273351 | 0.273400 | 0.262829 | 0.273351 |
| REPO_SPEC | 0.086610 | 0.071452 | 0.096902 | 0.108253 | 0.095631 | 0.095631 |
| ACTION_EXECUTION | 0.070387 | 0.071438 | 0.069067 | 0.067692 | 0.068761 | 0.069067 |
| PACKAGE_LOOKUP | 0.063202 | 0.060723 | 0.059085 | 0.062304 | 0.054712 | 0.060723 |

Configured-target work ranks first in each restarted trace, but its key/value semantics remain unsupported. Module loading and Bzl loading are also unsupported. A function name identifies a diagnostic family, not an equality or reuse contract.

## All captured states

| Lifecycle | Pair | COMPUTE, s | Other captured states, s | All captured states, s |
| --- | ---: | ---: | ---: | ---: |
| restart | 1 | 5.342068 | 1.828740 | 7.170808 |
| warm | 1 | 0.019725 | 0.002317 | 0.022043 |
| restart | 2 | 5.247522 | 1.672073 | 6.919595 |
| warm | 2 | 0.019535 | 0.002569 | 0.022104 |
| restart | 3 | 5.273600 | 1.766611 | 7.040211 |
| warm | 3 | 0.019543 | 0.002349 | 0.021892 |
| restart | 4 | 5.336610 | 1.667555 | 7.004166 |
| warm | 4 | 0.019657 | 0.002469 | 0.022127 |
| restart | 5 | 5.196740 | 1.717672 | 6.914411 |
| warm | 5 | 0.019398 | 0.002506 | 0.021904 |

Known clocks do not mean complete command attribution. Warm requests record just two COMPUTE intervals (BUILD_DRIVER and ACTION_EXECUTION); fifteen other observed nodes have no COMPUTE interval. Retained-only nodes have no current-command CPU credit. Setup, asynchronous work, JVM work and cache finalization remain outside this state ledger or lack distinct attribution.

## Semantic and memory limits

The exact supported projections in these ten traces are only main-root directory and nonexistent FILE_STATE observations: 811 observations per restarted trace and 269 per warm trace. No digest-backed regular-file, symlink or directory-listing projections occur in this workload. The smoke demonstrated some of those types separately; its coverage must not be imported here.

The combined unattributed cost remains the largest unresolved question. Do not subtract these state totals from trace-off request CPU: they come from perturbed executions and omit different work. No individual missing family is established as dominant. The trace also provides no retained-memory ownership partition; projection sizes cannot fill it.

Keep all unsupported CPU and memory potentially removable in an optimistic model. Neither inexpensive supported leaves nor expensive unsupported names prove an architectural pass or failure. The next decision must resolve the bounded residual question or declare M1 inconclusive, as required by the frozen protocol.
