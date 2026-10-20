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
          "Shared local POSIX analysis-cache directory, outside the source workspace. "
              + "Enables bidirectional analysis caching while retaining the in-memory graph. "
              + "Experimental: validates complete project snapshots and observed external inputs.")
  public abstract String getDirectory();
}
