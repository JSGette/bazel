package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheTransport;
import java.nio.file.Path;
import java.time.Duration;

/** Diagnostic fixture preparation through the production authenticated importer. */
public final class HydrateSkycacheSeed {
  public static void main(String[] args) throws Exception {
    var transport = new SkyCacheTransport(args[2], "datadog-bench", "", Duration.ofSeconds(600), 2L*1024*1024*1024, 100);
    try (var remote = new RemoteSkyCache(Path.of(args[0]), args[1], 512L*1024*1024, "datadog-bench", transport)) {
      int count=remote.hydrate();
      if(count!=2) throw new AssertionError("Expected the exact two-publication seed: "+count);
      System.out.println(remote.statisticsJson());
    }
  }
}
