# Java CPU and heap profile comparison

These diagnostic recordings are separate from the timing runs. They include forced garbage collections from Bazel memory profiling and JFR startup. CPU events are samples, not exact method durations; allocation weights are estimates. Rust CPU stacks and Windows host/WSL-VM memory are not profiled. Process memory measurements do include the Rust process.

## Analysis

### Vanilla

Execution samples: 758; samples with a tier/client stack: 0. Allocation samples: 1323. Diagnostic GC pause sum: 0.664 s (not a normal-build GC measurement).

| Sampled leaf method | Samples |
|---|---:|
| com.google.common.collect.RegularImmutableMap.get | 32 |
| java.util.concurrent.ConcurrentHashMap.get | 18 |
| java.util.concurrent.ForkJoinPool.runWorker | 15 |
| java.util.concurrent.ForkJoinPool.deactivate | 13 |
| java.util.HashMap.putVal | 12 |
| net.starlark.java.eval.Eval.eval | 12 |

| Allocation class | Estimated sampled weight, MiB |
|---|---:|
| [Ljava.lang.Object; | 731.5 |
| [B | 396.6 |
| com.google.devtools.build.lib.collect.nestedset.NestedSetBuilder$DefaultNestedSetBuilder | 115.1 |
| java.lang.String | 96.9 |
| java.util.LinkedHashMap$Entry | 85.5 |

### Modified, tier off

Execution samples: 818; samples with a tier/client stack: 0. Allocation samples: 1388. Diagnostic GC pause sum: 0.673 s (not a normal-build GC measurement).

| Sampled leaf method | Samples |
|---|---:|
| com.google.common.collect.RegularImmutableMap.get | 26 |
| java.util.concurrent.ConcurrentHashMap.get | 18 |
| net.starlark.java.eval.Eval.eval | 16 |
| com.google.devtools.build.lib.skyframe.SkyframeExecutor$TransitiveActionLookupKeysCollector$VisitActionLookupKey.compute | 16 |
| java.util.concurrent.ForkJoinPool.runWorker | 12 |
| net.starlark.java.eval.Starlark.positionalOnlyCall | 12 |

| Allocation class | Estimated sampled weight, MiB |
|---|---:|
| [Ljava.lang.Object; | 519.7 |
| [B | 453.8 |
| java.util.LinkedHashMap$Entry | 146.7 |
| net.starlark.java.eval.BuiltinFunction$ArgumentProcessor | 144.7 |
| java.lang.String | 134.2 |

### Rust tiered_idle

Execution samples: 823; samples with a tier/client stack: 3. Allocation samples: 1298. Diagnostic GC pause sum: 0.678 s (not a normal-build GC measurement).

| Sampled leaf method | Samples |
|---|---:|
| net.starlark.java.eval.Eval.eval | 26 |
| com.google.common.collect.RegularImmutableMap.get | 26 |
| java.util.concurrent.ConcurrentHashMap.get | 24 |
| java.util.concurrent.ForkJoinPool.deactivate | 18 |
| java.util.HashMap.getNode | 18 |
| java.util.HashMap.putVal | 15 |

| Allocation class | Estimated sampled weight, MiB |
|---|---:|
| [Ljava.lang.Object; | 620.9 |
| [B | 268.1 |
| [Ljava.util.HashMap$Node; | 226.8 |
| java.util.LinkedHashMap | 157.0 |
| java.lang.String | 118.1 |

## Full Build

### Vanilla

Execution samples: 744; samples with a tier/client stack: 0. Allocation samples: 2690. Diagnostic GC pause sum: 0.307 s (not a normal-build GC measurement).

| Sampled leaf method | Samples |
|---|---:|
| java.util.concurrent.ConcurrentHashMap.get | 39 |
| com.google.devtools.build.lib.vfs.DigestUtils.toHexByteString | 28 |
| com.google.devtools.build.lib.vfs.FileSystem.getDigest | 24 |
| java.util.Comparator.lambda$comparing$ea9a8b3a$1 | 14 |
| sun.security.provider.ByteArrayAccess.i2bBig | 13 |
| java.lang.String.charAt | 11 |

| Allocation class | Estimated sampled weight, MiB |
|---|---:|
| [B | 1606.4 |
| [Ljava.lang.Object; | 377.4 |
| java.lang.String | 271.8 |
| [I | 190.8 |
| [J | 99.2 |

### Modified, tier off

Execution samples: 776; samples with a tier/client stack: 0. Allocation samples: 2606. Diagnostic GC pause sum: 0.282 s (not a normal-build GC measurement).

| Sampled leaf method | Samples |
|---|---:|
| com.google.devtools.build.lib.vfs.DigestUtils.toHexByteString | 29 |
| java.util.concurrent.ConcurrentHashMap.get | 24 |
| com.google.common.collect.RegularImmutableMap.get | 21 |
| com.google.devtools.build.lib.vfs.FileSystem.getDigest | 18 |
| sun.security.provider.ByteArrayAccess.i2bBig | 18 |
| com.google.common.hash.MessageDigestHashFunction$MessageDigestHasher.update | 12 |

| Allocation class | Estimated sampled weight, MiB |
|---|---:|
| [B | 1703.5 |
| [Ljava.lang.Object; | 473.5 |
| java.lang.String | 234.7 |
| com.google.devtools.build.skyframe.GroupedDeps | 132.8 |
| com.google.devtools.build.skyframe.SkyFunctionEnvironment | 122.2 |

### Rust tiered_idle

Execution samples: 737; samples with a tier/client stack: 3. Allocation samples: 2689. Diagnostic GC pause sum: 0.269 s (not a normal-build GC measurement).

| Sampled leaf method | Samples |
|---|---:|
| java.util.concurrent.ConcurrentHashMap.get | 40 |
| com.google.devtools.build.lib.vfs.FileSystem.getDigest | 27 |
| com.google.devtools.build.lib.vfs.DigestUtils.toHexByteString | 17 |
| com.google.common.collect.RegularImmutableMap.get | 14 |
| sun.security.provider.ByteArrayAccess.i2bBig | 13 |
| java.lang.String.charAt | 11 |

| Allocation class | Estimated sampled weight, MiB |
|---|---:|
| [B | 1679.0 |
| [Ljava.lang.Object; | 478.8 |
| java.lang.String | 217.9 |
| [I | 146.1 |
| [J | 115.4 |

## Interpretation

Across the analysis recordings, existing Starlark evaluation and map/graph operations remain prominent. Only three of 823 tiered execution samples contain the new tier/client stack. That is too sparse to derive a trustworthy tier CPU percentage. The explicit maintenance counters are more useful for these short operations: median restore 7.92 ms and spill 10.41 ms per warm analysis command.

Live-heap histograms are taken after the profile recording, while tiered values are detached. The later Skyframe dumps run a normal command, which restores values first; therefore those dumps describe the resident graph. Per-function memory attribution assigns shared objects to one traversal owner and is not a precise independently retained-size measure.

JFR recordings, Bazel trace JSON, Bazel memory profiles, histograms, per-function memory dumps, sample CSVs, run logs and environment metadata are included in skyframe-profiles.zip. The first full default run is cache priming and is excluded from the comparison.
