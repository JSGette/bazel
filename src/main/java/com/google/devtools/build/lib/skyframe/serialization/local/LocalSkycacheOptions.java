// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.common.options.Converters.ByteSizeConverter;
import com.google.devtools.common.options.Converters.DurationConverter;
import com.google.devtools.common.options.Option;
import com.google.devtools.common.options.OptionDocumentationCategory;
import com.google.devtools.common.options.OptionEffectTag;
import com.google.devtools.common.options.OptionsBase;
import com.google.devtools.common.options.OptionsClass;
import java.time.Duration;

/** Options for the shared local analysis cache. */
@OptionsClass
public abstract class LocalSkycacheOptions extends OptionsBase {
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
