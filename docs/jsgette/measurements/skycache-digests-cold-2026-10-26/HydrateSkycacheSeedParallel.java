package com.google.devtools.build.lib.skyframe.serialization.local;

import build.bazel.remote.execution.v2.Digest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheTransport;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

/** Benchmark-only eager prefetch; production hydration still verifies and installs every file. */
public final class HydrateSkycacheSeedParallel {
  public static void main(String[] args) throws Exception {
    var transport = new SkyCacheTransport(args[2], "datadog-bench", "", Duration.ofSeconds(600),
        2L * 1024 * 1024 * 1024, 100);
    try (var remote = new RemoteSkyCache(Path.of(args[0]), args[1], 512L * 1024 * 1024,
        "datadog-bench", transport)) {
      long start = System.nanoTime();
      if (remote.prepare() != 2) throw new AssertionError("Expected two publications");
      var chunks = new LinkedHashSet<Digest>();
      for (var candidate : remote.candidates()) {
        for (var file : candidate.publication().getFilesList()) chunks.addAll(file.getChunksList());
      }
      // Invoke the existing authenticated downloader, outside hydrate's exclusive lifecycle lock.
      // Futures retain no downloaded byte arrays; memory stays bounded by 16 concurrent calls.
      var download = RemoteSkyCache.class.getDeclaredMethod("download", Digest.class);
      download.setAccessible(true);
      try (var workers = Executors.newFixedThreadPool(16)) {
        var futures = new ArrayList<Future<?>>();
        for (var chunk : chunks) {
          futures.add(workers.submit(() -> { download.invoke(remote, chunk); return null; }));
        }
        for (var future : futures) future.get();
      }
      double prefetched = (System.nanoTime() - start) / 1e9;
      if (remote.hydrate() != 2) throw new AssertionError("Expected two publications");
      System.err.println("PREFETCH_SECONDS=" + prefetched);
      System.out.println(remote.statisticsJson());
    }
  }
}
