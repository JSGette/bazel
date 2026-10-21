// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.common.options.Option;
import com.google.devtools.common.options.OptionDocumentationCategory;
import com.google.devtools.common.options.OptionEffectTag;
import com.google.devtools.common.options.OptionsBase;
import com.google.devtools.common.options.OptionsClass;

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
}
