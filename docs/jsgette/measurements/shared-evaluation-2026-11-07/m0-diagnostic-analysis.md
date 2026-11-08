# M0 diagnostic evidence

Instrumented single trials; no milestone verdict or statistical claim. CPU values below are measured process/cgroup counters. Profiler samples are not converted to CPU seconds; analysis/execution timing fields remain elapsed durations.

| Arm / phase | Wall s | JVM CPU s | Scope CPU s | JVM peak PSS MiB | Observer CPU s | CPU samples |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| off / preparation | 64.968 | 30.380 | 59.641 | 614.955 | 1.852 | unprofiled |
| off / restart | 14.085 | 22.180 | 22.751 | 750.040 | 0.443 | 1849 |
| off / warm | 0.317 | 0.910 | 0.981 | 754.747 | 0.020 | 38 |
| on / preparation | 70.338 | 36.440 | 65.933 | 773.613 | 2.138 | unprofiled |
| on / restart | 15.550 | 23.380 | 23.971 | 744.858 | 0.466 | 1982 |
| on / warm | 1.388 | 2.710 | 2.851 | 755.210 | 0.076 | 209 |

## OFF value-memory evidence

Heap after forced GC was displayed as **94MB** (rounded diagnostic text). All 63,236 value-dump pairs sum to **44,281,296 estimated bytes** (42.23 MiB). 7 printed-name collision groups contain 7 excess entries; an ordinary last-wins JSON parser would lose 3,352 bytes. These are estimated unique reachable value payloads, not retained/dominator sizes or total evaluator memory.

| Skyfunction | Rows | Estimated MiB |
| --- | ---: | ---: |
| BZL_LOAD | 920 | 24.338 |
| CONFIGURED_TARGET | 14874 | 6.855 |
| PACKAGE | 183 | 2.493 |
| FILE | 16627 | 1.437 |
| SINGLE_EXTENSION_EVAL | 33 | 1.196 |
| MODULE_FILE | 280 | 1.079 |
| BUILD_CONFIGURATION | 7 | 0.758 |
| DIRECTORY_LISTING_STATE | 1985 | 0.752 |
| GLOBS | 56 | 0.733 |
| FILE_STATE | 16136 | 0.599 |

### off / restart leaf samples

Decoded 1849 CPU events, matching the JFR event summary; missing stacks 0. This validates event decoding, not a percentage of JVM CPU captured. Samples expose at most 5 decoded frames. Categories use only the leaf frame and are mutually exclusive; caller work remains unattributed.

| Leaf category | Samples | Sample % |
| --- | ---: | ---: |
| JVM runtime/native (includes JIT and GC) | 718 | 38.83 |
| JDK collections/concurrency/utilities | 309 | 16.71 |
| JDK strings/regex/numbers/crypto | 232 | 12.55 |
| Other native/system library | 101 | 5.46 |
| Bazel filesystem | 69 | 3.73 |
| Starlark | 62 | 3.35 |
| Unknown/other Java library | 59 | 3.19 |
| Bazel analysis/packages/labels | 57 | 3.08 |
| Bazel graph engine/functions | 53 | 2.87 |
| Guava/protobuf | 46 | 2.49 |
| Unknown/unresolved native or generated stub | 45 | 2.43 |
| Other JDK | 45 | 2.43 |
| Other Bazel | 26 | 1.41 |
| JDK loading/reflection/method handles | 24 | 1.30 |
| Bazel actions/execution | 3 | 0.16 |

### off / warm leaf samples

Decoded 38 CPU events, matching the JFR event summary; missing stacks 0. This validates event decoding, not a percentage of JVM CPU captured. Samples expose at most 5 decoded frames. Categories use only the leaf frame and are mutually exclusive; caller work remains unattributed.

| Leaf category | Samples | Sample % |
| --- | ---: | ---: |
| JVM runtime/native (includes JIT and GC) | 23 | 60.53 |
| Bazel graph engine/functions | 5 | 13.16 |
| Other native/system library | 5 | 13.16 |
| Unknown/other Java library | 1 | 2.63 |
| JDK collections/concurrency/utilities | 1 | 2.63 |
| Guava/protobuf | 1 | 2.63 |
| Other JDK | 1 | 2.63 |
| Unknown/unresolved native or generated stub | 1 | 2.63 |

## ON value-memory evidence

Heap after forced GC was displayed as **113MB** (rounded diagnostic text). All 50,835 value-dump pairs sum to **46,882,072 estimated bytes** (44.71 MiB). 7 printed-name collision groups contain 7 excess entries; an ordinary last-wins JSON parser would lose 3,352 bytes. These are estimated unique reachable value payloads, not retained/dominator sizes or total evaluator memory.

| Skyfunction | Rows | Estimated MiB |
| --- | ---: | ---: |
| BZL_LOAD | 863 | 23.796 |
| CONFIGURED_TARGET | 3392 | 10.384 |
| PACKAGE | 137 | 2.114 |
| FILE | 16331 | 1.402 |
| SINGLE_EXTENSION_EVAL | 33 | 1.196 |
| MODULE_FILE | 280 | 1.079 |
| DIRECTORY_LISTING_STATE | 1946 | 0.738 |
| BUILD_CONFIGURATION | 6 | 0.735 |
| GLOBS | 32 | 0.724 |
| FILE_STATE | 15878 | 0.590 |

### on / restart leaf samples

Decoded 1982 CPU events, matching the JFR event summary; missing stacks 0. This validates event decoding, not a percentage of JVM CPU captured. Samples expose at most 5 decoded frames. Categories use only the leaf frame and are mutually exclusive; caller work remains unattributed.

| Leaf category | Samples | Sample % |
| --- | ---: | ---: |
| JVM runtime/native (includes JIT and GC) | 802 | 40.46 |
| JDK strings/regex/numbers/crypto | 276 | 13.93 |
| JDK collections/concurrency/utilities | 272 | 13.72 |
| Other native/system library | 107 | 5.40 |
| Unknown/other Java library | 107 | 5.40 |
| Bazel filesystem | 74 | 3.73 |
| Unknown/unresolved native or generated stub | 63 | 3.18 |
| Starlark | 60 | 3.03 |
| Guava/protobuf | 48 | 2.42 |
| Other JDK | 44 | 2.22 |
| Bazel graph engine/functions | 33 | 1.66 |
| Bazel serialization/cache | 27 | 1.36 |
| JDK loading/reflection/method handles | 25 | 1.26 |
| Bazel analysis/packages/labels | 21 | 1.06 |
| Other Bazel | 20 | 1.01 |
| Bazel actions/execution | 3 | 0.15 |

### on / warm leaf samples

Decoded 209 CPU events, matching the JFR event summary; missing stacks 0. This validates event decoding, not a percentage of JVM CPU captured. Samples expose at most 5 decoded frames. Categories use only the leaf frame and are mutually exclusive; caller work remains unattributed.

| Leaf category | Samples | Sample % |
| --- | ---: | ---: |
| JVM runtime/native (includes JIT and GC) | 100 | 47.85 |
| Other native/system library | 36 | 17.22 |
| Unknown/other Java library | 30 | 14.35 |
| JDK collections/concurrency/utilities | 11 | 5.26 |
| Other JDK | 7 | 3.35 |
| Unknown/unresolved native or generated stub | 7 | 3.35 |
| Bazel graph engine/functions | 5 | 2.39 |
| JDK loading/reflection/method handles | 4 | 1.91 |
| JDK strings/regex/numbers/crypto | 3 | 1.44 |
| Guava/protobuf | 3 | 1.44 |
| Bazel serialization/cache | 1 | 0.48 |
| Bazel analysis/packages/labels | 1 | 0.48 |
| Other Bazel | 1 | 0.48 |

## Interpretation limits

- CPU profiling covers the command interval, not necessarily JVM startup or all process CPU. No sample-period multiplication, elapsed-to-CPU conversion, or inference that unprofiled CPU is zero.
- Leaf-frame categories are naming heuristics, not logical query ownership. JVM native frames include JIT/GC/runtime; utility/native frames may serve graph work. Thread totals are a separate view and must not be added to categories.
- Warm profiles have few samples and short windows; percentages are unstable. These diagnostic trials include observer/profiler overhead and are not replacements for the uninstrumented matrix.
- Scope CPU includes processes beyond the JVM; waited-child rusage and observer timings are not an additive process-role partition. PSS is sampled whole-JVM residency, not Java heap. Scope memory includes page cache.
- Memory analysis uses object-pairs parsing and sums every emitted row. Original raw dump is preserved in the hash-verified archive; collision groups retain every value entry in this analysis.
- Inspected checkout SkyframeMemoryDumper.java:280-351 uses one global object-identity set, excludes pre-seen builtins, traverses SkyValues, and prints diagnostic canonical names. Traversal attribution can move between rows; wrappers, graph keys/edges, JVM native memory and all roots are not a retained-memory partition.
- The heap display is rounded and occurs before the subsequent value-dump command. Value bytes and heap/PSS cannot be subtracted to infer exact missing graph overhead.
