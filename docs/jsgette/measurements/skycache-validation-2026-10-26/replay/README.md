# Validation scheduling replay

`ValidationReplay.java` calls the preserved binary's namespace, source and typed-witness
checks through method handles. `LocalContentHasher.java` is the diagnostic shared-cache
prototype compiled ahead of that binary's server JAR on the classpath. These are experimental
sources, not additional production implementations.

The baseline binary/server JAR and fixture manifest hashes are in [baseline.json](baseline.json).
The native classpath and four fixture arguments are in [command.json](command.json). Compile
both Java sources with the recorded JDK 25 and the baseline server JAR:

```sh
javac -proc:none -cp "$BASELINE_SERVER_JAR" -d classes LocalContentHasher.java ValidationReplay.java
java -Xmx6g -cp "classes:$BASELINE_SERVER_JAR" \
  com.google.devtools.build.lib.skyframe.serialization.local.ValidationReplay \
  "$MANIFEST" "$OBJECT_ROOT" "$FIXTURE_WORKSPACE" "$FIXTURE_OUTPUT_BASE"
```

The recorded private fixture and preserved binary are required; this is not a self-contained
benchmark download. The JAR was extracted from the preserved Bazel binary. The production
validator must accept the fixture before replay begins. One serial warmup is excluded; the
four variants then execute three times in a rotated order. Each trial requires fresh namespace,
source and all registered witness checks to succeed.

[results.jsonl](results.jsonl) contains raw output; [summary.json](summary.json) contains medians
and byte totals. `files_hashed`, `bytes_hashed` and `digest_hits` sum disjoint worker counters.
**`retained_digests` sums snapshots of cache sizes. For shared-cache variants it counts the same
map repeatedly and is not an occupancy or memory measurement.** The production map remains
bounded at 256 entries, checked separately by unit tests. `batch_seconds` includes the source
capture as its final item in parallel variants. Method-handle dispatch, warmed files and the
absence of a live Bazel workload limit this replay; use the separate full-command comparison
for the promotion decision.
