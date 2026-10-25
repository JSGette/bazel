// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.common.options.Converters.ByteSizeConverter;
import com.google.devtools.common.options.Converters.DurationConverter;
import com.google.devtools.common.options.Converters.RangeConverter;
import com.google.devtools.common.options.Option;
import com.google.devtools.common.options.OptionDocumentationCategory;
import com.google.devtools.common.options.OptionEffectTag;
import com.google.devtools.common.options.OptionsBase;
import com.google.devtools.common.options.OptionsClass;
import com.google.devtools.common.options.OptionsParsingException;
import java.time.Duration;

/** Options for the shared local analysis cache. */
@OptionsClass
public abstract class LocalSkycacheOptions extends OptionsBase {
  @Option(
      name = "experimental_remote_skycache",
      defaultValue = "",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      help =
          "Experimental SkyCache catalog and REAPI CAS endpoint (grpc:// or grpcs://)."
              + " Requires --experimental_local_skycache as the validated staging cache.")
  public abstract String getRemoteEndpoint();

  @Option(
      name = "experimental_remote_skycache_instance_name",
      defaultValue = "",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      help = "Instance shared by the remote SkyCache catalog and metadata CAS.")
  public abstract String getRemoteInstanceName();

  @Option(
      name = "experimental_remote_skycache_token_file",
      defaultValue = "",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      help = "Optional file containing a bearer token for the SkyCache endpoint.")
  public abstract String getRemoteTokenFile();

  @Option(
      name = "experimental_remote_skycache_timeout",
      defaultValue = "60s",
      converter = DurationConverter.class,
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      help = "Total deadline for each remote SkyCache import or export. Failures are cache misses.")
  public abstract Duration getRemoteTimeout();

  @Option(
      name = "experimental_remote_skycache_max_transfer_size",
      defaultValue = "257M",
      converter = ByteSizeConverter.class,
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      help =
          "Maximum attempted blob payload bytes per remote SkyCache read session or export."
              + " Must be positive and at most 4 GiB + 1 MiB. Independent of the 4 GiB logical"
              + " publication bound, 1 MiB chunk bound and local metadata admission budget."
              + " Failed reads consume this budget; exhaustion is a cache miss.")
  public abstract long getRemoteMaxTransferSize();

  public static final class RemoteConcurrencyConverter extends RangeConverter {
    public RemoteConcurrencyConverter() {
      super(1, 1000);
    }
  }

  @Option(
      name = "experimental_remote_skycache_max_concurrency",
      defaultValue = "100",
      converter = RemoteConcurrencyConverter.class,
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.HOST_MACHINE_RESOURCE_OPTIMIZATIONS},
      help =
          "Maximum concurrent SkyCache chunk downloads and, separately, cached range reads."
              + " Must be between 1 and 1000. RPCs still share the configured cumulative time"
              + " and attempted-byte budgets. Higher concurrency uses more temporary buffers.")
  public abstract int getRemoteMaxConcurrency();

  @Option(
      name = "experimental_remote_skycache_local_cache_size",
      defaultValue = "5G",
      converter = ByteSizeConverter.class,
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.HOST_MACHINE_RESOURCE_OPTIMIZATIONS},
      help =
          "Soft size limit for downloaded SkyCache chunks retained beneath the local SkyCache"
              + " directory. Zero disables retention. Chunks are verified before reuse; this"
              + " does not import partial publications into the local catalog. Pruning occurs"
              + " after commands; active or abandoned scratch pins can retain additional bytes.")
  public abstract long getRemoteLocalCacheSize();

  /** IdleTaskManager schedules in whole seconds; do not silently truncate a positive timeout. */
  public static final class IdleRetentionConverter extends DurationConverter {
    @Override
    public Duration convert(String input) throws OptionsParsingException {
      Duration duration = super.convert(input);
      if (duration.getNano() != 0) {
        throw new OptionsParsingException("Idle retention must be a whole number of seconds");
      }
      return duration;
    }
  }

  @Option(
      name = "experimental_analysis_cache_idle_retention",
      defaultValue = "0",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.HOST_MACHINE_RESOURCE_OPTIMIZATIONS},
      converter = IdleRetentionConverter.class,
      help =
          "Release the retained Skyframe graph after this idle duration when a successful local"
              + " SkyCache publication is available. Zero disables eviction. This prototype also"
              + " drops loading state; the next build reloads eligible analysis and recomputes"
              + " misses. Preserves the JVM, action-result cache and output files.")
  public abstract Duration getAnalysisCacheIdleRetention();

  @Option(
      name = "experimental_local_skycache",
      defaultValue = "",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      help =
          "Shared local POSIX analysis-cache directory, outside the source workspace. Enables"
              + " bidirectional analysis caching while retaining the in-memory graph. Experimental:"
              + " shares validated analysis entries across worktrees of one local Git repository.")
  public abstract String getDirectory();

  @Option(
      name = "experimental_local_skycache_max_publications",
      defaultValue = "64",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      help =
          "Maximum shared publications opened per invocation. Zero disables shared reads; private"
              + " writes remain available.")
  public abstract int getMaxPublications();

  @Option(
      name = "experimental_local_skycache_max_metadata_mb",
      defaultValue = "256",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      help =
          "Maximum combined encoded manifest and index MiB admitted per invocation. This is not a"
              + " total JVM memory limit; decoded metadata and graph objects use additional"
              + " memory.")
  public abstract int getMaxMetadataMb();

  @Option(
      name = "experimental_local_skycache_gc_max_size",
      defaultValue = "5G",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      converter = ByteSizeConverter.class,
      help =
          "Soft byte limit for the lease-aware cache population. Live readers/writers and their"
              + " parents remain protected. Legacy layouts and unrecognized files are not"
              + " collected. Zero disables the size limit.")
  public abstract long getGcMaxSize();

  @Option(
      name = "experimental_local_skycache_gc_max_age",
      defaultValue = "7d",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      converter = DurationConverter.class,
      help =
          "Maximum publication age for idle cache collection. Needed parents and active"
              + " publications remain protected. This measures publication time, not last access."
              + " Zero disables the age limit; setting both size and age to zero disables"
              + " collection.")
  public abstract Duration getGcMaxAge();

  @Option(
      name = "experimental_local_skycache_gc_idle_delay",
      defaultValue = "30s",
      documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
      effectTags = {OptionEffectTag.BAZEL_INTERNAL_CONFIGURATION},
      converter = DurationConverter.class,
      help = "How long the server must be idle before collecting its shared local SkyCache.")
  public abstract Duration getGcIdleDelay();
}
